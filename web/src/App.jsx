// App.jsx —— PM Copilot 主业务页。
// 顶部：需求输入 + 运行 + 原型开关。主体：React Flow 节点图。右侧：节点产物抽屉。
//
// API base：import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000"
//   GET /api/health                                    → {"ok": true}
//   GET /api/run?requirement=<urlencoded>&prototype=…  → SSE（EventSource）
//
// SSE 事件协议（server.py）：
//   {type:"start", thread_id} | {type:"node", node, data}
//   {type:"paused", thread_id}  ← 跑到 prototype 前中断（关原型开关时）
//   {type:"done", thread_id} | {type:"error", message}
// 节点状态机：pending →(收到本节点 node 事件)→ done；事件到来前把它标 running，落定后转 done。
// 续跑：收到 paused 保存 thread_id，展示「▶ 生成原型（续跑）」按钮，
//       点按钮 EventSource 连 /api/resume?thread_id=… 续跑 ⑧/⑨。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import StageNode from "./StageNode.jsx";
import Drawer from "./Drawer.jsx";
import { NODE_DEFS, EDGE_DEFS, NODE_BUSY_DESC } from "./flowConfig.js";

// id → 中文 label 快查（运行记录/全局提示用）。
const LABEL_BY_ID = Object.fromEntries(NODE_DEFS.map((n) => [n.id, n.label]));
// 续跑阶段会点亮的 proto 节点集合（用于 protoActive 派生判断）。
const PROTO_IDS = new Set(NODE_DEFS.filter((n) => n.protoOnly).map((n) => n.id));

const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";
const nodeTypes = { stage: StageNode };

// 节点执行先后顺序（用于把「下一个待跑节点」预置为 running，制造流动观感）。
// 与 src/graph.py 节点名严格对齐：删 dispatch/evaluate，新增三 eval 评委、
// eval_gate、三 accept 评委、accept_gate（同列并行节点按相邻排布）。
const ORDER = [
  "structure",
  "research",
  "prd_draft",
  "critic_pm",
  "critic_dev",
  "critic_qa",
  "converge",
  "eval_pm",
  "eval_dev",
  "eval_qa",
  "eval_gate",
  "breakdown",
  "prototype",
  "accept_pm",
  "accept_dev",
  "accept_qa",
  "accept_gate",
];

// 续跑阶段节点（paused 后这些标记为「待续跑」，由按钮点亮）
const RESUME_NODES = [
  "prototype",
  "accept_pm",
  "accept_dev",
  "accept_qa",
  "accept_gate",
];

// 评委节点集合：状态按自己投的票上色（verdict pass/fail）。
const JUDGE_IDS = new Set([
  "eval_pm",
  "eval_dev",
  "eval_qa",
  "accept_pm",
  "accept_dev",
  "accept_qa",
]);
// 闸门节点：状态按判定上色（verdict revise/done）。
const GATE_IDS = new Set(["eval_gate", "accept_gate"]);

// 闸门 → revise 时回退目标 + 本回路要重跑（重置回 pending）的节点。
const REVISE_TARGET = {
  eval_gate: {
    back: "prd_draft", // 回退目标，重新置 running
    reset: ["critic_pm", "critic_dev", "critic_qa", "converge", "eval_pm", "eval_dev", "eval_qa"],
  },
  accept_gate: {
    back: "prototype",
    reset: ["accept_pm", "accept_dev", "accept_qa"],
  },
};

// 判定某节点收到 node 事件后应落到的最终状态：
// 评委看 data[id].verdict（pass→done / fail→fail）；
// 闸门看 data[id].verdict（done→done / revise→fail）；
// 其它节点一律 'done'。
function settleStatusFor(id, data) {
  const v =
    data && typeof data === "object" && data[id] && typeof data[id] === "object"
      ? data[id].verdict
      : undefined;
  if (JUDGE_IDS.has(id)) return v === "fail" ? "fail" : "done";
  if (GATE_IDS.has(id)) return v === "revise" ? "fail" : "done";
  return "done";
}

