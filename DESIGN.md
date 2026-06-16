# PM Copilot —— 从一句话需求到可运行原型

> 设计基准：Anthropic《Building Effective Agents》(以下简称 **BEA**)
> 定位：一条能跑通的全流程 demo，**一个 demo 同时展示三件事**——产品判断力、Workflow 编排、以及对一个自主 Agent 的编排（agent-of-agents）。
> 状态：已实现并开源（完整实现进度见 §11）。

---

## 0. 一句话定位

输入「一句话需求 + 一个产品场景」，输出一条完整可见的链路，**最终落到一个真的能跑起来的原型**：

**需求结构化 → 竞品调研 → PRD 草稿 → 对抗评审 → 收敛回填 → 拆解(用户故事/验收/测试用例) → 原型生成 → 验收**

每个环节都留下可读的中间产物。最后一步把 spec 交给一个自主 coding agent（Claude Code / Codex / Gemini CLI）产出可运行原型。

---

## 1. 先按 BEA 定性：这是一个 **Hybrid** —— Workflow 编排一个 Agent

先厘清 BEA 的核心区分：

- **Workflow**：LLM 和工具通过**预定义的代码路径**编排，路径可预测。
- **Agent**：LLM **动态决定**自己的流程和工具使用，开放、不可预测。
- BEA 同时强调：**这些模式可以、也应该组合（hybrid）**。

**本项目是 Hybrid，要讲准它的两层：**

1. **外层是 Workflow**——PM 出文档到原型的流程是高度可预测的固定阶段，所以**控制流由我写死**，不让 LLM 自己决定下一步。这正是 BEA 说的「能 workflow 就别 agent」。
2. **其中一个节点（⑧原型生成）委托一个 Agent**——`Claude Code / Codex CLI / Gemini CLI` **本身就是 coding agent**（内部循环：读码→改→运行→看报错→再改）。我把 PRD+测试用例当 spec，**把这一步外包给自主 agent**，放手让它迭代到原型可运行。

> ⚠️ 关键认知：加入一个会调 coding agent 的节点，**并不会让整个系统变成 agent**。判断「是 workflow 还是 agent」看的是**谁控制控制流**。我的控制流仍是写死的，只是某一步把活外包给了一个 agent。所以这是 **hybrid**，而不是「一个 agent」。
>
> 外层 workflow 由我控制，原型那一步该放手时放手给 agent，边界清晰——这比笼统地说「做了个 agent」更准确地描述了系统。

---

## 2. 三条设计原则（直接采用 BEA）

1. **Simplicity（简单优先）**
   不堆叠无谓的复杂度。**本项目选用 LangGraph 编排**——这与 BEA「优先直调 API」的建议有张力，但 BEA 同时要求「若用框架，务必吃透其底层」。我们用它是因为这个 demo 真用得上它的流式事件 / 断点续跑 / 条件循环（理由见 §6.1），且已在 hotel-rag-agent 证明理解其底层。除框架外，节点逻辑仍尽量直白、prompt 可见。

2. **Transparency（透明：暴露思考步骤）**
   每个节点的中间产物（结构化 JSON、调研报告、评审清单、风险分级、**以及 coding agent 的执行轨迹**）全部可见、可导出。不做黑箱。这既是 BEA 原则，也是 demo「不像玩具」的关键——尤其原型节点把 agent 的迭代过程亮出来，反而是看点。

3. **ACI / 好的工具接口（Agent-Computer Interface）**
   各节点的工具输入输出 schema 明确、有文档、有示例。**重点是 ⑧ 对 coding CLI 的调用接口**——怎么把 spec 喂进去、怎么收回结果，BEA 认为打磨这层和打磨 prompt 一样重要。见 §6、§7。

---

## 3. 整体架构与 BEA 模式映射

主链路 = BEA 的 **Prompt Chaining（提示链）**，步与步之间加 **gate（校验门）**；在四个节点嵌入更高级的 BEA 模式：

