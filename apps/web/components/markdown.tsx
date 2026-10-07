import React from "react";
import type { Citation } from "@/lib/api";

/**
 * Minimal, safe renderer for assistant output. It builds React elements only (no innerHTML), supports headings,
 * lists, code fences, **bold**, _italic_ and [EV-n] citations. Links are never generated from text, so imported
 * Markdown cannot inject URLs or scripts.
 */
function inline(text: string, cites: Map<number, Citation>, key: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|_[^_]+_|\[EV-\d+\]|\[unverified\])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    const k = `${key}-${i++}`;
    if (t.startsWith("**")) out.push(<strong key={k}>{t.slice(2, -2)}</strong>);
    else if (t.startsWith("_")) out.push(<em key={k}>{t.slice(1, -1)}</em>);
    else if (t === "[unverified]") out.push(<span key={k} className="text-danger">[unverified]</span>);
    else {
      const id = Number(t.slice(4, -1));
      const c = cites.get(id);
      out.push(
        c ? (
          <a key={k} href={c.url} target="_blank" rel="noopener noreferrer" title={`${c.title} (${c.provider})`} className="mx-0.5 rounded border border-border px-1 text-xs text-accent hover:bg-bg">
            EV-{id}
          </a>
        ) : (
          <span key={k} className="text-xs text-muted">[EV-{id}]</span>
        ),
      );
    }
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export default function Markdown({ text, citations = [] }: { text: string; citations?: Citation[] }) {
  const cites = new Map(citations.map((c) => [c.id, c]));
  const lines = text.split("\n");
  const nodes: React.ReactNode[] = [];
  let list: React.ReactNode[] = [];
  let code: string[] | null = null;
  const flush = (k: number) => {
    if (list.length) nodes.push(<ul key={`ul${k}`} className="my-1 list-disc space-y-1 pl-5">{list}</ul>);
    list = [];
  };
  lines.forEach((ln, i) => {
    if (ln.startsWith("```")) {
      if (code) {
        nodes.push(<pre key={`c${i}`} className="my-2 overflow-x-auto whitespace-pre-wrap rounded bg-bg p-2 text-xs">{code.join("\n")}</pre>);
        code = null;
      } else {
        flush(i);
        code = [];
      }
      return;
    }
    if (code) return void code.push(ln);
    if (/^\s*- /.test(ln)) return void list.push(<li key={i}>{inline(ln.replace(/^\s*- /, ""), cites, `l${i}`)}</li>);
    flush(i);
    if (ln.startsWith("### ")) nodes.push(<h4 key={i} className="mt-3 font-semibold">{inline(ln.slice(4), cites, `h${i}`)}</h4>);
    else if (ln.startsWith("## ")) nodes.push(<h3 key={i} className="mt-2 text-base font-semibold">{inline(ln.slice(3), cites, `h${i}`)}</h3>);
    else if (ln.trim()) nodes.push(<p key={i} className="my-1">{inline(ln, cites, `p${i}`)}</p>);
  });
  flush(lines.length);
  return <div className="text-sm leading-relaxed">{nodes}</div>;
}
