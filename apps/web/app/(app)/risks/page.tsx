"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Finding } from "@/lib/api";
import { useRole, usePaths } from "@/components/shell";
import { Badge, Button, Card, Empty, ErrorState, Evidence, Field, fmt, inputCls, Loading, Notice, severityKind } from "@/components/ui";

export default function RisksPage() {
  const { proj } = usePaths();
  const { isManager } = useRole();
  const qc = useQueryClient();
  const [conf, setConf] = useState("");
  const [status, setStatus] = useState("open");
  const params = new URLSearchParams();
  if (conf) params.set("confidence", conf);
  if (status) params.set("status", status);
  const q = useQuery({ queryKey: ["risks", proj, params.toString()], queryFn: () => api<Finding[]>(`${proj}/risks?${params}`) });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["risks"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const scan = useMutation({ mutationFn: () => api(`${proj}/risks/scan`, { method: "POST" }), onSuccess: refresh });
  const setStat = useMutation({ mutationFn: (v: { id: number; status: string }) => api(`${proj}/risks/${v.id}`, { method: "PATCH", json: { status: v.status } }), onSuccess: refresh });

  const confirmed = q.data?.filter((f) => f.confidence === "confirmed") ?? [];
  const possible = q.data?.filter((f) => f.confidence !== "confirmed") ?? [];
  const row = (f: Finding) => (
    <li key={f.id} className="rounded border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge kind={severityKind(f.severity)}>{f.severity}</Badge>
        <Badge kind={f.confidence === "confirmed" ? "info" : "low"}>{f.confidence}</Badge>
        <span className="text-xs text-muted">
          rule: {f.method} · detected {fmt(f.detected_at)} · {f.status}
        </span>
      </div>
      <p className="mt-1 text-sm">{f.explanation}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        <span>Evidence:</span> <Evidence ids={f.evidence_ids} />
      </div>
      {isManager && (
        <div className="mt-2 flex gap-2">
          {(["acknowledged", "resolved", "dismissed"] as const).map((s) => (
            <Button key={s} variant="secondary" disabled={f.status === s || setStat.isPending} onClick={() => setStat.mutate({ id: f.id, status: s })}>
              {s === "acknowledged" ? "Acknowledge" : s === "resolved" ? "Resolve" : "Dismiss"}
            </Button>
          ))}
        </div>
      )}
    </li>
  );
  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-bold">Risks &amp; blockers</h1>
        <div className="flex items-end gap-3">
          <Field label="Confidence">
            <select className={inputCls} value={conf} onChange={(e) => setConf(e.target.value)}>
              <option value="">All</option>
              <option>confirmed</option>
              <option>likely</option>
              <option>possible</option>
            </select>
          </Field>
          <Field label="Status">
            <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option>open</option>
              <option>acknowledged</option>
              <option>resolved</option>
              <option>dismissed</option>
            </select>
          </Field>
          {isManager && (
            <Button onClick={() => scan.mutate()} disabled={scan.isPending}>
              {scan.isPending ? "Scanning…" : "Re-run scan"}
            </Button>
          )}
        </div>
      </div>
      <Notice>Findings come from deterministic rules. “Possible” findings (for example inactivity or chat mentions) are signals to review, not proof that work is blocked.</Notice>
      {scan.isError && <ErrorState error={scan.error} />}
      {q.isLoading && <Loading label="Loading findings" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.length === 0 && <Empty title="No findings match" hint="Nothing is flagged by the current rules and thresholds." />}
      {confirmed.length > 0 && (
        <Card title={`Confirmed (${confirmed.length})`}>
          <ul className="space-y-2">{confirmed.map(row)}</ul>
        </Card>
      )}
      {possible.length > 0 && (
        <Card title={`Possible risks that need review (${possible.length})`}>
          <ul className="space-y-2">{possible.map(row)}</ul>
        </Card>
      )}
    </>
  );
}