```
 输入：一句话需求 + 产品场景
   │
   ▼
 ① 需求结构化 ............... [Augmented LLM]              目标用户/痛点/场景/价值假设
   │  └─ gate：信息不足 → 向用户追问(澄清)
   ▼
 ② 竞品 & 资料调研 .......... [Augmented LLM：检索/web]    ★技术亮点1
   │
   ▼
 ③ PRD 草稿生成 ............. [Prompt Chaining 一环]        背景/目标/范围/功能点/边界/指标
   │
   ▼
 ④ 对抗评审 ................. [Parallelization：Sectioning] ★技术亮点2
   │     ├─ critic A：资深PM（目标/指标/优先级）   ┐
   │     ├─ critic B：开发（边界/依赖/可行性）      ├─ 三视角并行撕
   │     └─ critic C：QA（异常流/测试覆盖缺口）     ┘
   ▼
 ⑤ 收敛回填 ................. [Evaluator-Optimizer 循环]
   │     评审问题 → 改写 PRD → 重评 → 直到高危清零 / 迭代≤N
   ▼
 ⑥ 拆解 .................... [Prompt Chaining 一环]        用户故事 + 验收标准 + 测试用例
   │  └─ 这些正好成为 coding agent 的 spec
   ▼
 ⑧ 原型生成 ................ [委托自主 Coding Agent]        ★技术亮点3（hybrid 的灵魂）
   │     调用 Claude Code / Codex / Gemini CLI（headless）
   │     输入：PRD + 用户故事 + 测试用例
   │     agent 内部自主循环：生成 → 运行 → 自检 → 迭代
   ▼
 ⑨ 验收 ................... [Evaluator：ground truth]
   │     原型能否 build/run？是否覆盖验收标准？跑没跑通测试用例？
   ▼
 ⑦ 复盘设计 ................ [Prompt Chaining 一环]        上线指标 + 假设验证方案
   │
   ▼
 输出：全套产物 + 可运行原型 + 一份 evaluation 评分报告
```

> 节点编号 ⑧⑨ 在 ⑥ 之后、⑦ 之前，是为了让「拆解 → 造原型 → 验收」连成一段；⑦复盘收尾。

### 用到的 BEA 模式清单（逐项对照）

| BEA 模式 | 用在哪 | 为什么是它 |
|---|---|---|
| **Augmented LLM**（LLM+检索+工具） | ①②的基础单元 | BEA 的最小积木 |
| **Prompt Chaining** | 整条主链路 | 任务可拆成固定顺序的子步 |
| **Parallelization (Sectioning)** | ④ 对抗评审 | 三个独立视角无依赖，并行、互不污染 |
| **Evaluator-Optimizer** | ⑤ 收敛、⑨ 验收 | 生成→评估→优化的闭环 |
| **委托 Agent（hybrid）** | ⑧ 原型生成 | 路径不可测、有环境反馈(能否跑)、有 ground truth(测试)，正是 agent 的甜区——所以这一步**应该**放手给 agent |
| **Routing**（可选，见 §8） | ① 之后 | 不同需求类型走不同 PRD 模板 |

> 故意**没用**全自主 Orchestrator/Agent 来跑整条链：主流程步骤固定，不需要 LLM 动态规划——这正是 BEA 说的「别为了用而用」。agentic 只用在真正不可预测的 ⑧。

---

## 4. 各节点详细设计（输入 / 输出 / 深度）

### ① 需求结构化　`[Augmented LLM]`
- **输入**：自然语言需求 + 场景（可含用户反馈、会议纪要原文）
- **输出**(JSON)：`目标用户 / 核心痛点 / 使用场景 / 价值假设 / 待澄清问题[]`
- **gate**：关键字段缺失 → 输出澄清问题回给用户，不硬编（透明）
- **深度**：浅。

### ② 竞品 & 资料调研　`[Augmented LLM + web/检索]`　★亮点1
- **输入**：①的结构化需求
- **输出**：竞品现状 / 已有方案 / 差评痛点 / 机会点，带来源
- **技术**：复用已验证的 **fan-out 调研 + 来源核实** workflow 模式
- **深度**：深。展示 **RAG / 检索增强 / 信息综合**。

### ③ PRD 草稿生成　`[Prompt Chaining]`
- **输入**：①②
- **输出**：结构化 PRD（背景 / 目标 / 范围与非范围 / 功能点 / 边界 / 成功指标）
- **深度**：浅。**明确不当卖点**——基座本就会，如实标注（transparency）。

### ④ 对抗评审　`[Parallelization: Sectioning]`　★亮点2
- **输入**：③的 PRD 草稿
- **三个 critic 并行**，各有独立 system prompt 与输出 schema：
  - **A 资深PM**：目标是否清晰？指标可测？优先级合理？范围是否发散？
  - **B 开发**：边界 case？隐藏依赖？技术可行性？跨团队接口？
  - **C QA**：异常流？测试用例缺口？验收标准是否可测？
