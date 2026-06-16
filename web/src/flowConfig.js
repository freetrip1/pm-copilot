// flowConfig.js —— React Flow 节点/连线静态布局 + SSE→产物字段映射。
// 节点 id 与 LangGraph 节点名一一对应（见 src/graph.py），坐标固定从左到右分列。
//
// M4 新图：
//   prd_draft →(三 critic)→ converge →(三 eval)→ eval_gate
//   eval_gate 虚线回 prd_draft（revise）/ 实线 → breakdown
//   breakdown → prototype →(三 accept)→ accept_gate
//   accept_gate 虚线回 prototype（revise）/ 实线 → END
// 已删除旧 dispatch、evaluate 节点。

// 列 x 坐标（从左到右）
const COL = {
  structure: 0,
  research: 240,
  prd_draft: 480,
  critic: 720,
  converge: 960,
  eval: 1200, // 五b 终审三视角
  evalGate: 1440, // 五b 闸门
  branch: 1680, // breakdown
  proto: 1920, // prototype
  accept: 2160, // 九 验收三视角
  acceptGate: 2400, // 九 闸门
};

// 节点定义：id / 中文 label / 像素坐标 / 是否原型专属（默认置灰）
export const NODE_DEFS = [
  { id: "structure", label: "① 需求结构化", x: COL.structure, y: 220 },
  { id: "research", label: "② 竞品调研", x: COL.research, y: 220 },
  { id: "prd_draft", label: "③ PRD草稿+流程图", x: COL.prd_draft, y: 220 },
  { id: "critic_pm", label: "④ 产品评审", x: COL.critic, y: 60 },
  { id: "critic_dev", label: "④ 开发评审", x: COL.critic, y: 220 },
  { id: "critic_qa", label: "④ QA评审", x: COL.critic, y: 380 },
  { id: "converge", label: "⑤ 收敛", x: COL.converge, y: 220 },
  // 五b 终审·三视角（竖排）
  { id: "eval_pm", label: "⑤b 终审·产品", x: COL.eval, y: 60 },
  { id: "eval_dev", label: "⑤b 终审·开发", x: COL.eval, y: 220 },
  { id: "eval_qa", label: "⑤b 终审·QA", x: COL.eval, y: 380 },
  { id: "eval_gate", label: "⑤b 闸门", x: COL.evalGate, y: 220 },
  { id: "breakdown", label: "⑥ 拆解", x: COL.branch, y: 220 },
  { id: "prototype", label: "⑧ 原型", x: COL.proto, y: 220, protoOnly: true },
  // 九 验收·三视角（竖排）
  { id: "accept_pm", label: "⑨ 验收·产品", x: COL.accept, y: 60, protoOnly: true },
  { id: "accept_dev", label: "⑨ 验收·开发", x: COL.accept, y: 220, protoOnly: true },
  { id: "accept_qa", label: "⑨ 验收·QA", x: COL.accept, y: 380, protoOnly: true },
  { id: "accept_gate", label: "⑨ 闸门", x: COL.acceptGate, y: 220, protoOnly: true },
];

// 连线定义。dashed=true 表示虚线（revise 回路）；label 可选。
// 每条边带稳定 id（手写、与 source/target 绑定），避免过滤后用数组 index 拼 id
// 导致边 id 漂移 → React Flow 边重挂载 / 视图跑飞。
const RAW_EDGES = [
  { source: "structure", target: "research" },
  { source: "research", target: "prd_draft" },
  // prd_draft 直接扇出三 critic
  { source: "prd_draft", target: "critic_pm" },
  { source: "prd_draft", target: "critic_dev" },
  { source: "prd_draft", target: "critic_qa" },
  // 三 critic 汇入 converge
  { source: "critic_pm", target: "converge" },
  { source: "critic_dev", target: "converge" },
  { source: "critic_qa", target: "converge" },
  // converge 扇出三终审评委
  { source: "converge", target: "eval_pm" },
  { source: "converge", target: "eval_dev" },
  { source: "converge", target: "eval_qa" },
  // 三终审汇入 eval_gate
  { source: "eval_pm", target: "eval_gate" },
  { source: "eval_dev", target: "eval_gate" },
  { source: "eval_qa", target: "eval_gate" },
  // eval_gate done 分支（实线）
  { source: "eval_gate", target: "breakdown" },
  // eval_gate revise 回路（虚线，回 prd_draft）
  {
    source: "eval_gate",
    target: "prd_draft",
    dashed: true,
    label: "revise 循环",
  },
  // 原型分支（仅 prototype 模式点亮）
  { source: "breakdown", target: "prototype", protoOnly: true },
  // prototype 扇出三验收评委
  { source: "prototype", target: "accept_pm", protoOnly: true },
  { source: "prototype", target: "accept_dev", protoOnly: true },
  { source: "prototype", target: "accept_qa", protoOnly: true },
  // 三验收汇入 accept_gate
  { source: "accept_pm", target: "accept_gate", protoOnly: true },
  { source: "accept_dev", target: "accept_gate", protoOnly: true },
  { source: "accept_qa", target: "accept_gate", protoOnly: true },
  // accept_gate revise 回路（虚线，回 prototype）
  {
    source: "accept_gate",
    target: "prototype",
    dashed: true,
    label: "revise 返工",
    protoOnly: true,
  },
];