// 取闸门 verdict（用于回路重启判定与时间线标注）。
function gateVerdict(id, data) {
  if (!GATE_IDS.has(id)) return undefined;
  return data && typeof data === "object" && data[id] && typeof data[id] === "object"
    ? data[id].verdict
    : undefined;
}

export default function App() {
  const [requirement, setRequirement] = useState("");
  const [prototype, setPrototype] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("idle"); // idle | running | paused | done | error
  const [pausedThreadId, setPausedThreadId] = useState(null); // paused 时保存以便续跑

  // 每节点：{ status, data }。selectedId 控制抽屉。
  const [statuses, setStatuses] = useState({}); // id -> "pending"|"running"|"done"
  const [payloads, setPayloads] = useState({}); // id -> data dict
  const [selectedId, setSelectedId] = useState(null);

  // 节点变 running 的起始时间戳（id -> ms），用于节点上的「已运行 Xs」走表。
  const [startedAt, setStartedAt] = useState({});
  // 运行记录（append-only）：每收到一次 node 事件就追加一条，回路会让同名节点多次出现。
  // 每条：{ seq, id, label, occurrence(该节点第几次), t(相对开始的秒) }
  const [timeline, setTimeline] = useState([]);
  // 本次运行的开始时间（算相对耗时）。
  const runStartRef = useRef(0);

  const esRef = useRef(null);

  // 卸载时关闭 SSE
  useEffect(() => () => esRef.current?.close(), []);

  // 把某些 id 标 running 并记录其起始时间（仅当之前不是 running）。
  const markRunning = useCallback((ids) => {
    setStartedAt((prev) => {
      const next = { ...prev };
      const now = Date.now();
      ids.forEach((id) => {
        if (next[id] == null) next[id] = now;
      });
      return next;
    });
  }, []);

  const resetRun = useCallback(() => {
    const init = {};
    NODE_DEFS.forEach((n) => (init[n.id] = "pending"));
    setStatuses(init);
    setPayloads({});
    setError("");
    setPausedThreadId(null);
    setTimeline([]);
    setStartedAt({});
    runStartRef.current = Date.now();
  }, []);

  // 公共 node 事件处理：追加运行记录 + 写产物 + 点亮下一个待跑节点
  const onNodeEvent = useCallback(
    (msg) => {
      const id = msg.node;
      if (!id) return;
      const label = LABEL_BY_ID[id] || id;

      const data = msg.data;
      // 本节点应落到的最终状态（评委按投票 / 闸门按判定 / 其它 done）。
      const settled = settleStatusFor(id, data);
      // 闸门 revise 判定：要在图上重启回路。
      const verdict = gateVerdict(id, data);
      const isReviseGate = GATE_IDS.has(id) && verdict === "revise";

      // 1) append-only 运行记录：不去重，回路重复节点分别成行。
      //    闸门记录后缀标注判定（返工/通过）。
      setTimeline((tl) => {
        const occurrence = tl.filter((r) => r.id === id).length + 1;
        const t = runStartRef.current
          ? Math.max(0, Math.round((Date.now() - runStartRef.current) / 1000))
          : 0;
        const note = GATE_IDS.has(id)
          ? verdict === "revise"
            ? "返工"
            : verdict === "done"
            ? "通过"
            : undefined
          : undefined;
        return [...tl, { seq: tl.length + 1, id, label, occurrence, t, note }];
      });

      // 2) 写产物：防御式 merge，msg.data 不是对象也不崩
      setPayloads((p) => {
        const incoming =
          data && typeof data === "object" && !Array.isArray(data) ? data : {};
        return { ...p, [id]: { ...(p[id] || {}), ...incoming } };
      });

      // 3) 状态机：
      //    - 评委/闸门按 settled（done 绿 / fail 红）落定；其它节点 done。
      //    - 闸门 revise：把回退目标重新置 running、本回路重跑节点重置 pending，
      //      不往前预点亮（revise 时不该往前走）。
      //    - 否则按 ORDER 预点亮下一个 pending 节点（含闸门 done）。
      let nextRunningId = null;
      setStatuses((s) => {
        const next = { ...s, [id]: settled };
        if (isReviseGate) {
          const cfg = REVISE_TARGET[id];
          if (cfg) {
            // 回退目标重新「运行中」。
            next[cfg.back] = "running";
            nextRunningId = cfg.back;
            // 本回路会重跑的节点重置回 pending，等新事件重新点亮。
            cfg.reset.forEach((rid) => {
              next[rid] = "pending";
            });
          }
        } else {
          const idx = ORDER.indexOf(id);
          for (let k = idx + 1; k < ORDER.length; k++) {
            if (next[ORDER[k]] === "pending") {
              next[ORDER[k]] = "running";
              nextRunningId = ORDER[k];
              break;
            }
          }
        }
        return next;
      });
      // revise 回退时，回退目标重新计时：先清掉旧起点再重新打点。
      if (isReviseGate) {
        const cfg = REVISE_TARGET[id];
        if (cfg) {
          setStartedAt((prev) => {
            const n = { ...prev };
            cfg.reset.forEach((rid) => delete n[rid]);
            delete n[cfg.back]; // 清旧起点，下面 markRunning 重新打点
            return n;
          });
        }
      }
      if (nextRunningId) markRunning([nextRunningId]);
    },
    [markRunning]
  );

  // 收尾：把残留 running 复位 pending，并清掉它们的计时起点（停表）。
  const settleRunning = useCallback(() => {
    setStatuses((s) => {
      const stale = [];
      const next = { ...s };
      Object.keys(next).forEach((k) => {
        if (next[k] === "running") {
          next[k] = "pending";
          stale.push(k);
        }
      });
      if (stale.length) {
        setStartedAt((prev) => {
          const n = { ...prev };
          stale.forEach((k) => delete n[k]);
          return n;
        });
      }
      return next;
    });
  }, []);

  const handleRun = useCallback(() => {
    const req = requirement.trim();
    if (!req || running) return;
    esRef.current?.close();
    resetRun();
    setRunning(true);
    setPhase("running");

    const url = `${API_BASE}/api/run?requirement=${encodeURIComponent(
      req
    )}&prototype=${prototype ? "true" : "false"}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "start") {
        setStatuses((s) => ({ ...s, structure: "running" }));
        markRunning(["structure"]);
      } else if (msg.type === "node") {
        onNodeEvent(msg);
      } else if (msg.type === "paused") {
        // 跑到 prototype 前暂停：停止运行态，保存 thread_id，等待按钮续跑
        es.close();
        setRunning(false);
        setPhase("paused");
        setPausedThreadId(msg.thread_id || null);
        // 把续跑阶段节点标为「待续跑」，其余残留 running 复位（并停表）
        setStartedAt({});
        setStatuses((s) => {
          const next = { ...s };
          Object.keys(next).forEach((k) => {
            if (next[k] === "running") next[k] = "pending";
          });
          RESUME_NODES.forEach((id) => {
            if (next[id] === "pending") next[id] = "awaiting";
          });
          return next;
        });
      } else if (msg.type === "done") {
        es.close();
        setRunning(false);
        setPhase("done");
        settleRunning();
      } else if (msg.type === "error") {
        es.close();
        setRunning(false);
        setPhase("error");
        setError(msg.message || "运行出错");
      }
    };

    es.onerror = () => {
      es.close();
      setRunning(false);
      setPhase((p) => (p === "done" || p === "paused" ? p : "error"));
      setError((prev) => prev || "连接中断（请确认后端 127.0.0.1:8000 已启动）");
    };
  }, [requirement, prototype, running, resetRun, onNodeEvent, settleRunning, markRunning]);

  // 续跑：连 /api/resume?thread_id=… 续跑 ⑧/⑨
  const handleResume = useCallback(() => {
    if (!pausedThreadId || running) return;
    esRef.current?.close();
    setRunning(true);
    setPhase("running");
    setError("");
    // 点亮 prototype 为 running，accept 系仍 awaiting
    setStatuses((s) => ({ ...s, prototype: "running" }));
    markRunning(["prototype"]);

    const url = `${API_BASE}/api/resume?thread_id=${encodeURIComponent(
      pausedThreadId
    )}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.onmessage = (e) => {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg.type === "start") {
        setStatuses((s) => ({ ...s, prototype: "running" }));
        markRunning(["prototype"]);
      } else if (msg.type === "node") {
        onNodeEvent(msg);
      } else if (msg.type === "done") {
        es.close();
        setRunning(false);
        setPhase("done");
        setPausedThreadId(null);
        settleRunning();
      } else if (msg.type === "error") {
        es.close();
        setRunning(false);
        setPhase("error");
        setError(msg.message || "续跑出错");
      }
    };

    es.onerror = () => {
      es.close();
      setRunning(false);
      setPhase((p) => (p === "done" ? p : "error"));
      setError((prev) => prev || "续跑连接中断（请确认后端已启动）");
    };
  }, [pausedThreadId, running, onNodeEvent, settleRunning, markRunning]);

  // 派生标志：proto 分支是否「激活」。
  // 关原型开关后再续跑，prototype 仍为 false，但 proto 节点已被点亮（status 非 pending）。
  // 用 protoActive 取代裸 prototype 来决定 dimming 与边过滤，使续跑后的 proto 节点/边
  // 正常显示并【持续保留】，根治「原型生成完节点消失」。
  const protoActive = useMemo(() => {
    if (prototype) return true;
    for (const id of PROTO_IDS) {
      const st = statuses[id];
      if (st && st !== "pending") return true; // running / awaiting / done 任一即激活
    }
    return false;
  }, [prototype, statuses]);

  // 是否已生成可打开的原型：⑧ prototype 节点 done，或其产物里 index_exists 为真。
  const prototypeReady = useMemo(() => {
    if (statuses["prototype"] === "done") return true;
    const p = payloads["prototype"];
    return !!(p && (p.index_exists || (p.prototype && p.prototype.index_exists)));
  }, [statuses, payloads]);

  // 构建 React Flow 节点。17 个节点恒定渲染，绝不因状态/payload 而消失。
  const rfNodes = useMemo(
    () =>
      NODE_DEFS.map((n) => {
        const status = statuses[n.id] || "pending";
        return {
          id: n.id,
          type: "stage",
          position: { x: n.x, y: n.y },
          data: {
            label: n.label,
            status,
            protoOnly: n.protoOnly,
            // proto 节点仅在「未激活」时置灰；激活后（含续跑完成）正常显示。
            dimmed: n.protoOnly && !protoActive,
            startedAt: startedAt[n.id],
            busyDesc: status === "running" ? NODE_BUSY_DESC[n.id] : undefined,
          },
        };
      }),
    [statuses, protoActive, startedAt]
  );

  // 构建 React Flow 连线。proto 边在「未激活」时整体淡出但仍渲染（保留稳定 id），
  // 激活后正常显示；edge id 来自 EDGE_DEFS（稳定、不随过滤漂移）。
  const rfEdges = useMemo(
    () =>
      EDGE_DEFS.map((e) => {
        const dim = e.protoOnly && !protoActive;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.label,
          animated: e.dashed,
          hidden: e.protoOnly && !protoActive,
          style: {
            stroke: e.dashed ? "#f59e0b" : "#94a3b8",
            strokeWidth: 1.5,
            strokeDasharray: e.dashed ? "6 4" : undefined,
            opacity: dim ? 0.35 : 1,
          },
          labelStyle: { fill: "#b45309", fontSize: 11, fontWeight: 600 },
          labelBgStyle: { fill: "#fffbeb" },
          markerEnd: { type: MarkerType.ArrowClosed, color: e.dashed ? "#f59e0b" : "#94a3b8" },
          type: "smoothstep",
        };
      }),
    [protoActive]
  );

  const onNodeClick = useCallback(
    (_evt, node) => setSelectedId(node.id),
    []
  );

  const selectedNode = useMemo(() => {
    if (!selectedId) return null;
    const def = NODE_DEFS.find((n) => n.id === selectedId);
    if (!def) return null;
    return {
      id: def.id,
      label: def.label,
      status: statuses[def.id] || "pending",
      data: payloads[def.id] || {},
    };
  }, [selectedId, statuses, payloads]);

  // 当前 running 的节点 label 列表（全局提示用）。
  const runningList = useMemo(
    () =>
      NODE_DEFS.filter((n) => statuses[n.id] === "running").map((n) => ({
        id: n.id,
        label: n.label,
        startedAt: startedAt[n.id],
      })),
    [statuses, startedAt]
  );

  return (
    <div className="flex h-screen flex-col bg-slate-100 text-slate-800">
      {/* 顶部栏 */}
      <header className="z-10 border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-3 px-6 py-3">
          <h1 className="mr-2 text-lg font-bold tracking-tight">
            PM <span className="text-indigo-600">Copilot</span>
          </h1>
          <input
            value={requirement}
            onChange={(e) => setRequirement(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRun()}
            placeholder="用一句话描述你的产品需求…"
            disabled={running}
            className="min-w-[260px] flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:bg-slate-50"
          />
          <label
            className="flex cursor-pointer select-none items-center gap-2 text-sm text-slate-600"
            title="开：运行时一气呵成生成原型；关：跑到原型前暂停，再由「续跑」按钮生成"
          >
            <input
              type="checkbox"
              checked={prototype}
              onChange={(e) => setPrototype(e.target.checked)}
              disabled={running}
              className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-400"
            />
            生成原型
            <span className="font-mono text-xs text-slate-400">
              {prototype ? "一气呵成" : "暂停后续跑"}
            </span>
          </label>
          <button
            onClick={handleRun}
            disabled={running || !requirement.trim()}
            className="rounded-lg bg-indigo-600 px-5 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {running ? "运行中…" : "运行"}
          </button>
          {phase === "paused" && pausedThreadId && (
            <button
              onClick={handleResume}
              disabled={running}
              className="animate-pulse rounded-lg bg-amber-500 px-5 py-2 text-sm font-bold text-white shadow-md shadow-amber-500/40 transition-colors hover:bg-amber-600 disabled:cursor-not-allowed disabled:bg-slate-300"
              title={`续跑 thread ${pausedThreadId}`}
            >
              ▶ 生成原型（续跑）
            </button>
          )}
          {prototypeReady && (
            <a
              href={`${API_BASE}/api/prototype`}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-bold text-white no-underline shadow-md shadow-emerald-500/40 transition-colors hover:bg-emerald-700"
              title="在新标签页打开并运行生成的原型"
            >
              🔗 打开并运行原型
            </a>
          )}
        </div>
        {/* 状态条 */}
        <StatusBar phase={phase} error={error} />
        {/* 全局「正在运行」提示行 */}
        <RunningBanner running={running} runningList={runningList} />
      </header>

      {/* 画布 */}
      <main className="relative flex-1">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.18 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          className="bg-slate-100"
        >
          <Background color="#cbd5e1" gap={20} />
          <Controls showInteractive={false} />
        </ReactFlow>

        {/* 图例 */}
        <Legend />

        {/* 运行记录 / 时间线面板（画布底部，可折叠可滚动，默认展开） */}
        <TimelinePanel timeline={timeline} onPick={setSelectedId} />
      </main>

      {/* 右侧抽屉 */}
      <Drawer node={selectedNode} onClose={() => setSelectedId(null)} />
    </div>
  );
}