- **输出**：每个 critic 一份 `问题清单[{问题, 严重度, 证据, 建议}]`
- **深度**：深。展示 **multi-agent / 并行 / 视角隔离**。基座「太好说话」，这里专门让它**撕**——demo 的灵魂之一。

### ⑤ 收敛回填　`[Evaluator-Optimizer]`
- **输入**：③ + ④三份问题清单
- **逻辑**：汇总去重 → 风险分级 → 改写 PRD → **重评** → 循环到「高危清零」或「迭代达上限 N」
- **输出**：终版 PRD + 问题处置记录（改了哪些 / 哪些标「已知风险接受」）
- **深度**：中。展示**闭环优化**。

### ⑥ 拆解　`[Prompt Chaining]`
- **输入**：终版 PRD
- **输出**：用户故事(As a… I want… so that…) + 验收标准 + 测试用例
- **深度**：浅。**但它的输出是 ⑧ 的 spec**，所以格式要为 coding agent 可读而设计（ACI）。

### ⑧ 原型生成　`[委托自主 Coding Agent — hybrid]`　★亮点3
- **输入**：终版 PRD + 用户故事 + 测试用例（来自⑥）
- **做法**：以 **headless / 非交互模式**调用一个 coding CLI agent，把 spec 作为任务 prompt，让它**在一个隔离目录里**自主生成→运行→自检→迭代出原型。
  - Claude Code：`claude -p "<spec>" --output-format json`（print 模式，专为程序化调用设计）
  - Codex CLI：`codex exec`；Gemini CLI 同理
- **输出**：一个可运行原型（建议范围死死框小：单页 UI 或一个 CLI 小程序）+ agent 执行轨迹
- **深度**：深。展示 **agent-of-agents 编排 + ACI 设计**。**这是把 demo 从"会写文档"拉升到"能产出可运行东西"的关键节点。**
- ⚠️ 这是不确定性最高的一步，对策见 §6 的四个坑。

### ⑨ 验收　`[Evaluator：ground truth]`
- **输入**：⑧产出的原型
- **逻辑**：能否 build / run？是否覆盖⑥的验收标准？测试用例跑通几条？
- **输出**：验收报告（通过/不通过 + 缺口）。不通过可回⑧再迭代一轮（带上限）
- **深度**：中。**有 ground truth**（能不能跑），让 ⑧ 从「生成」升级成「可验证的 agent」——这是 BEA 强调的 agent 必备要素。

### ⑦ 复盘设计　`[Prompt Chaining]`
- **输入**：终版 PRD
- **输出**：上线该看的指标 + 价值假设的验证方案（A/B 或埋点设计）
- **深度**：浅。体现产品闭环思维。

### ⊕ Evaluation（贯穿）
- 给 PRD 质量打分（完整性/可测性/边界覆盖）、评审覆盖度、原型验收通过率。
- **深度**：小投入大回报。AI 岗极看重「会不会做评估」，BEA 也强调要有衡量标准。

---

## 5. 状态与数据结构

全流程共享一个 `ProjectState`，每个节点读写、可序列化导出（transparency）：

```jsonc
{
  "raw_input": "...",
  "structured_req": { "target_user": "...", "pain_points": [], "scenarios": [], "value_hypotheses": [], "open_questions": [] },
  "research": { "competitors": [], "opportunities": [], "sources": [] },
  "prd_draft": "...(markdown)",
  "reviews": { "pm": [], "dev": [], "qa": [] },       // 每项 {issue, severity, evidence, suggestion}
  "prd_final": "...(markdown)",
  "issue_log": [],
  "breakdown": { "user_stories": [], "acceptance": [], "test_cases": [] },
  "prototype": { "workdir": "...", "agent_trace": "...", "build_ok": false, "run_ok": false },
  "acceptance_report": { "passed": [], "gaps": [] },
  "retro_plan": { "metrics": [], "validation": [] },
  "eval": { "prd_score": {}, "review_coverage": {}, "prototype_pass_rate": {} }
}
```

---

## 6. 技术选型与四个坑

### 6.1 技术栈（已定型 2026-06-14）

