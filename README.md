# PM Copilot

> 基于 Anthropic《Building Effective Agents》的产品经理副驾：从**一句话需求**出发，自动生成结构化需求 → PRD 草稿 → 三视角并行评审 → 收敛终版 → 拆解为用户故事/验收/测试用例，最终（后续里程碑）落到**可运行原型**。

PM Copilot 把「写 PRD」这件事拆成一条由 LLM 驱动的工作流（Workflow），用 LangGraph 把每一步显式编排成节点。它刻意采用《Building Effective Agents》里推荐的**可控编排模式**（Prompt Chaining + Parallelization + Orchestrator/Evaluator），而非放养式 Agent，让每一步的输入输出都可观察、可复现。

---

## 实现进度（M0–M4 全部完成）

本仓库已实现并实跑验证 **M0–M4 完整管线 + 可视化前端**。下方「M0 范围 / 主链路流程图 / 节点契约」等小节为最初分期说明，**当前实际架构以 `DESIGN.md` §11 实现进度为准**（含：扇出三评委终审 + 验收双回路、运行时并行多 agent 原型、收敛即止闸门）。运行报告见 `sample_run_m1/m2/m4.md`。

- **M0** 主链路 ①③④⑤⑥ · **M1** ②真实调研 + ⑤循环 + 评估 · **M2** ⑧原型(headless 调 Claude Code, hybrid) + ⑨验收 · **M3** FastAPI SSE + React Flow 活图 · **M4** 三评委投票回路重构 + 收敛即止闸门
- **M4 之后**：**异构 CLI 评委**（9 个评委按视角路由 PM→Claude / DEV→Gemini / QA→Codex，失败回退 GLM，对症局限 #2）；**checkpointer 续跑**（SqliteSaver + `interrupt_before=["prototype"]`：跑到拆解后暂停，`--resume <tid>` 或前端「续跑」按钮从存档造原型、不重跑 PRD，对症局限 #6 + 一致性）
- CLI：`python main.py "需求" [--prototype] [--md out.md]`；默认跑到 PRD 暂停并打印 `thread_id`，`python main.py --resume <thread_id>` 续跑造原型
- Web：`python server.py`（:8000）+ `cd web && npm run dev`（:5173）

---

## M0 范围

本期（M0）只实现**主链路**，对应设计稿的 ①③④⑤⑥ 五步：

| 步骤 | 节点 | 职责 |
| --- | --- | --- |
| ① 需求结构化 | `structure` | 把一句话需求解析为结构化字段 |
| ③ PRD 草稿 | `prd_draft` | 由结构化需求生成 PRD（markdown） |
| ④ 三视角并行评审 | `critic_pm` / `critic_dev` / `critic_qa` | 资深 PM / 开发 / QA 三视角并行挑问题 |
| ⑤ 收敛 | `converge` | 汇总评审意见，产出终版 PRD 与问题处置记录 |
| ⑥ 拆解 | `breakdown` | 拆成用户故事 / 验收标准 / 测试用例 |

**后续里程碑（M0 不做）：**
- **② 调研**：接真实检索 / 资料调研，丰富 `research` 字段（M1）。
- **⑧ 原型**：根据终版 PRD 生成可运行原型。
- **⑨ 验收**：原型自动验收。

> 状态字段定义见 `src/state.py` 的 `ProjectState`。其中 `research` 字段在 M0 留空，为 ② 预留。

---

## 技术栈

- **编排**：LangGraph（`StateGraph`，以 `ProjectState` 为 schema）
- **大模型**：GLM-5.1，经 Z.AI 的 OpenAI 兼容接口接入（`langchain-openai` 的 `ChatOpenAI`）
- **后续前端**：FastAPI（后端服务）+ React Flow（前端流程可视化），用于把工作流节点与中间产物可视化、可交互。

---

## 安装与运行

环境：Windows，Python 3.12。

```powershell
# 1) 进入项目目录
cd D:\code\pm-copilot

# 2) 创建并激活虚拟环境
python -m venv venv
.\venv\Scripts\Activate.ps1

# 3) 安装依赖
pip install -r requirements.txt

# 4) 配置密钥：复制 .env.example 为 .env，填入你的 Z.AI Key
copy .env.example .env
# 然后编辑 .env，把 ZAI_API_KEY 改成你的真实 key

# 5) 运行主链路（传入一句话需求）
python main.py "做一个帮独居老人按时吃药的提醒 App"
```

> macOS / Linux 下激活虚拟环境用 `source venv/bin/activate`，复制用 `cp .env.example .env`。

`.env` 需要的变量（见 `.env.example`）：

| 变量 | 说明 |
| --- | --- |
| `ZAI_API_KEY` | Z.AI 平台密钥（必填） |
| `ZAI_BASE_URL` | OpenAI 兼容端点，默认 `https://api.z.ai/api/paas/v4` |
| `GLM_CHAT_MODEL` | 模型名，默认 `glm-5.1` |

