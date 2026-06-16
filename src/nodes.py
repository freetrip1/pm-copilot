"""M4 主链路的 LangGraph 节点实现（扇出终审 + 验收回路）。

每个节点签名为 def name(state: ProjectState) -> dict，只返回它写入的字段，
做增量更新（ProjectState total=False）。并行节点各写独立 key，无冲突。

新架构（替换 M1 的 dispatch/单 evaluate 循环）：
  prd_draft →(扇出 critic_pm/dev/qa)→ converge
  converge →(扇出 eval_pm/dev/qa)→ eval_gate →(route_eval) prd_draft / breakdown
  breakdown → prototype（planner→并行造 part→merge）
  prototype →(扇出 accept_pm/dev/qa)→ accept_gate →(route_accept) prototype / END
"""
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
from typing import Any

from .llm import chat
from .prompts import (
    SYS_STRUCTURE,
    SYS_QUERYGEN,
    SYS_RESEARCH,
    SYS_PRD,
    SYS_FLOWCHART,
    SYS_CRITIC_PM,
    SYS_CRITIC_DEV,
    SYS_CRITIC_QA,
    SYS_CONVERGE,
    SYS_EVAL_VOTE,
    SYS_BREAKDOWN,
    SYS_PROTO_PLAN,
    SYS_ACCEPT_VOTE,
)
from .state import ProjectState

# ===== 循环上限与并行度常量 =====
PRD_MAX_ITERS = int(os.getenv("PM_PRD_MAX_ITERS", "3"))    # 五b→prd_draft 回路上限
PROTO_MAX_ITERS = int(os.getenv("PM_PROTO_MAX_ITERS", "1"))  # 九→八 回路上限（原型每轮调 claude 极贵，默认仅 1 次返工）
PROTO_PARTS = 2                                             # 原型并行模块数上限

# ===== 三视角文案（注入终审/验收的"评审视角"）=====
LENS_PM = "资深产品视角：目标/指标/优先级/范围"
LENS_DEV = "技术视角：边界/依赖/可行性/接口"
LENS_QA = "质量视角：异常流/可测性/验收口径"

# ===== 异构 CLI 评委路由：lens → CLI（env 可覆盖）=====
# 生成侧仍用 GLM（chat()），仅 9 个"评委"节点改走对应 CLI 裁决，失败回退 GLM。
JUDGE_CLI = {
    "pm": os.getenv("PM_JUDGE_PM", "claude"),
    "dev": os.getenv("PM_JUDGE_DEV", "gemini"),
    "qa": os.getenv("PM_JUDGE_QA", "codex"),
}


# ---------------------------------------------------------------------------
# 容错 JSON 解析 helper
# ---------------------------------------------------------------------------
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json(text: str, default: Any) -> Any:
    """容错解析模型输出的 JSON。

    - 先剥离 ```json ... ``` 围栏（以及裸 ``` 围栏）。
    - 直接 json.loads；失败再尝试从文本中截取第一个 {..} 或 [..] 片段。
    - 仍失败则返回 default，绝不抛异常中断链路。
    """
    if not isinstance(text, str):
        return default
    cleaned = _FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    # 退一步：抓取第一个对象或数组片段
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return default


# ---------------------------------------------------------------------------
# ① 需求结构化
# ---------------------------------------------------------------------------
def structure(state: ProjectState) -> dict:
    print("[① 需求结构化] 正在把原始需求拆解为结构化要素 ...")
    raw = state.get("raw_input", "")
    out = chat(SYS_STRUCTURE, raw)
    default = {
        "target_user": "",
        "pain_points": [],
        "scenarios": [],
        "value_hypotheses": [],
        "open_questions": [],
    }
    structured = parse_json(out, default)
    if not isinstance(structured, dict):
        structured = default
    return {"structured_req": structured}


# ---------------------------------------------------------------------------
# ② 竞品调研（真实联网检索 + 模型综合）
# ---------------------------------------------------------------------------
def _web_search(query: str, max_results: int = 5) -> list:
    """DuckDuckGo 联网检索。任何异常都吞掉并返回 []，绝不中断图。"""
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        with DDGS() as d:
            return list(d.text(query, max_results=max_results))
    except Exception:
        print("   联网检索不可用，回退模型知识")
        return []