| 维度 | 选型 | 说明 |
|---|---|---|
| 编排框架 | **LangGraph** | 复用 hotel-rag-agent 经验；见下方「为什么选 LangGraph」 |
| 语言 | Python 3.12 | |
| 主模型 | **GLM-5.1**(Z.AI，实测可用；key 开通 5.2 后可升) | `langchain_openai.ChatOpenAI` 指向 Z.AI OpenAI 兼容端点，复用 hotel-rag-agent 的 `src/llm.py` |
| coding agent | **Claude Code**（先） | headless：`claude -p "<spec>" --output-format json`；Codex / Gemini CLI 留作后续对比（三个本机都装了） |
| 后端 | **FastAPI** | 包住 LangGraph，用 SSE 把节点事件流给前端 |
| 前端 | **React + Vite + Tailwind + shadcn/ui + React Flow** | 不用 Streamlit；见 §6.4「前端方案」 |

**GLM 接入（复用 hotel-rag-agent/src/llm.py 模式）：**
```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(
    model="glm-5.2",                              # ⚠️ 确认 Z.AI 上的准确模型串
    openai_api_base="https://api.z.ai/api/paas/v4",
    openai_api_key=os.environ["ZAI_API_KEY"],
    temperature=0.3,
)
```
主链路用默认温度；④评审节点可调高温鼓励挑刺，或换更强档位。

**为什么选 LangGraph（对 BEA 的回应）——选型理由：**
> BEA 确实建议优先直调 API、对框架（含 LangGraph）持谨慎。但 BEA 同时说「若用框架，务必理解其底层代码」。我选 LangGraph 是因为这个 demo 真用得上它三个特性：① `astream_events` 流式事件 → 驱动前端实时图动画；② `checkpointer` 断点续跑 → ⑧ coding agent 那步慢且可能失败，能续跑很关键；③ 条件边/循环 → ⑤收敛、⑨验收的回环用 `add_conditional_edges` 表达最自然。而且我在 hotel-rag-agent 里已证明懂它底层（ReAct 环、ToolNode、MemorySaver），不是黑箱调用。

> 这个取舍把「违背 BEA 建议」翻转成「我读懂了 BEA、并有具体理由地做了取舍」——比无脑用框架强得多。

### 6.2 ⑧原型节点的四个坑（不想清楚 demo 会翻车）

1. **不确定性**：coding agent 非确定、可能失败/超时。
   → **对策**：原型范围死死框小（单页 UI / CLI 小程序）；设迭代上限与超时；准备 fallback（失败就展示「agent 尝试轨迹 + 部分产物」，不阻塞演示）。
2. **交接接口（ACI）**：怎么把 spec 喂给 CLI、怎么收结果。
   → **对策**：用 **headless/print 模式**当子进程调用（`claude -p … --output-format json`）；spec 用⑥的结构化输出，约定清晰格式。这句「我用 headless 模式把 agent 当子进程编排」就是硬实力信号。
3. **验证**：workflow 怎么知道原型是好的。
   → **对策**：让 agent 自己跑起来 / 跑测试，再加 ⑨ 验收节点检查 build/run + 验收标准覆盖。有 ground truth 才算「可验证的 agent」。
4. **成本/延迟**：agentic build 又慢又烧 token。
   → **对策**：demo 没问题，但文档如实标注（transparency）；可缓存一次成功产物用于现场兜底演示。

### 6.4 前端方案（替代 Streamlit）

Streamlit 呆板，且对这个 demo 也不合适——这个 demo 的灵魂是「**看着一条 workflow 一个节点一个节点跑起来**」，Streamlit 给不了这种动态。

**推荐：React Flow 实时流程图**
`React + Vite + Tailwind + shadcn/ui + React Flow(xyflow) + framer-motion`

- 用 **React Flow** 把 workflow 画成一张**可交互节点图**（即 §3 那张图的活版）。
- LangGraph 用 `astream_events` 推「节点开始/结束」事件 → FastAPI 用 **SSE** 转发 → 前端**节点逐个点亮、连线流动**，像看流水线运转。
- **点任一节点**，右侧抽屉展示其产物：结构化 JSON、PRD、④三 critic 评审面板、⑧ coding agent 执行轨迹。
- framer-motion 做过渡动画。

