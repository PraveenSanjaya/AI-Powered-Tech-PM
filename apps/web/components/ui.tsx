"use client";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, type Citation } from "@/lib/api";
import { useSelection } from "@/lib/context";

export function Card({ title, children, actions, className = "" }: { title?: string; children: React.ReactNode; actions?: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-lg border border-border bg-surface p-4 ${className}`} aria-label={title}>
      {(title || actions) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

const tone: Record<string, string> = {
  high: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
  medium: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  low: "bg-slate-100 text-slate-800 dark:bg-slate-800 dark:text-slate-200",
  ok: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  info: "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200",
  demo: "bg-purple-100 text-purple-900 dark:bg-purple-950 dark:text-purple-200",
};
export function Badge({ children, kind = "low" }: { children: React.ReactNode; kind?: keyof typeof tone | string }) {
  return <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${tone[kind] ?? tone.low}`}>{children}</span>;
}

export function Button({ variant = "primary", className = "", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const v = {
    primary: "bg-accent text-accent-fg hover:opacity-90",
    secondary: "border border-border bg-surface hover:bg-bg",
    danger: "bg-danger text-white hover:opacity-90",
  }[variant];
  return <button {...p} className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50 ${v} ${className}`} />;
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted">{label}</span>
      {children}
    </label>
  );
}
export const inputCls = "rounded-md border border-border bg-surface px-2 py-1.5 text-sm";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="p-6 text-sm text-muted">
      {label}…
    </div>
  );
}
export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="rounded-md border border-dashed border-border p-6 text-center">
      <p className="font-medium">{title}</p>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
    </div>
  );
}
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Something went wrong";
  return (
    <div role="alert" className="rounded-md border border-danger p-4 text-sm">
      <p className="font-semibold text-danger">Could not load data</p>
      <p className="mt-1">{msg}</p>
      {onRetry && (
        <Button variant="secondary" className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "warn" | "demo"; children: React.ReactNode }) {
  const cls = { info: "border-accent", warn: "border-warn", demo: "border-purple-500" }[kind];
  return (
    <div role="note" className={`rounded-md border-l-4 ${cls} bg-surface p-3 text-sm`}>
      {children}
    </div>
  );
}

export function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-border text-muted">
            {head.map((h) => (
              <th key={h} scope="col" className="px-2 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
export const Td = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => <td className={`border-b border-border px-2 py-2 align-top ${className}`}>{children}</td>;

export const fmt = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/** Imported text is untrusted: always rendered as plain text by React, never as HTML. */
export function EvidenceChip({ id }: { id: number }) {
  const { workspaceId, projectId } = useSelection();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["evidence", workspaceId, projectId, id],
    queryFn: () => api<Citation & { retrieved_at: string; author: string | null }>(`/workspaces/${workspaceId}/projects/${projectId}/evidence/${id}`),
    enabled: open,
  });
  return (
    <span className="inline-block align-baseline">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="rounded border border-border px-1.5 text-xs text-accent hover:bg-bg">
        EV-{id}
      </button>
      {open && (
        <span className="mt-1 block max-w-xl rounded border border-border bg-bg p-2 text-xs">
          {q.isLoading && "Loading evidence…"}
          {q.isError && <span className="text-danger">Evidence unavailable</span>}
          {q.data && (
            <>
              <strong>{q.data.title}</strong> {q.data.is_demo && <Badge kind="demo">demo data</Badge>}
              <span className="block whitespace-pre-wrap text-muted">{q.data.excerpt}</span>
              <span className="block text-muted">
                {q.data.provider} · {fmt(q.data.timestamp)} · {q.data.author ?? "unknown author"}
              </span>
              <a href={q.data.url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                Source link
              </a>
              {q.data.is_demo && <span className="block text-muted">(demo link: does not resolve to a real service)</span>}
            </>
          )}
        </span>
      )}
    </span>
  );
}
export const Evidence = ({ ids }: { ids: number[] }) => (
  <span className="inline-flex flex-wrap gap-1">{ids.length ? ids.map((i) => <EvidenceChip key={i} id={i} />) : <span className="text-xs text-muted">no evidence record</span>}</span>
);

export function severityKind(s: string) {
  return s === "high" ? "high" : s === "medium" ? "medium" : "low";
}