# 常见中文引流 / 灰产关键词，命中即判垃圾片段
_SPAM_KEYWORDS = (
    "上门", "小姐", "品茶", "约炮", "约爱", "特殊服务", "上门服务",
    "TG客服", "tg客服", "薇芯", "微芯", "加微", "加V", "威信", "威芯",
    "私人定制", "全套", "一条龙", "楼凤", "外围", "嫩模", "陪玩陪睡",
    "SEO", "seo", "刷单", "刷量", "刷粉", "代刷", "发票", "代开",
    "博彩", "赌博", "彩票", "菠菜", "棋牌", "色情", "成人", "情色",
    "贷款", "办证", "代办", "包养", "援交",
)


def _gen_queries(req: dict) -> list:
    """让 LLM 把结构化需求提炼成 1~2 条简短中文检索词；失败时回退。"""
    fallback = [((req.get("target_user", "") if isinstance(req, dict) else "") or "产品") + " 竞品 App"]
    try:
        out = chat(SYS_QUERYGEN, json.dumps(req, ensure_ascii=False))
        data = parse_json(out, {})
        queries = data.get("queries", []) if isinstance(data, dict) else []
        clean = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
        return clean[:2] if clean else fallback
    except Exception:
        return fallback


def _looks_like_spam(text: str) -> bool:
    """判断一条检索片段是否为引流 / 灰产 / 非法链接垃圾。命中任一即 True。"""
    if not isinstance(text, str):
        return True
    low = text.lower()
    for kw in _SPAM_KEYWORDS:
        if kw.lower() in low:
            return True
    # href 必须是 http(s) 链接；文本里夹带非 http 协议时也判垃圾
    if "http://" not in low and "https://" not in low:
        return True
    return False


def research(state: ProjectState) -> dict:
    print("[② 竞品调研] 正在联网检索同类方案与市场缝隙 ...")
    req = state.get("structured_req", {})
    if not isinstance(req, dict):
        req = {}

    queries = _gen_queries(req)
    print("   检索词: " + " | ".join(queries))

    hits = []
    for q in queries:
        hits += _web_search(q, 5)

    # 干净过滤：剔除引流/灰产/非法链接，再按 href 去重，最多 6 条
    clean = []
    seen = set()
    for h in hits:
        href = h.get("href", "")
        blob = h.get("title", "") + h.get("body", "") + href
        if _looks_like_spam(blob):
            continue
        if href in seen:
            continue
        seen.add(href)
        clean.append(h)
        if len(clean) >= 6:
            break

    if clean:
        lines = []
        for i, h in enumerate(clean, 1):
            title = h.get("title", "")
            body = h.get("body", "")
            href = h.get("href", "")
            lines.append(f"[{i}] {title} — {body} ({href})")
        snippets = "\n".join(lines)
    else:
        snippets = "（无可用检索结果，请基于你的知识概览，sources 留空）"

    user = (
        json.dumps(req, ensure_ascii=False, indent=2)
        + "\n\n已过滤的检索片段:\n"
        + snippets
    )
    out = chat(SYS_RESEARCH, user)
    default = {"competitors": [], "opportunities": [], "sources": []}
    data = parse_json(out, default)
    if not isinstance(data, dict):
        data = dict(default)

    # sources 净化：只保留 LLM 返回且确实出现在 clean href 集合里的 URL，去重
    clean_hrefs = {h.get("href", "") for h in clean if h.get("href", "")}
    raw_sources = data.get("sources", []) or []
    if not isinstance(raw_sources, list):
        raw_sources = []
    sources = []
    for s in raw_sources:
        if isinstance(s, str) and s in clean_hrefs and s not in sources:
            sources.append(s)
    data["sources"] = sources
    return {"research": data}