function StatusBar({ phase, error }) {
  if (phase === "idle") return null;
  const map = {
    running: { txt: "流程运行中…", cls: "bg-blue-50 text-blue-700 border-blue-100" },
    paused: {
      txt: "已暂停在原型前（PRD 已审完存档）— 点击「▶ 生成原型（续跑）」从此处续跑，不重跑 PRD",
      cls: "bg-amber-50 text-amber-700 border-amber-100",
    },
    done: { txt: "流程已完成 ✓ 点击任一节点查看产物", cls: "bg-emerald-50 text-emerald-700 border-emerald-100" },
    error: { txt: `出错：${error}`, cls: "bg-rose-50 text-rose-700 border-rose-100" },
  };
  const m = map[phase];
  if (!m) return null;
  return (
    <div className={`border-t px-6 py-1.5 text-xs ${m.cls}`}>{m.txt}</div>
  );
}

function Legend() {
  const items = [
    { c: "bg-slate-400", t: "待运行" },
    { c: "bg-blue-500", t: "运行中" },
    { c: "bg-emerald-500", t: "已完成" },
    { c: "bg-rose-600", t: "未通过/返工" },
  ];
  return (
    <div className="pointer-events-none absolute bottom-4 right-4 z-10 rounded-lg border border-slate-200 bg-white/90 px-3 py-2 text-xs shadow-sm backdrop-blur">
      <div className="mb-1 font-semibold text-slate-500">节点状态</div>
      <div className="flex gap-3">
        {items.map((it) => (
          <span key={it.t} className="flex items-center gap-1 text-slate-600">
            <span className={`h-2.5 w-2.5 rounded-full ${it.c}`} />
            {it.t}
          </span>
        ))}
      </div>
      <div className="mt-1.5 flex items-center gap-1 text-slate-500">
        <span className="inline-block h-0 w-5 border-t-2 border-dashed border-amber-500" />
        revise 循环
      </div>
    </div>
  );
}