export const EDGE_DEFS = RAW_EDGES.map((e) => ({
  ...e,
  // 稳定 id：source→target(+r 表示 revise 回路)，永不随过滤结果变化。
  id: `e-${e.source}-${e.target}${e.dashed ? "-r" : ""}`,
}));

// 每个节点 running 时的「它在做什么」一句话中文描述（存活感用）。
export const NODE_BUSY_DESC = {
  structure: "正在拆解需求要点…",
  research: "正在检索竞品资料…",
  prd_draft: "正在起草 PRD 与流程图…",
  critic_pm: "正在调用模型评审（产品视角）…",
  critic_dev: "正在调用模型评审（开发视角）…",
  critic_qa: "正在调用模型评审（QA 视角）…",
  converge: "正在收敛评审意见…",
  eval_pm: "正在调用模型终审（产品）…",
  eval_dev: "正在调用模型终审（开发）…",
  eval_qa: "正在调用模型终审（QA）…",
  eval_gate: "正在统计终审投票…",
  breakdown: "正在拆解用户故事/用例…",
  prototype: "正在生成原型（耗时较长）…",
  accept_pm: "正在调用模型验收（产品）…",
  accept_dev: "正在调用模型验收（开发）…",
  accept_qa: "正在调用模型验收（QA）…",
  accept_gate: "正在汇总验收结论…",
};

// SSE→产物字段映射：node id → [{ key, kind(json|md|mermaid), title }]
export const FIELD_MAP = {
  structure: [{ key: "structured_req", kind: "json", title: "结构化需求" }],
  research: [{ key: "research", kind: "json", title: "竞品调研" }],
  prd_draft: [
    { key: "prd_draft", kind: "md", title: "PRD 草稿" },
    { key: "prd_flowchart", kind: "mermaid", title: "流程图（mermaid 源码）" },
  ],
  critic_pm: [{ key: "review_pm", kind: "json", title: "产品评审意见" }],
  critic_dev: [{ key: "review_dev", kind: "json", title: "开发评审意见" }],
  critic_qa: [{ key: "review_qa", kind: "json", title: "QA 评审意见" }],
  converge: [{ key: "prd_final", kind: "md", title: "终版 PRD" }],
  eval_pm: [{ key: "eval_pm", kind: "json", title: "终审投票·产品" }],
  eval_dev: [{ key: "eval_dev", kind: "json", title: "终审投票·开发" }],
  eval_qa: [{ key: "eval_qa", kind: "json", title: "终审投票·QA" }],
  eval_gate: [{ key: "eval_gate", kind: "json", title: "终审闸门" }],
  breakdown: [{ key: "breakdown", kind: "json", title: "拆解（故事/用例）" }],
  prototype: [{ key: "prototype", kind: "json", title: "原型产物" }],
  accept_pm: [{ key: "accept_pm", kind: "json", title: "验收投票·产品" }],
  accept_dev: [{ key: "accept_dev", kind: "json", title: "验收投票·开发" }],
  accept_qa: [{ key: "accept_qa", kind: "json", title: "验收投票·QA" }],
  accept_gate: [{ key: "acceptance_report", kind: "json", title: "验收报告" }],
};