# ---------------------------------------------------------------------------
# ③ PRD 草稿（+③b 流程图；循环感知：带 prev_prd + eval_feedback 定向改进）
# ---------------------------------------------------------------------------
def prd_draft(state: ProjectState) -> dict:
    it = state.get("prd_iteration", 0)
    if it > 0:
        print(f"[③ PRD 草稿] 第{it + 1}轮：依据终审反馈定向改进 PRD ...")
    else:
        print("[③ PRD 草稿] 正在依据结构化需求撰写 PRD 草稿 ...")

    req = state.get("structured_req", {})
    research_data = state.get("research", {})
    payload: dict = {"structured_req": req, "research": research_data}

    # 循环回路：带上一版 PRD 与三评委 fail 理由，做定向改进
    if it > 0:
        feedback = []
        for key in ("eval_pm", "eval_dev", "eval_qa"):
            vote = state.get(key, {})
            if isinstance(vote, dict):
                for r in vote.get("reasons", []) or []:
                    if isinstance(r, str) and r.strip():
                        feedback.append(r)
        payload["prev_prd"] = state.get("current_prd", "")
        payload["eval_feedback"] = feedback

    user = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        prd = chat(SYS_PRD, user)
    except Exception as e:  # noqa: BLE001
        print(f"   [③ PRD 草稿] 撰写异常: {e}")
        prd = state.get("current_prd", "") or ""

    # ③b 流程图（mermaid）：输入=结构化需求 + PRD 草稿
    try:
        flow = chat(
            SYS_FLOWCHART,
            "结构化需求:\n"
            + json.dumps(req, ensure_ascii=False, indent=2)
            + "\n\nPRD 草稿:\n"
            + prd,
        )
    except Exception as e:  # noqa: BLE001
        print(f"   [③b 流程图] 生成异常: {e}")
        flow = ""

    return {"prd_draft": prd, "current_prd": prd, "prd_flowchart": flow}


# ---------------------------------------------------------------------------
# ④ 三视角并行评审（各写独立 key，无 reducer）
# ---------------------------------------------------------------------------
def _warn_fallback(intended: str, model: str) -> None:
    """若实际裁决模型与意图 CLI 不一致（发生了回退），打印真实模型，避免控制台撒谎。"""
    if model != intended:
        print(f"   ⚠ {intended} 不可用，实际由 {model} 裁决")


def critic_pm(state: ProjectState) -> dict:
    intended = JUDGE_CLI["pm"]
    print(f"[④ 评审·产品视角] 委托 {intended} 挑毛病 ...")
    draft = state.get("current_prd") or state.get("prd_draft", "")
    reviews, model = _cli_judge("pm", SYS_CRITIC_PM, draft, [])
    _warn_fallback(intended, model)
    if not isinstance(reviews, list):
        reviews = []
    return {"review_pm": reviews}


def critic_dev(state: ProjectState) -> dict:
    intended = JUDGE_CLI["dev"]
    print(f"[④ 评审·开发视角] 委托 {intended} 排雷 ...")
    draft = state.get("current_prd") or state.get("prd_draft", "")
    reviews, model = _cli_judge("dev", SYS_CRITIC_DEV, draft, [])
    _warn_fallback(intended, model)
    if not isinstance(reviews, list):
        reviews = []
    return {"review_dev": reviews}


def critic_qa(state: ProjectState) -> dict:
    intended = JUDGE_CLI["qa"]
    print(f"[④ 评审·QA视角] 委托 {intended} 戳验收漏洞 ...")
    draft = state.get("current_prd") or state.get("prd_draft", "")
    reviews, model = _cli_judge("qa", SYS_CRITIC_QA, draft, [])
    _warn_fallback(intended, model)
    if not isinstance(reviews, list):
        reviews = []
    return {"review_qa": reviews}


# ---------------------------------------------------------------------------
# ⑤ 收敛终稿（fan-in）
# ---------------------------------------------------------------------------
def converge(state: ProjectState) -> dict:
    print("[⑤ 收敛终稿] 正在汇总三视角评审并改写 PRD ...")
    payload = {
        "prd_draft": state.get("current_prd") or state.get("prd_draft", ""),
        "review_pm": state.get("review_pm", []),
        "review_dev": state.get("review_dev", []),
        "review_qa": state.get("review_qa", []),
    }
    user = json.dumps(payload, ensure_ascii=False, indent=2)
    final = chat(SYS_CONVERGE, user)
    issue_log = {
        "review_pm": payload["review_pm"],
        "review_dev": payload["review_dev"],
        "review_qa": payload["review_qa"],
    }
    return {
        "current_prd": final,
        "prd_final": final,
        "issue_log": issue_log,
    }