---

## 主链路流程图

```
                                  START
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │   structure  (①)      │  raw_input → structured_req
                        └───────────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │   prd_draft  (③)      │  structured_req → prd_draft
                        └───────────────────────┘
                                    │
                  ┌─────────────────┼─────────────────┐   ④ 三视角并行（fan-out）
                  ▼                 ▼                 ▼
          ┌─────────────┐  ┌──────────────┐  ┌─────────────┐
          │ critic_pm   │  │ critic_dev   │  │ critic_qa   │
          │ → review_pm │  │ → review_dev │  │ → review_qa │
          └─────────────┘  └──────────────┘  └─────────────┘
                  └─────────────────┼─────────────────┘   ⑤ 汇合（fan-in）
                                    ▼
                        ┌───────────────────────┐
                        │   converge   (⑤)      │  prd_draft + 三份 review
                        │                       │  → prd_final + issue_log
                        └───────────────────────┘
                                    │
                                    ▼
                        ┌───────────────────────┐
                        │   breakdown  (⑥)      │  prd_final
                        │                       │  → user_stories/acceptance/test_cases
                        └───────────────────────┘
                                    │
                                    ▼
                                   END
```

---

## 节点契约

所有节点签名统一为 `def name(state: ProjectState) -> dict`，**只返回自己写入的字段**（增量更新）。每个节点开头会 `print` 一行中文进度，便于观察（transparency）。

| 节点 | 读 | 写 | 备注 |
| --- | --- | --- | --- |
| `structure` | `raw_input` | `structured_req` | 解析为 `{target_user, pain_points[], scenarios[], value_hypotheses[], open_questions[]}` |
| `prd_draft` | `structured_req` | `prd_draft` | markdown 字符串 |
| `critic_pm` | `prd_draft` | `review_pm` | `temperature=0.6`，输出问题列表 |
| `critic_dev` | `prd_draft` | `review_dev` | `temperature=0.6`，输出问题列表 |
| `critic_qa` | `prd_draft` | `review_qa` | `temperature=0.6`，输出问题列表 |
| `converge` | `prd_draft` + 三份 review | `prd_final` + `issue_log` | 汇总收敛 |
| `breakdown` | `prd_final` | `breakdown` | `{user_stories[], acceptance[], test_cases[]}` |

三个 critic 各写**不同的 key**（`review_pm` / `review_dev` / `review_qa`），并行无写冲突，因此**不使用 reducer**。每条评审项的结构为：

```json
{ "issue": "...", "severity": "...", "evidence": "...", "suggestion": "..." }
```

---

## 代码结构与约定

```
pm-copilot/
├── DESIGN.md            # 设计稿
├── README.md            # 本文件
├── requirements.txt     # langgraph / langchain-openai / python-dotenv
├── .env.example         # 复制为 .env 填 key
├── main.py              # 入口：读命令行一句话需求，编译并跑 graph（待实现）
└── src/
    ├── __init__.py
    ├── state.py         # ProjectState（不可修改）
    ├── llm.py           # chat() / get_chat_llm()（不可修改）
    ├── prompts.py       # 各节点 system prompt 常量（待实现）
    └── nodes.py         # 各节点函数 + graph 装配（待实现）
```

**实现约定（写 `nodes.py` / `prompts.py` 时遵守）：**

