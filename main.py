"""主链路命令行入口（M5：checkpointer + interrupt 续跑）。

三种模式：
    python main.py "需求"                # 新 thread；跑到 breakdown 后、prototype 前
                                        # 自动暂停（interrupt_before=["prototype"]），
                                        # 打印到 breakdown 为止的产物 + thread_id + 续跑提示
    python main.py "需求" --prototype    # 同上跑到暂停后立即 resume，跑完八/九并打印全部
    python main.py --resume <thread_id>  # 不需 raw_input；同一 DB+tid 从存档续跑八/九

其它：
    python main.py                      # 无参数用内置 SAMPLE 需求（默认暂停模式）
    python main.py "需求" --md out.md    # 额外把运行报告写成 markdown 文件（与上面三模式叠加）

注意：load_dotenv() 必须在 import src.graph 之前执行，
以保证 src.llm 在被导入、读取环境变量（ZAI_API_KEY 等）时已加载 .env。
"""
import json
import sys
import uuid

# Windows 控制台默认 GBK，会让中文产物乱码；强制 stdout/stderr 用 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from dotenv import load_dotenv

load_dotenv()  # 必须先于下面的 import src.graph

from src.graph import build_resumable_graph  # noqa: E402
from src.checkpoint import open_checkpointer  # noqa: E402


# 任意产品的默认示例需求
SAMPLE = (
    "我想做一个面向独居上班族的智能冰箱助手 App，"
    "能识别冰箱里还剩什么食材、提醒临期食品、并根据现有食材推荐今晚能做的菜，"
    "最好还能在食材快用完时帮我一键下单补货。"
)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _build_sections(
    raw: str, result: dict, with_prototype: bool = False
) -> list[tuple[str, str, str]]:
    """返回 (标题, 内容, 类型) 列表；类型 ∈ {text, json, markdown}。"""
    sections = [
        ("原始需求输入", raw, "text"),
        ("① 结构化需求", _dump(result.get("structured_req", {})), "json"),
        ("② 竞品调研", _dump(result.get("research", {})), "json"),
        ("③ PRD 草稿", result.get("prd_draft", "（空）"), "markdown"),
        ("③b 流程图（mermaid）", result.get("prd_flowchart", "（空）"), "markdown"),
        ("④ 三视角评审 · 产品(PM)", _dump(result.get("review_pm", [])), "json"),
        ("④ 三视角评审 · 开发(DEV)", _dump(result.get("review_dev", [])), "json"),
        ("④ 三视角评审 · 质量(QA)", _dump(result.get("review_qa", [])), "json"),
        ("⑤ 终版 PRD", result.get("prd_final", "（空）"), "markdown"),
        ("⑤b 终审投票 · 产品(PM)", _dump(result.get("eval_pm", {})), "json"),
        ("⑤b 终审投票 · 开发(DEV)", _dump(result.get("eval_dev", {})), "json"),
        ("⑤b 终审投票 · 质量(QA)", _dump(result.get("eval_qa", {})), "json"),
        ("⑤b 终审闸门（票数/判定/轮次）", _dump(result.get("eval_gate", {})), "json"),
        ("⑤ 问题处置记录 issue_log", _dump(result.get("issue_log", {})), "json"),
        ("⑥ 拆解：用户故事 / 验收标准 / 测试用例", _dump(result.get("breakdown", {})), "json"),
    ]
    if with_prototype:
        sections.append(
            ("⑧ 原型生成（planner→并行造模块→合并）", _dump(result.get("prototype", {})), "json")
        )
        sections.append(
            ("⑨ 验收投票 · 产品(PM)", _dump(result.get("accept_pm", {})), "json")
        )
        sections.append(
            ("⑨ 验收投票 · 开发(DEV)", _dump(result.get("accept_dev", {})), "json")
        )
        sections.append(
            ("⑨ 验收投票 · 质量(QA)", _dump(result.get("accept_qa", {})), "json")
        )
        sections.append(
            ("⑨ 验收闸门（票数/判定/轮次）", _dump(result.get("accept_gate", {})), "json")
        )
    return sections


def _print_console(sections: list[tuple[str, str, str]]) -> None:
    for title, content, _kind in sections:
        print("\n" + "=" * 60)
        print(f"  {title}")
        print("=" * 60)
        print(content)