# ---------------------------------------------------------------------------
# ⑤b 终审投票（三视角并行，各写独立 key）
# ---------------------------------------------------------------------------
def _eval_vote(state: ProjectState, lens_key: str, lens: str) -> dict:
    user = "评审视角：" + lens + "\n\nPRD：\n" + state.get("current_prd", "")
    default = {"verdict": "fail", "reasons": ["解析失败"]}
    v, model = _cli_judge(lens_key, SYS_EVAL_VOTE, user, default)
    if not isinstance(v, dict):
        v = dict(default)
    v["model"] = model  # 方便前端展示实际裁决方
    return v


def eval_pm(state: ProjectState) -> dict:
    intended = JUDGE_CLI["pm"]
    print(f"[⑤b 终审·产品视角] 委托 {intended} 投票 ...")
    vote = _eval_vote(state, "pm", LENS_PM)
    _warn_fallback(intended, vote.get("model", ""))
    return {"eval_pm": vote}


def eval_dev(state: ProjectState) -> dict:
    intended = JUDGE_CLI["dev"]
    print(f"[⑤b 终审·技术视角] 委托 {intended} 投票 ...")
    vote = _eval_vote(state, "dev", LENS_DEV)
    _warn_fallback(intended, vote.get("model", ""))
    return {"eval_dev": vote}


def eval_qa(state: ProjectState) -> dict:
    intended = JUDGE_CLI["qa"]
    print(f"[⑤b 终审·质量视角] 委托 {intended} 投票 ...")
    vote = _eval_vote(state, "qa", LENS_QA)
    _warn_fallback(intended, vote.get("model", ""))
    return {"eval_qa": vote}


# ---------------------------------------------------------------------------
# ⑤b 闸门（fan-in）：≥2 票 fail 且未超上限 → revise 回 prd_draft
# ---------------------------------------------------------------------------
def eval_gate(state: ProjectState) -> dict:
    print("[⑤b 终审闸门] 正在统计三评委票数并判定是否再迭代 ...")
    fails = 0
    for key in ("eval_pm", "eval_dev", "eval_qa"):
        vote = state.get(key, {})
        if isinstance(vote, dict) and vote.get("verdict") == "fail":
            fails += 1
    it = state.get("prd_iteration", 0)
    prev = state.get("eval_prev_fail")  # 上一轮 fail 票，首轮为 None
    # 收敛即止：通过 / 到上限 / 较上轮未改善，三者任一即停，避免每次都烧满循环预算
    if fails < 2:
        verdict, why = "done", "通过（≥2 评委放行）"
    elif it >= PRD_MAX_ITERS:
        verdict, why = "done", f"达迭代上限（{PRD_MAX_ITERS}）"
    elif prev is not None and fails >= prev:
        verdict, why = "done", f"未见改善（上轮{prev}→本轮{fails}），收敛即止"
    else:
        verdict, why = "revise", "仍≥2 fail 且较上轮改善"
    new_it = it + 1 if verdict == "revise" else it
    print(f"   [⑤b 闸门] 第{it}轮：fail 票={fails}/3 → {verdict}（{why}）")
    return {
        "eval_gate": {"fail_count": fails, "verdict": verdict, "iteration": it, "reason": why},
        "prd_iteration": new_it,
        "eval_prev_fail": fails,
    }


def route_eval(state: ProjectState) -> str:
    return state.get("eval_gate", {}).get("verdict", "done")


# ---------------------------------------------------------------------------
# ⑥ 拆解：用户故事 / 验收标准 / 测试用例
# ---------------------------------------------------------------------------
def breakdown(state: ProjectState) -> dict:
    print("[⑥ 拆解交付单元] 正在把终版 PRD 拆成用户故事/验收/测试用例 ...")
    prd = state.get("prd_final", "")
    out = chat(SYS_BREAKDOWN, prd)
    default = {"user_stories": [], "acceptance": [], "test_cases": []}
    result = parse_json(out, default)
    if not isinstance(result, dict):
        result = default
    return {"breakdown": result}