- **相对导入**：`nodes.py` 从 `.llm` 导入 `chat`、从 `.prompts` 导入各常量、从 `.state` 导入 `ProjectState`。
- **`prompts.py` 必须导出**：`SYS_STRUCTURE`、`SYS_PRD`、`SYS_CRITIC_PM`、`SYS_CRITIC_DEV`、`SYS_CRITIC_QA`、`SYS_CONVERGE`、`SYS_BREAKDOWN`。
- **JSON 输出**：凡要求模型输出 JSON 的 prompt，必须明确「**只输出 JSON，不要任何解释、不要 markdown 代码块**」。
- **容错解析**：`nodes.py` 提供一个容错 JSON 解析 helper —— 先剥离 ```` ```json ```` 围栏再 `json.loads`，解析失败时返回传入的默认值，**不抛异常中断链路**。
- **温度**：三个 critic 用 `temperature=0.6`（鼓励发散挑刺），其余节点用 `chat` 默认温度。

---

## 设计理念

PM Copilot 把《Building Effective Agents》中的几种基础模式映射到真实的产品工作流：

- **Prompt Chaining**：① → ③ 把「结构化」与「写作」拆成两跳，各自更聚焦、可校验。
- **Parallelization（Sectioning）**：④ 三视角并行评审，PM / 开发 / QA 各看各的盲区，互不串味。
- **Orchestrator / Evaluator**：⑤ 收敛节点充当评估者，吸收三份评审、产出终版 PRD 与可追溯的 `issue_log`。

这种「显式编排 + 中间产物全部落到状态里」的做法，换来的是**可观察、可复现、可调试**，也为后续接入 ②（调研）/⑧（原型）/⑨（验收）留好了扩展位。

---

## 回路怎么走（两组评委，易混）

图上有**两组评委**，角色不同：

- **④ 详评** `critic_pm/dev/qa`：**找问题**（列 issue）。**每轮都**喂给 ⑤ `converge`，由它改写 PRD——不分过不过。
- **⑤b 终审** `eval_pm/dev/qa`：**投票**（pass/fail）。**≥2 票 fail 才触发回退**。

回退重写数据流：`⑤b ≥2 fail → eval_gate 判 revise（红）→ 回退 ③ prd_draft（重新运行）→ prd_draft 带【上一版 PRD + 投 fail 评委的 reasons】定向重写出新一版 → 再走 ④→⑤→⑤b……直到放行 / 收敛即止 / 到上限`。⑨ 验收回路同构（accept 投票 → 不过则回 ⑧ 重造原型）。

要点：**触发回退的是 ⑤b 投票评委**（不是 ④ 详评）；**"出新一版 PRD" 发生在 ③ prd_draft**（拿反馈重写），不是 converge。详见 `DESIGN.md` §13。

---

## 已知局限与改进方向

> 这是一个**能力演示**（展示 Agent 编排 / BEA 范式 / 系统级 debug），不是开箱即用的生产工具。清楚它的边界，本身就是工程判断力的一部分。

1. **没有真实上下文接地（grounding）——最致命。** 它不知道你公司的代码库、现有产品、团队、排期、历史决策、真实用户。产出的 PRD 读着漂亮却「放之四海皆准」，恰恰说明没解决任何具体问题。`②调研` 只是浅层网搜。
   *改进*：接公司 Confluence / Jira / 代码库 / 用户反馈做 RAG，让评审能说出「这和上季度那条冲突了」这种只有内部人知道的话。

2. **自评自证，鲁棒性是假象。** 三评委、三 critic 原本全是同一个 GLM 自言自语，同源判断高度相关，多数票并不比单票可靠多少。
   *已部分改进*：现已把 9 个评委按视角路由到**异构 CLI**（PM→Claude · DEV→Gemini · QA→Codex；缺失/失败自动回退 GLM），三个不同实验室的前沿模型并行裁决，盲点不再相关——实测出现过 Claude/Gemini 放行、GLM 否决的 **2:1 真分歧**。但仍是 LLM 判 LLM，且 grounding 缺口（#1）让它们可能「异口同声地错」，根治还需真人 / 真实测试做 ground truth。

3. **循环不保证收敛。** 评委（尤其同源时）几乎总能挑出 fail，回路容易打满预算而非真改好；实测还出现过「越改越差」。
   *已部分改进*：M4 把闸门从「还有 fail 就返工」改成**收敛即止**（通过 / 到上限 / 较上轮未改善任一即停），实测把「撞预算」变成了「检测到退化即叫停」；异构评委的真分歧也缓解了「众口一词永不收敛」。但仍是治标，根治需更强的 gate 与人审。

4. **原型是一次性 mock，会制造虚假信心。** ⑧ 出的是单文件假数据 HTML，没后端、没真可行性、没真人用过；⑨ 覆盖率诚实地偏低。看着能跑 ≠ 方案被验证。
   *改进*：接真用户测试 / 真功能验证，把「界面长这样」和「这事能不能成」分开。

5. **成本 / 延迟 / 不确定。** 一次跑十几到几十次 LLM 调用，开原型那条最坏十几次 claude、十几分钟；同样需求每次产出不同。
   *改进*：中间产物缓存、小模型分流、对结构化部分加确定性约束。

6. **没有人在环，PM 插不上手。** 一次性发射、中途无法干预。
   *已解决*：用 LangGraph `checkpointer`(SqliteSaver) + `interrupt_before=["prototype"]`，管线跑到拆解后**暂停存档**，由人决定是否续跑造原型；`--resume <thread_id>`（或前端「▶ 生成原型（续跑）」按钮）**从存档继续、不重跑 PRD**，原型必基于已审的那份 PRD。一举两得：既是 human-in-the-loop，也解决了「重跑会得到一份没审过的新 PRD」的一致性问题。剩余：`open_questions` 尚未做成可回答的交互。

7. **coding agent 无沙箱。** ⑧ 用 `--dangerously-skip-permissions` 在项目目录里跑 claude，demo 隔离 OK，生产化必须容器隔离。

8. **形式严谨可能掩盖实质平庸。** 它会产出结构精美、篇幅可观、看着很专业的东西，但底层判断可能很浅。**当辅助用，别当权威。**

> 一句话：作为求职 demo，上面每一条都是「我懂这类系统边界」的弹药；面试时主动抛出，比 demo 本身更值钱。
