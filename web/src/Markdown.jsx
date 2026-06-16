// Markdown.jsx —— 极简 markdown 渲染（无外部依赖），仅覆盖 PRD 常见语法：
// 标题(#~####)、无序/有序列表、加粗、行内代码、```代码块```、分隔线、段落。
// 足以把 prd_draft / prd_final 渲染为可读文本，不追求完备。

function inline(text) {
  // 转义后再按 **bold** / `code` 切分，返回 React 节点数组
  const nodes = [];
  let rest = text;
  let key = 0;
  const re = /(\*\*([^*]+)\*\*)|(`([^`]+)`)/;
  let m;
  while ((m = re.exec(rest))) {
    if (m.index > 0) nodes.push(rest.slice(0, m.index));
    if (m[2] !== undefined) {
      nodes.push(<strong key={key++} className="font-semibold text-slate-900">{m[2]}</strong>);
    } else if (m[4] !== undefined) {
      nodes.push(
        <code key={key++} className="rounded bg-slate-100 px-1 py-0.5 text-[0.85em] text-rose-600">
          {m[4]}
        </code>
      );
    }
    rest = rest.slice(m.index + m[0].length);
  }
  if (rest) nodes.push(rest);
  return nodes;
}

export default function Markdown({ text }) {
  if (!text) return <p className="text-slate-400">（无内容）</p>;
  const lines = String(text).split("\n");
  const blocks = [];
  let i = 0;
  let key = 0;
  let list = null; // { ordered, items: [] }

  const flushList = () => {
    if (!list) return;
    const items = list.items.map((it, idx) => (
      <li key={idx} className="ml-1">{inline(it)}</li>
    ));
    blocks.push(
      list.ordered ? (
        <ol key={key++} className="my-2 list-decimal space-y-1 pl-6 text-slate-700">{items}</ol>
      ) : (
        <ul key={key++} className="my-2 list-disc space-y-1 pl-6 text-slate-700">{items}</ul>
      )
    );
    list = null;
  };

  while (i < lines.length) {
    const line = lines[i];

    // 代码块
    if (line.trim().startsWith("```")) {
      flushList();
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++; // 跳过结束 ```
      blocks.push(
        <pre key={key++} className="my-2 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
          {buf.join("\n")}
        </pre>
      );
      continue;
    }

    // 分隔线
    if (/^\s*(---|\*\*\*|___)\s*$/.test(line)) {
      flushList();
      blocks.push(<hr key={key++} className="my-3 border-slate-200" />);
      i++;
      continue;
    }

    // 标题
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      flushList();
      const level = h[1].length;
      const sizes = ["text-xl", "text-lg", "text-base", "text-sm"];
      blocks.push(
        <p key={key++} className={`mt-3 mb-1 font-bold text-slate-900 ${sizes[level - 1]}`}>
          {inline(h[2])}
        </p>
      );
      i++;
      continue;
    }

    // 列表项
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ul || ol) {
      const ordered = !!ol;
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push((ul || ol)[1]);
      i++;
      continue;
    }

    // 空行
    if (line.trim() === "") {
      flushList();
      i++;
      continue;
    }

    // 普通段落
    flushList();
    blocks.push(
      <p key={key++} className="my-1 leading-relaxed text-slate-700">{inline(line)}</p>
    );
    i++;
  }
  flushList();

  return <div className="text-[15px]">{blocks}</div>;
}