为什么这个对你最值：
1. **一点不呆板**——动态图 + 实时点亮，演示现场极抓眼。
2. **它本身就是架构的可视化**——观者能直接看到 BEA 模式在跑，不用靠口头解释。把 transparency 原则做成了卖点。
3. **和你的前端品味一致**（你那个 Astro flipbook 博客说明你在意视觉）。

代价：比 Streamlit 多花 1~2 天搭 React。门面值这个投入。

**轻量备选**（想省时间）：纯 HTML + Tailwind + Alpine.js，SSE 驱动一个时间线/卡片流。比 Streamlit 干净，但没有 React Flow 那种「活的流程图」冲击力。

---

## 7. 范围与里程碑（控制别做飞 + 永远留可演示底座）

- **M0（主链路骨架，1~2 天）**：①→③→④→⑤→⑥ 打通，硬编一个示例需求，产物打印到控制台。证明链路成立。
- **M1（两个亮点做深，3~5 天）**：②接真实检索；④三 critic 真并行 + ⑤循环；加 evaluation。
- **M2（hybrid 冲刺，3~5 天）**：实现 ⑧ headless 调 coding agent + ⑨ 验收。**这是最出彩也最不稳的一段，单独做，不阻塞主链路。**
- **M3（能演示，2~3 天）**：补⑦复盘、简单前端逐段展示（含 agent 轨迹）、README + 架构图（标注 BEA 模式）。
- **示例锚点**：用**你自己做过的产品**当输入（如 math_app / sassy_timer），从「一句话需求」一路跑到「可运行原型」→ 案例真实、说服力强。

> 🛟 **铁律：永远有个"一定能跑"的底座。** 即使 ⑧ 当天抽风，M0/M1 那条到测试用例的 workflow 必须独立完整可演示。demo 不能把全部赌注压在最不稳的节点上。

**不做（守边界，BEA 的 simplicity）**：
- ❌ 不做评审会议/排期甘特/接 Jira/飞书（社交协作类，偏离亮点）
- ❌ 不把整条链做成自主 agent（步骤固定，无需动态规划）
- ❌ 不追求多产品/多租户/商业化（这是 demo）

---

## 8. 可选增强（有余力再加）

- **Routing**：① 后按需求类型(新功能/体验优化/bugfix)路由到不同 PRD 模板 → 多展示一个 BEA 模式。
- **Memory**：跨需求记住产品历史决策，让评审能说「与上次冲突」——需 grounding 数据，demo 阶段可省。

---

## 9. 这套系统展示了什么

**一套系统，覆盖三层能力：**

1. **产品判断力**：完整产品闭环（需求→评审→指标→复盘），尤其④对抗评审体现「懂 PRD 哪里会出问题」。
2. **Workflow 编排**：完整覆盖 `Prompt Chaining / Parallelization / Evaluator-Optimizer / Augmented LLM / Evaluation` 等模式。
3. **Agent 编排 / hybrid 取舍**：⑧把自主 coding agent 当工具编排，并清晰界定「为什么外层是 workflow、唯独这一步该放手给 agent」——直接对标 Anthropic 工程范式。

**工程上的取舍**：零外部依赖、纯 LLM + 本机 CLI 编排、完全可控、范围收敛，便于稳定交付与演示。

---

## 10. 拍板进度

**已定（2026-06-14）：**
- ✅ 框架：**LangGraph**
- ✅ 主模型：**GLM-5.2**（Z.AI，复用 hotel-rag-agent 接入）
- ✅ coding agent：**先 Claude Code**（headless），Codex/Gemini 留作后续对比
- ✅ 前端方向：**不用 Streamlit** → 推荐 React Flow 实时流程图（§6.4，待你确认这个具体方案）

**还差：**
1. **示例锚点**：用哪个你自己的产品当贯穿案例？(math_app / sassy_timer / 其他) —— M0 需要它当输入，这个还没定
2. **确认前端**：走 §6.4 的 React Flow 方案，还是要更轻的备选？
3. **确认 GLM-5.2 在 Z.AI 的准确模型串**：我按 `glm-5.2` 写，你那边 key 能不能跑这个档？
4. 架构有没有要增删的节点？

定了我就出 M0 骨架（LangGraph StateGraph + GLM 接入 + 节点桩）。

---

## 11. 实现进度（收口 · 2026-06-15）

M0–M4 + 后续增强全部完成并实跑验证（生成侧模型用 **GLM-5.1**；评委侧已异构化，见 M5）。

