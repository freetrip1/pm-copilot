// StageNode.jsx —— React Flow 自定义节点。按运行状态着色。
// status: pending(灰) / running(蓝, 脉冲) / done(绿) / fail(玫红, 未通过/返工) / awaiting(琥珀虚线, 待续跑)。
// protoOnly 且未运行时额外置灰描边。
// running 时显示存活感：脉冲 + 走动的「已运行 Xs」计时 + 一句正在做什么的中文描述。
import { useEffect, useState } from "react";
import { Handle, Position } from "@xyflow/react";

const STATUS_STYLE = {
  pending: "bg-slate-800 border-slate-600 text-slate-300",
  running: "bg-blue-600 border-blue-300 text-white animate-pulse shadow-lg shadow-blue-500/40",
  done: "bg-emerald-600 border-emerald-300 text-white shadow-lg shadow-emerald-500/30",
  fail: "bg-rose-600 border-rose-300 text-white shadow-lg shadow-rose-500/40",
  awaiting:
    "bg-amber-900/40 border-amber-400 border-dashed text-amber-200 shadow-lg shadow-amber-500/20",
};

const STATUS_DOT = {
  pending: "bg-slate-500",
  running: "bg-blue-200",
  done: "bg-emerald-200",
  fail: "bg-rose-200",
  awaiting: "bg-amber-300 animate-pulse",
};

// running 计时小钟：每秒 +1，从 startedAt 起算。
function useElapsed(active, startedAt) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
  if (!active || !startedAt) return 0;
  return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
}

export default function StageNode({ data }) {
  const { label, status = "pending", protoOnly, dimmed, startedAt, busyDesc } = data;
  const running = status === "running";
  const elapsed = useElapsed(running, startedAt);

  const base =
    "relative rounded-xl border px-4 py-3 text-sm font-medium select-none transition-colors duration-300 min-w-[130px] text-center cursor-pointer";
  const cls = STATUS_STYLE[status] || STATUS_STYLE.pending;
  const ghost = protoOnly && dimmed ? "opacity-40" : "";

  return (
    <div className={`${base} ${cls} ${ghost}`}>
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />
      <div className="flex items-center justify-center gap-2">
        <span className={`h-2 w-2 rounded-full ${STATUS_DOT[status]}`} />
        <span>{label}</span>
      </div>
      {running && (
        <div className="mt-1.5 space-y-0.5">
          <div className="flex items-center justify-center gap-1.5 text-[11px] font-semibold text-blue-100">
            <span className="inline-block h-1.5 w-1.5 animate-ping rounded-full bg-blue-200" />
            已运行 {elapsed}s
          </div>
          {busyDesc && (
            <div className="text-[10px] font-normal leading-tight text-blue-200/90">
              {busyDesc}
            </div>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
}