// 每秒重渲染，用于走表显示。返回当前 tick 计数（值本身无意义）。
function useTick(active) {
  const [, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setN((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
}

// 全局「正在运行」提示行：列出当前 running 的节点 label 及各自 Xs。
function RunningBanner({ running, runningList }) {
  useTick(running && runningList.length > 0);
  if (!running || runningList.length === 0) return null;
  const now = Date.now();
  const secs = (ts) => (ts ? Math.max(0, Math.floor((now - ts) / 1000)) : 0);
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-blue-100 bg-blue-50/70 px-6 py-1.5 text-xs text-blue-700">
      <span className="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-blue-500" />
      <span className="font-semibold">
        正在运行（{runningList.length}）：
      </span>
      {runningList.map((r) => (
        <span
          key={r.id}
          className="rounded-full bg-white/80 px-2 py-0.5 font-medium shadow-sm"
        >
          {r.label}（{secs(r.startedAt)}s）
        </span>
      ))}
    </div>
  );
}

// 运行记录 / 时间线面板：按到达顺序 append-only 记录每一次 node 事件。
// 同一节点多次出现分别成行（不去重），并标注「第 N 次」，让用户数清评审走了几轮。
function TimelinePanel({ timeline, onPick }) {
  const [open, setOpen] = useState(true);
  const total = timeline.length;
  return (
    <div className="absolute bottom-4 left-4 z-10 w-[300px] max-w-[80vw] overflow-hidden rounded-lg border border-slate-200 bg-white/95 text-xs shadow-md backdrop-blur">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between border-b border-slate-100 px-3 py-2 text-left font-semibold text-slate-600 hover:bg-slate-50"
      >
        <span>运行记录 · 共 {total} 步</span>
        <span className="text-slate-400">{open ? "▾ 收起" : "▸ 展开"}</span>
      </button>
      {open && (
        <div className="max-h-[40vh] overflow-y-auto px-1.5 py-1.5">
          {total === 0 ? (
            <p className="px-2 py-3 text-center text-slate-400">
              运行后这里按到达顺序记录每一步
              <br />
              （回路会让同一节点重复出现）
            </p>
          ) : (
            <ol className="space-y-0.5">
              {timeline.map((r) => (
                <li key={r.seq}>
                  <button
                    onClick={() => onPick(r.id)}
                    className="flex w-full items-baseline gap-2 rounded px-2 py-1 text-left hover:bg-indigo-50"
                    title="点击查看该节点产物"
                  >
                    <span className="w-5 shrink-0 text-right font-mono text-slate-400">
                      {r.seq}
                    </span>
                    <span className="flex-1 truncate text-slate-700">
                      {r.label}
                      {r.occurrence > 1 && (
                        <span className="ml-1 rounded bg-amber-100 px-1 font-semibold text-amber-700">
                          第{r.occurrence}次
                        </span>
                      )}
                      {r.note && (
                        <span
                          className={`ml-1 rounded px-1 font-semibold ${
                            r.note === "返工"
                              ? "bg-rose-100 text-rose-700"
                              : "bg-emerald-100 text-emerald-700"
                          }`}
                        >
                          判定：{r.note}
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 font-mono text-[10px] text-slate-400">
                      {r.t}s
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}