| 里程碑 | 内容 | 状态 | 验证 |
|---|---|---|---|
| **M0** | LangGraph 主链路 ①③④⑤⑥（结构化→PRD→三视角并行评审→收敛→拆解） | ✅ | 端到端实跑，`sample_run.txt`（后并入 .md） |
| **M1** | ② 真实联网调研（ddgs + sources 净化）+ ⑤ evaluator-optimizer 循环 + ⑤b 评估打分节点 | ✅ | `sample_run_m1.md`，循环实测 12→9 high |
| **M2** | ⑧ headless 调 Claude Code 出可运行原型 + ⑨ ground-truth 验收（hybrid，挂 `--prototype`） | ✅ | `sample_run_m2.md` + `prototype_out/index.html`（24KB，可打开），⑨ coverage 33% |
| **M3** | FastAPI SSE 后端（`server.py`）+ React Flow 实时活图前端（`web/`） | ✅ | 后端 SSE 实测推完整管线含循环 0 error；前端 `npm run build` 通过 |
| **M4** | 重构：③加流程图(mermaid)；⑤b 改三评委并行投票闸门、≥2 fail 回 prd_draft（上限3）；⑧ 改运行时并行多 agent 造模块再合并；⑨ 改三验收评委投票、≥2 fail 回 ⑧（上限3）；两闸门**收敛即止**校准 | ✅ | `sample_run_m4.md`；投票回路实测触发；校准后由「烧满4轮」→「检测到退化(2→3 fail)即止」，省约一半预算 |
| **M5（增强）** | **异构 CLI 评委**：9 评委按视角路由 PM→Claude / DEV→Gemini / QA→Codex，失败回退 GLM（对症局限#2）+ **checkpointer 续跑**：SqliteSaver + `interrupt_before=["prototype"]`，跑到拆解后暂停，`--resume <tid>` / 前端「续跑」按钮从存档造原型、不重跑 PRD（对症局限#6 + 一致性）+ 控制台显示真实裁决模型 | ✅ | 异构实测 Claude/Gemini 真跑、QA 回退 GLM 出现 **2:1 分歧**；续跑实测 `--resume` 直入 ⑧、前面无 ①③ 重跑；玩具图证明 interrupt/resume API |

**运行方式：**
- CLI：`python main.py "需求" [--prototype] [--md out.md]`；默认跑到拆解暂停并打印 `thread_id`，`python main.py --resume <thread_id>` 续跑造原型。
- Web：终端1 `python server.py`（:8000）；终端2 `cd web && npm run dev`（:5173），浏览器打开看活图；关「生成原型」开关→跑到 PRD 停→点「▶ 生成原型（续跑）」。

**产物清单：** `DESIGN.md` / `README.md` / `sample_run_m1.md` / `sample_run_m2.md` / `sample_run_m4.md` / `prototype_out/index.html` / `src/`（state·llm·prompts·nodes·graph·checkpoint）/ `main.py` / `server.py` / `web/`。

**与 BEA 的对照：** Prompt Chaining（主链）、Parallelization-Sectioning（④三 critic、⑤b三评委、⑨三验收评委 均并行扇出）、Evaluator-Optimizer（⑤b→prd_draft、⑨→⑧ 两条投票回路 + 收敛即止）、Augmented LLM（②检索）、委托 Agent / hybrid + Orchestrator-Workers（⑧ planner→并行多 coding agent→merge）、Evaluation（⑨ ground-truth 投票）；并能讲清「为何外层是 workflow、唯独 ⑧ 放手给 agent」。

---

## 12. 踩坑记录（真实 debug）

