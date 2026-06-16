// Drawer.jsx —— 右侧抽屉：展示某节点的产物。
// 按 FIELD_MAP 取字段：md 用 <Markdown> 渲染，json 用 <pre> 美化。
import Markdown from "./Markdown.jsx";
import { FIELD_MAP } from "./flowConfig.js";

const STATUS_LABEL = {
  pending: "未开始",
  running: "运行中",
  done: "已完成",
  fail: "未通过/返工",
  awaiting: "待续跑",
};
const STATUS_BADGE = {
  pending: "bg-slate-100 text-slate-500",
  running: "bg-blue-100 text-blue-700",
  done: "bg-emerald-100 text-emerald-700",
  fail: "bg-rose-100 text-rose-700",
  awaiting: "bg-amber-100 text-amber-700",
};

function pretty(v) {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

export default function Drawer({ node, onClose }) {
  const open = !!node;
  return (
    <>
      {/* 遮罩 */}
      <div
        onClick={onClose}
        className={`fixed inset-0 z-20 bg-black/30 transition-opacity ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      {/* 面板 */}
      <aside
        className={`fixed right-0 top-0 z-30 flex h-full w-full max-w-xl flex-col border-l border-slate-200 bg-white shadow-2xl transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {node && <DrawerBody node={node} onClose={onClose} />}
      </aside>
    </>
  );
}

function DrawerBody({ node, onClose }) {
  const fields = FIELD_MAP[node.id] || [];
  const status = node.status || "pending";
  const hasData = node.data && Object.keys(node.data).length > 0;

  return (
    <>
      <header className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-slate-900">{node.label}</h2>
          <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_BADGE[status]}`}>
            {STATUS_LABEL[status]}
          </span>
        </div>
        <button
          onClick={onClose}
          className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          aria-label="关闭"
        >
          ✕
        </button>
      </header>

      <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
        {!hasData && (
          <p className="text-sm text-slate-400">
            该节点尚无产物。运行流程后，节点完成时会自动填充。
          </p>
        )}
        {hasData &&
          fields.map((f) => {
            const val = node.data[f.key];
            const present = val !== undefined && val !== null;
            return (
              <section key={f.key}>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  {f.title}
                  <span className="ml-2 font-mono lowercase text-slate-300">data.{f.key}</span>
                </h3>
                {!present ? (
                  <p className="text-sm text-slate-400">（该字段为空）</p>
                ) : f.kind === "md" ? (
                  <div className="rounded-xl border border-slate-200 bg-slate-50 p-4">
                    <Markdown text={val} />
                  </div>
                ) : (
                  <pre className="max-h-[60vh] overflow-auto rounded-xl border border-slate-200 bg-slate-900 p-4 text-xs leading-relaxed text-slate-100">
                    {typeof val === "string" ? val : pretty(val)}
                  </pre>
                )}
              </section>
            );
          })}
      </div>
    </>
  );
}