def _to_markdown(sections: list[tuple[str, str, str]]) -> str:
    lines = ["# PM Copilot 运行报告", ""]
    for title, content, kind in sections:
        lines.append(f"## {title}")
        lines.append("")
        if kind == "json":
            lines.append("```json")
            lines.append(content)
            lines.append("```")
        elif kind == "markdown":
            lines.append(content)  # PRD 本身就是 markdown，直接嵌入
        else:
            lines.append(content)
        lines.append("")
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> tuple[str, str | None, bool, str | None]:
    """解析命令行：返回 (需求文本, markdown 输出路径或 None, 是否开启 --prototype,
    续跑 thread_id 或 None)。

    --resume <tid>：续跑模式，此时不需要 raw_input。
    """
    args = argv[1:]
    with_prototype = False
    if "--prototype" in args:
        with_prototype = True
        args = [a for a in args if a != "--prototype"]
    resume_tid = None
    if "--resume" in args:
        i = args.index("--resume")
        resume_tid = args[i + 1] if i + 1 < len(args) else None
        # 移除 --resume 及其值
        args = args[:i] + args[i + 2:] if resume_tid is not None else args[:i]
    md_path = None
    if "--md" in args:
        i = args.index("--md")
        md_path = args[i + 1] if i + 1 < len(args) else "sample_run.md"
        args = args[:i] + args[i + 2:]
    raw = args[0].strip() if args and args[0].strip() else SAMPLE
    return raw, md_path, with_prototype, resume_tid


def _is_interrupted(graph, config) -> bool:
    """图当前是否停在 prototype 前的中断点。"""
    return "prototype" in (graph.get_state(config).next or ())


def _thread_exists(graph, config) -> bool:
    """该 thread_id 是否在 checkpoint DB 里有存档（未知 thread 时 created_at 为 None）。"""
    return graph.get_state(config).created_at is not None


def _emit(raw: str, result: dict, with_prototype: bool, md_path: str | None) -> None:
    """统一出口：打印控制台 + 按需写 markdown。"""
    sections = _build_sections(raw, result, with_prototype=with_prototype)
    _print_console(sections)
    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(_to_markdown(sections))
        print(f"\n[已写出 markdown 报告] {md_path}")


def main() -> None:
    raw, md_path, with_prototype, resume_tid = _parse_args(sys.argv)

    # checkpointer 上下文包住整个 invoke，退出即关闭 sqlite 连接
    with open_checkpointer() as cp:
        graph = build_resumable_graph(cp)

        # ===== 模式三：--resume <tid> 从存档续跑八/九 =====
        if resume_tid is not None:
            config = {"configurable": {"thread_id": resume_tid}}
            if not _thread_exists(graph, config):
                print(f"[续跑失败] thread_id={resume_tid} 不存在（DB 无此存档），"
                      f"请先用 python main.py \"需求\" 生成。")
                return
            if not _is_interrupted(graph, config):
                print(f"[无需续跑] thread_id={resume_tid} 已完成或未处于 prototype 前暂停，"
                      f"无可续跑的步骤。")
                return
            print(f"[续跑] thread_id={resume_tid}：从存档恢复，进入 ⑧ 原型 / ⑨ 验收 ...")
            result = graph.invoke(None, config)
            _emit(result.get("raw_input", ""), result, with_prototype=True, md_path=md_path)
            return

        # ===== 模式一/二：新 thread，先跑到 prototype 前暂停 =====
        tid = uuid.uuid4().hex[:12]
        config = {"configurable": {"thread_id": tid}}
        result = graph.invoke({"raw_input": raw}, config)

        if with_prototype:
            # ===== 模式二：--prototype 暂停后立即 resume 跑完八/九 =====
            if _is_interrupted(graph, config):
                print("\n[--prototype] 已到 prototype 前断点，立即续跑 ⑧ 原型 / ⑨ 验收 ...")
                result = graph.invoke(None, config)
            _emit(raw, result, with_prototype=True, md_path=md_path)
            print(f"\n[thread_id] {tid}（已一气呵成跑完原型）")
            return

        # ===== 模式一：默认暂停在 prototype 前，打印到 breakdown 为止 =====
        _emit(raw, result, with_prototype=False, md_path=md_path)
        if _is_interrupted(graph, config):
            print("\n" + "=" * 60)
            print(f"  [已暂停] thread_id = {tid}")
            print("  已存档至 breakdown 后、prototype 前。要生成原型请续跑：")
            print(f"      python main.py --resume {tid}")
            print("=" * 60)


if __name__ == "__main__":
    main()