1. **headless 调 Claude Code 卡在授权、不落盘。** 现象：`agent_ok=true` 但 `index.html` 没生成，trace 显示"需要授权"。三层根因：① `--permission-mode acceptEdits` 对新建文件不够；② 换 `--dangerously-skip-permissions` 仍失败——真因是**长多行 spec 当命令行参数经 Windows `cmd /c` 传递时，换行符把后面的 flag 冲掉了**；③ 解法：**spec 改走 stdin**（`claude -p` 无参时读 stdin），彻底绕开换行/引号/长度问题。另加 `_salvage_html` 兜底（agent 只输出代码不写文件时自动抢救）。
2. **SSE 服务在 revise 回路处崩溃。** 现象：流式推到第 2 轮 `dispatch` 报 `'gbk' codec can't encode '↻'`。根因：`↻` 符号 + Windows 进程 stdout 默认 GBK。CLI 入口（main.py）早已 `sys.stdout.reconfigure("utf-8")`，但 SSE 入口（server.py）漏了同样处理——**同一类坑要在每个进程入口都堵**。
3. **同源评委让循环必然打满预算（且可能越改越差）。** 现象：⑤b 三评委（同一 GLM）几乎总有 ≥2 fail，回路一路撞到 3 轮上限；实测还出现返工后 fail 2→3（越改越差）。根因：评委同源、判断相关，「还有 fail 就返工」的闸门永不满足。解法：闸门改 **收敛即止**——通过 / 到上限 / 较上轮未改善，任一即停，并用 `eval_prev_fail`/`accept_prev_fail` 记上轮票数。实测从「烧满 4 轮」→「第 1 轮检测到退化即止」，省约一半预算。
4. **异构评委的 CLI 旗标各不同 + codex 鉴权坑。** 把评委换成 claude/gemini/codex 三家 CLI 时，headless 旗标不统一：claude 用 `-p`（+写文件时 `--dangerously-skip-permissions`）；gemini 需 `-p "" --skip-trust`（`-p` 不能裸给值 + 跳过「未信任目录」门禁 rc=55）；codex 需 `exec --skip-git-repo-check`。spec 一律走 stdin（复用坑1 的 Windows 修法）。另：本机 codex 配的是 DeepSeek provider、key 返回 401 → 经 `_cli_judge` **自动回退 GLM**，实跑里 QA 那票就是 `glm-fallback(codex)`，证明「缺一家不崩」的降级有效。
5. **控制台"撒谎"。** 进度行原本印 `JUDGE_CLI[lens]`（意图的 CLI），但实际可能回退别的模型——控制台说 `(codex)` 其实跑的是 GLM。修法：进度行改印「委托 X」（意图），调用后若 `model != 意图` 再补印「⚠ 实际由 Y 裁决」，真相只看 `model` 字段。

收口完成。剩余可探索（按价值）：**grounding**（接公司数据 RAG，局限#1，最值钱）、**真人评委 / 真实测试做 ground truth**（局限#2 的根）、Routing（按需求类型走不同 PRD 模板）、Memory（跨需求记住产品历史决策）。完整局限清单见 `README.md`「已知局限与改进方向」。

---

## 13. 两组评委与回退重写数据流（易混点，备查）

图上有**两组**「评委」，角色完全不同，别混：

| 组 | 节点 | 角色 | 触发回退? | 反馈去向 |
|---|---|---|---|---|
| **④ 详评** | `critic_pm/dev/qa` | 找问题（列具体 issue） | 否 | **每轮都**喂给 ⑤ `converge`，converge 照着改写 PRD |
| **⑤b 终审** | `eval_pm/dev/qa` | 投票（pass / fail） | **是**（≥2 票 fail） | 回退时把投 fail 评委的 `reasons` 喂给 ③ `prd_draft` 定向重写 |

**回退重写的完整数据流：**
```
⑤b 里 ≥2 票 fail → eval_gate 判 verdict="revise"（图上变红）
  → 回退到 ③ prd_draft（图上重新"运行中"）
  → prd_draft 带上【prev_prd 上一版 PRD + eval_feedback 投 fail 评委的 reasons】
  → SYS_PRD「针对反馈定向改进，而非从零重写」→ 产出新一版 PRD
  → 再走 ④评审 → ⑤converge → ⑤b投票 → eval_gate …… 直到 放行 / 收敛即止 / 到上限
```

**三个关键区分：**
1. **触发回退**的是 **⑤b 投票评委**（不是 ④ 详评）；④ 的 issue 是**每轮都被 converge 吸收**，不分过不过。
2. **真正"出新一版 PRD"**发生在 **③ prd_draft**（拿反馈定向重写），**不是 converge**。
3. ⑨ 验收回路**同构**：`accept_pm/dev/qa` 投票 → `accept_gate` → revise 回 ⑧ `prototype`（带 accept 的 fail reasons 重造原型），done 则结束。

**诚实补充：** 拿反馈重写**不保证更好**（实测出现过 fail 2→3 越改越差），所以闸门做了「收敛即止」——较上轮无改善就停，不死磕。