# ---------------------------------------------------------------------------
# ⑧ 原型生成（planner → ThreadPoolExecutor 并行造 part → merge）
# ---------------------------------------------------------------------------
def _invoke_cli(
    cli_name: str,
    prompt: str,
    *,
    write_mode: bool = False,
    cwd: str | None = None,
    timeout: int = 420,
) -> dict:
    """通用 headless CLI 调用器（claude / gemini / codex 等）。

    prompt 一律走 stdin（绕开 Windows `cmd /c` 长多行 argv 换行截断 flag 的坑）。
    全程容错：任何异常都吞掉并返回带 error 的 dict，绝不抛出中断图。

    返回 {"ok": returncode==0, "stdout", "stderr", "returncode"}；
    PATH 缺失/异常时返回 {"ok": False, "error": ...}。

    各 CLI 的 headless/非交互旗标（已在本机 verify 阶段逐个试通）：
      claude: [exe, "-p"] (+ --dangerously-skip-permissions when write_mode)
              prompt 走 stdin，stdout 直出文本/JSON。
      gemini: [exe, "-p", "", "--skip-trust"]
              -p 需要值参数（不能裸给），传空串让 prompt 从 stdin 读取（gemini
              会把 stdin 追加进 prompt）；--skip-trust 跳过"未信任目录"门禁（rc=55）。
              warnings 走 stderr，不污染 stdout 的 JSON。
      codex : [exe, "exec", "--skip-git-repo-check"]
              exec 子命令非交互；无 prompt 实参时从 stdin 读取；--skip-git-repo-check
              跳过"非受信目录"门禁。注意 codex 的鉴权/模型在本机配置（provider/key）
              决定可用性，与旗标无关。
    """
    try:
        exe = shutil.which(cli_name)
        if exe is None:
            return {"ok": False, "error": f"{cli_name} 不在 PATH"}

        if cli_name == "claude":
            args = [exe, "-p"] + (["--dangerously-skip-permissions"] if write_mode else [])
        elif cli_name == "gemini":
            # -p 需带值；空串 + stdin 让 prompt 从标准输入进入；--skip-trust 过信任门禁
            args = [exe, "-p", "", "--skip-trust"]
        elif cli_name == "codex":
            args = [exe, "exec", "--skip-git-repo-check"]
        else:
            # 未知 CLI：保守按 [-p] 起，prompt 仍走 stdin。
            args = [exe, "-p"]

        # Windows 兼容：CLI 多为 .cmd/.bat，列表方式直接执行可能失败，用 cmd /c 包一层
        if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
            args = ["cmd", "/c"] + args

        proc = subprocess.run(
            args,
            input=prompt,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired as e:
        return {"ok": False, "error": f"{cli_name} 执行超时(>{timeout}s): {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{cli_name} 调用异常: {e}"}


def _invoke_coding_agent(spec: str, workdir: str, timeout: int = 600) -> dict:
    """⑧ 原型生成仍走 claude + write_mode（薄封装到 _invoke_cli）。

    全程容错：任何异常都吞掉并返回带 error 的 dict，绝不抛出中断图。
    """
    try:
        os.makedirs(workdir, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"无法创建工作目录: {e}"}
    return _invoke_cli("claude", spec, write_mode=True, cwd=workdir, timeout=timeout)


def _cli_judge(lens_key: str, system: str, user: str, default: Any) -> tuple:
    """异构 CLI 只读裁决，失败回退 GLM。返回 (data, model_used)。

    - 按 lens_key 路由到 JUDGE_CLI[lens_key]（pm→claude / dev→gemini / qa→codex）。
    - 评委是只读任务：prompt = system + "\n\n" + user（不写文件，write_mode=False）。
    - CLI ok 且 stdout 能 parse_json 出非 None → (parsed, cli)。
    - 否则（PATH 缺失 / 非零退出 / 解析失败）回退 GLM：
        out = chat(system, user); return (parse_json(out, default), f"glm-fallback({cli})")。
    - 绝不抛异常。
    """
    cli = JUDGE_CLI.get(lens_key, "claude")
    try:
        res = _invoke_cli(cli, system + "\n\n" + user, write_mode=False)
        if res.get("ok"):
            parsed = parse_json(res.get("stdout", "") or "", None)
            if parsed is not None:
                return parsed, cli
    except Exception:  # noqa: BLE001
        pass
    # 回退 GLM
    try:
        out = chat(system, user)
        return parse_json(out, default), f"glm-fallback({cli})"
    except Exception:  # noqa: BLE001
        return default, f"glm-fallback({cli})"


def _salvage_html(text: str) -> str:
    """兜底：若 agent 没落盘却把 HTML 贴在了输出里，从文本中抠出完整 HTML 文档。"""
    if not text:
        return ""
    m = re.search(r"```html\s*(.*?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(<!DOCTYPE html.*?</html>)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"(<html.*?</html>)", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    return ""


# 落盘指令（原样写进 spec 结尾，逼 agent 真正用 Write 工具创建文件）
_WRITE_DIRECTIVE = (
    "立即用 Write 工具把目标文件写入当前工作目录；"
    "禁止请求授权或确认、禁止只输出计划或描述、禁止把完整代码贴在回复文本里；"
    "必须真正创建出文件，写完后只简短回复「已写入」。"
)


def prototype(state: ProjectState) -> dict:
    proto_it = state.get("proto_iteration", 0)
    if proto_it > 0:
        print(f"[⑧ 原型生成] 第{proto_it + 1}轮：依据验收反馈并行重造原型 ...")
    else:
        print("[⑧ 原型生成] 正在 planner 拆模块并并行委托 coding agent（headless）...")

    prd_final = state.get("prd_final", "")
    bd = state.get("breakdown", {})

    # 改进要求：上一轮验收 fail 的 reasons 汇总
    improve = ""
    if proto_it > 0:
        fb = []
        for key in ("accept_pm", "accept_dev", "accept_qa"):
            vote = state.get(key, {})
            if isinstance(vote, dict) and vote.get("verdict") == "fail":
                for r in vote.get("reasons", []) or []:
                    if isinstance(r, str) and r.strip():
                        fb.append(r)
        if fb:
            improve = "\n\n改进要求（上一轮验收反馈）:\n- " + "\n- ".join(fb)

    # planner：把原型拆成最多 2 个可并行模块
    try:
        plan = parse_json(
            chat(
                SYS_PROTO_PLAN,
                json.dumps(
                    {"prd": prd_final, "breakdown": bd}, ensure_ascii=False
                )
                + improve,
            ),
            {"parts": []},
        )
    except Exception as e:  # noqa: BLE001
        print(f"   [⑧ planner] 拆分异常: {e}")
        plan = {"parts": []}

    parts = plan.get("parts", []) if isinstance(plan, dict) else []
    if not isinstance(parts, list):
        parts = []
    parts = parts[:PROTO_PARTS]
    # 退化：planner 空结果时，用整份 PRD 的简化 spec 造单 part
    if not parts:
        parts = [{
            "name": "原型主页面",
            "spec": "依据以下 PRD 的核心 happy-path，做一个移动端风格的单页原型，"
                    "覆盖最关键的几条验收点。PRD:\n" + prd_final[:4000] + improve,
        }]

    workdir = os.path.join(os.getcwd(), "prototype_out")
    try:
        os.makedirs(workdir, exist_ok=True)
        # 清理旧 part_*.html 与 index.html
        for fn in os.listdir(workdir):
            if fn == "index.html" or re.match(r"^part_\d+\.html$", fn):
                try:
                    os.remove(os.path.join(workdir, fn))
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        print(f"   [⑧ 原型生成] 清理工作目录异常: {e}")

    trace_parts = []

    def _build_part(idx: int, part: dict) -> dict:
        name = part.get("name", f"模块{idx}") if isinstance(part, dict) else f"模块{idx}"
        pspec = part.get("spec", "") if isinstance(part, dict) else ""
        spec = (
            f"你要构建原型的一个模块「{name}」。模块构建说明:\n{pspec}\n\n"
            "硬性约束：只产出一个自包含的 HTML 片段文件，所有 CSS/JS 内联，"
            "允许 CDN 外链，不得有构建步骤或后端；UI 用简体中文、移动端风格。\n"
            f"【落盘指令】把该模块写入 part_{idx}.html 到当前目录。" + _WRITE_DIRECTIVE
        )
        try:
            res = _invoke_coding_agent(spec, workdir, timeout=420)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": f"并行造 part 异常: {e}"}
        return {"name": name, "res": res}

    # 并行构建各 part
    results = []
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=PROTO_PARTS) as ex:
            futs = {
                ex.submit(_build_part, i, p): i for i, p in enumerate(parts)
            }
            for fut in concurrent.futures.as_completed(futs):
                try:
                    results.append((futs[fut], fut.result()))
                except Exception as e:  # noqa: BLE001
                    results.append((futs[fut], {"name": "?", "res": {"ok": False, "error": str(e)}}))
    except Exception as e:  # noqa: BLE001
        print(f"   [⑧ 原型生成] 并行执行异常: {e}")

    results.sort(key=lambda x: x[0])
    part_names = []
    for idx, r in results:
        part_names.append(r.get("name", f"模块{idx}"))
        res = r.get("res", {})
        trace_parts.append(
            f"[part_{idx} {r.get('name','')}] ok={bool(res.get('ok'))} "
            + (res.get("stdout") or res.get("error") or "")[-400:]
        )

    # 合并：读取所有 part_*.html 合成自包含 index.html（顶部 Tab 切换）
    merge_spec = (
        "读取当前目录的 part_0.html、part_1.html 等所有 part 文件，"
        "把它们合并成一个自包含 index.html：顶部 Tab 切换各模块、"
        "内联所有 CSS/JS、保证可直接浏览器打开；写入 index.html。"
        + _WRITE_DIRECTIVE
    )
    try:
        merge_res = _invoke_coding_agent(merge_spec, workdir, timeout=420)
    except Exception as e:  # noqa: BLE001
        merge_res = {"ok": False, "error": f"合并 index.html 异常: {e}"}
    trace_parts.append(
        "[merge] ok=" + str(bool(merge_res.get("ok")))
        + (merge_res.get("stdout") or merge_res.get("error") or "")[-600:]
    )

    index = os.path.join(workdir, "index.html")

    def _is_built() -> bool:
        try:
            return os.path.exists(index) and os.path.getsize(index) > 200
        except Exception:  # noqa: BLE001
            return False

    built = _is_built()

    # 抢救兜底：merge agent 没落盘但在输出里贴了完整 HTML，则替它写入。
    salvaged = False
    if not built:
        html = _salvage_html(merge_res.get("stdout", "") or "")
        if html:
            try:
                with open(index, "w", encoding="utf-8") as f:
                    f.write(html)
                salvaged = _is_built()
                built = salvaged
            except Exception:  # noqa: BLE001
                pass

    agent_ok = bool(merge_res.get("ok"))
    trace = ("\n".join(trace_parts))[-2000:]

    print(
        f"   [⑧ 原型生成] parts={part_names}，merge_ok={agent_ok}，"
        f"index.html {'已生成' if built else '未生成'}"
        f"{'（抢救落盘）' if salvaged else ''}"
    )
    return {
        "prototype": {
            "workdir": workdir,
            "parts": part_names,
            "agent_ok": agent_ok,
            "index_exists": built,
            "salvaged": salvaged,
            "trace": trace,
        },
        "proto_iteration": state.get("proto_iteration", 0),
    }


# ---------------------------------------------------------------------------
# ⑨ 验收投票（三视角并行，各写独立 key）
# ---------------------------------------------------------------------------
def _read_index_html(state: ProjectState) -> str:
    proto = state.get("prototype", {})
    if not isinstance(proto, dict):
        proto = {}
    index = os.path.join(proto.get("workdir", "prototype_out"), "index.html")
    try:
        if os.path.exists(index):
            with open(index, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _accept_vote(state: ProjectState, lens_key: str, lens: str) -> dict:
    html = _read_index_html(state)
    if not html:
        return {"verdict": "fail", "reasons": ["原型缺失"]}
    bd = state.get("breakdown", {})
    criteria = bd.get("acceptance", []) if isinstance(bd, dict) else []
    user = (
        "评审视角：" + lens
        + "\n验收标准：\n" + json.dumps(criteria, ensure_ascii=False)
        + "\n原型HTML：\n" + html[:6000]
    )
    default = {"verdict": "fail", "reasons": ["解析失败"]}
    v, model = _cli_judge(lens_key, SYS_ACCEPT_VOTE, user, default)
    if not isinstance(v, dict):
        v = dict(default)
    v["model"] = model  # 方便前端展示实际裁决方
    return v


def accept_pm(state: ProjectState) -> dict:
    intended = JUDGE_CLI["pm"]
    print(f"[⑨ 验收·产品视角] 委托 {intended} 投票 ...")
    vote = _accept_vote(state, "pm", LENS_PM)
    _warn_fallback(intended, vote.get("model", ""))
    return {"accept_pm": vote}


def accept_dev(state: ProjectState) -> dict:
    intended = JUDGE_CLI["dev"]
    print(f"[⑨ 验收·技术视角] 委托 {intended} 投票 ...")
    vote = _accept_vote(state, "dev", LENS_DEV)
    _warn_fallback(intended, vote.get("model", ""))
    return {"accept_dev": vote}


def accept_qa(state: ProjectState) -> dict:
    intended = JUDGE_CLI["qa"]
    print(f"[⑨ 验收·质量视角] 委托 {intended} 投票 ...")
    vote = _accept_vote(state, "qa", LENS_QA)
    _warn_fallback(intended, vote.get("model", ""))
    return {"accept_qa": vote}


# ---------------------------------------------------------------------------
# ⑨ 验收闸门（fan-in）：≥2 票 fail 且未超上限 → revise 回 prototype
# ---------------------------------------------------------------------------
def accept_gate(state: ProjectState) -> dict:
    print("[⑨ 验收闸门] 正在统计验收票数并判定是否返工 ...")
    votes = {}
    fails = 0
    for short, key in (("pm", "accept_pm"), ("dev", "accept_dev"), ("qa", "accept_qa")):
        vote = state.get(key, {})
        if not isinstance(vote, dict):
            vote = {"verdict": "fail", "reasons": ["解析失败"]}
        votes[short] = vote
        if vote.get("verdict") == "fail":
            fails += 1

    it = state.get("proto_iteration", 0)
    prev = state.get("accept_prev_fail")
    # 收敛即止（同 eval_gate）：原型回路尤其贵（每轮调多次 claude），未改善就别再烧
    if fails < 2:
        verdict, why = "done", "通过（≥2 评委放行）"
    elif it >= PROTO_MAX_ITERS:
        verdict, why = "done", f"达迭代上限（{PROTO_MAX_ITERS}）"
    elif prev is not None and fails >= prev:
        verdict, why = "done", f"未见改善（上轮{prev}→本轮{fails}），收敛即止"
    else:
        verdict, why = "revise", "仍≥2 fail 且较上轮改善"
    new_it = it + 1 if verdict == "revise" else it

    # 原型文件状态（兼容旧 acceptance_report 字段）
    html = _read_index_html(state)
    file_ok = bool(html) and len(html) > 200

    acceptance_report = {
        "file_ok": file_ok,
        "html_bytes": len(html),
        "fail_count": fails,
        "verdict": verdict,
        "reason": why,
        "votes": votes,
    }
    print(f"   [⑨ 闸门] 第{it}轮：fail 票={fails}/3 → {verdict}（{why}）")
    return {
        "accept_gate": {"fail_count": fails, "verdict": verdict, "iteration": it, "reason": why},
        "proto_iteration": new_it,
        "acceptance_report": acceptance_report,
    }


def route_accept(state: ProjectState) -> str:
    return state.get("accept_gate", {}).get("verdict", "done")
