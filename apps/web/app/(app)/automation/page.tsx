"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "@/lib/api";
import { useRole, usePaths } from "@/components/shell";
import { Badge, Button, Card, Empty, ErrorState, Field, fmt, inputCls, Loading, Notice, Table, Td } from "@/components/ui";

type Schedule = { id: number; kind: string; timezone: string; time_of_day: string; weekday: number | null; destination: { channel?: string }; enabled: boolean; external_posting: boolean };
type Run = { id: number; schedule_id: number; status: string; scheduled_for: string; error: string | null; result: Record<string, unknown> };
type Settings = { risk_thresholds: Record<string, number | string[]>; defaults: Record<string, unknown> };

export default function AutomationPage() {
  const { ws, projectId } = usePaths();
  const { isManager, isAdmin } = useRole();
  const qc = useQueryClient();
  const sch = useQuery({ queryKey: ["schedules", ws], queryFn: () => api<Schedule[]>(`${ws}/automation/schedules`) });
  const runs = useQuery({ queryKey: ["runs", ws], queryFn: () => api<Run[]>(`${ws}/automation/runs`) });
  const settings = useQuery({ queryKey: ["settings", ws], queryFn: () => api<Settings>(`${ws}/settings`) });
  const refresh = () => qc.invalidateQueries();
  const [form, setForm] = useState({ kind: "daily_summary", timezone: "Asia/Colombo", time_of_day: "09:00", weekday: 0, channel: "" });
  const create = useMutation({
    mutationFn: () =>
      api(`${ws}/automation/schedules`, {
        method: "POST",
        json: { project_id: projectId, kind: form.kind, timezone: form.timezone, time_of_day: form.time_of_day, weekday: form.kind === "weekly_review" ? Number(form.weekday) : null, destination: form.channel ? { channel: form.channel } : {}, enabled: true, policy: {} },
      }),
    onSuccess: refresh,
  });
  const toggle = useMutation({
    mutationFn: (s: Schedule) => api(`${ws}/automation/schedules/${s.id}/enabled`, { method: "POST", json: { enabled: !s.enabled } }),
    onSuccess: refresh,
  });
  const runNow = useMutation({ mutationFn: (id: number) => api(`${ws}/automation/schedules/${id}/run-now`, { method: "POST" }), onSettled: refresh });
  const [stale, setStale] = useState("");
  const [prHours, setPrHours] = useState("");
  const saveSettings = useMutation({
    mutationFn: () => api(`${ws}/settings`, { method: "PATCH", json: { risk_thresholds: { ...(stale ? { stale_days: Number(stale) } : {}), ...(prHours ? { pr_review_hours: Number(prHours) } : {}) } } }),
    onSuccess: refresh,
  });
  const th = settings.data?.risk_thresholds;
  const err = create.error ?? toggle.error ?? runNow.error ?? saveSettings.error;

  return (
    <>
      <h1 className="text-2xl font-bold">Automation settings</h1>
      <Notice>
        Scheduled jobs generate summaries and scans, and at most <strong>draft</strong> a post for approval. Automatic external posting needs an explicit admin-created policy naming the destination and an authorized owner (not available in this UI yet; API only).
      </Notice>
      {err && <ErrorState error={err} />}
      <Card title="Schedules">
        {sch.isLoading && <Loading />}
        {sch.data?.length === 0 && <Empty title="No schedules" />}
        {sch.data && sch.data.length > 0 && (
          <Table head={["Kind", "When", "Destination", "Posting", "Enabled", ""]}>
            {sch.data.map((s) => (
              <tr key={s.id}>
                <Td>{s.kind.replace("_", " ")}</Td>
                <Td>{s.weekday !== null && `weekday ${s.weekday} `}{s.time_of_day} {s.timezone}</Td>
                <Td>{s.destination.channel ?? "report only"}</Td>
                <Td>{s.external_posting ? <Badge kind="high">auto-post by policy</Badge> : <Badge>drafts only</Badge>}</Td>
                <Td>{s.enabled ? <Badge kind="ok">on</Badge> : <Badge>off</Badge>}</Td>
                <Td>
                  {isManager && (
                    <span className="flex gap-2">
                      <Button variant="secondary" onClick={() => toggle.mutate(s)}>{s.enabled ? "Disable" : "Enable"}</Button>
                      <Button variant="secondary" onClick={() => runNow.mutate(s.id)} disabled={runNow.isPending}>Run now</Button>
                    </span>
                  )}
                </Td>
              </tr>
            ))}
          </Table>
        )}
        {isManager && (
          <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="mt-4 flex flex-wrap items-end gap-3">
            <Field label="Kind">
              <select className={inputCls} value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                <option value="daily_summary">Daily sprint summary</option>
                <option value="blocker_scan">Blocker scan</option>
                <option value="weekly_review">Weekly project review</option>
              </select>
            </Field>
            <Field label="IANA timezone"><input className={inputCls} value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })} /></Field>
            <Field label="Time (HH:MM)"><input className={inputCls} value={form.time_of_day} onChange={(e) => setForm({ ...form, time_of_day: e.target.value })} pattern="[0-2][0-9]:[0-5][0-9]" /></Field>
            {form.kind === "weekly_review" && (
              <Field label="Weekday (0=Mon)"><input type="number" min={0} max={6} className={inputCls} value={form.weekday} onChange={(e) => setForm({ ...form, weekday: Number(e.target.value) })} /></Field>
            )}
            <Field label="Draft to Slack channel (optional)"><input className={inputCls} placeholder="#atlas-dev" value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })} /></Field>
            <Button type="submit" disabled={create.isPending}>Add schedule</Button>
          </form>
        )}
      </Card>
      <Card title="Execution history">
        {runs.data?.length === 0 && <Empty title="No runs yet" />}
        {runs.data && runs.data.length > 0 && (
          <Table head={["Run", "Slot", "Status", "Result"]}>
            {runs.data.map((r) => (
              <tr key={r.id}>
                <Td>#{r.id}</Td>
                <Td>{fmt(r.scheduled_for)}</Td>
                <Td><Badge kind={r.status === "succeeded" ? "ok" : r.status === "failed" ? "high" : "info"}>{r.status}</Badge></Td>
                <Td className="text-xs">{r.error ?? JSON.stringify(r.result)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      <Card title="Risk thresholds">
        {th && (
          <p className="mb-2 text-sm text-muted">
            Current: stale after {String(th.stale_days)} day(s) · PR waiting after {String(th.pr_review_hours)}h · sprint-end window {String(th.sprint_end_window_days)} day(s)
          </p>
        )}
        {isAdmin ? (
          <form onSubmit={(e) => { e.preventDefault(); saveSettings.mutate(); }} className="flex flex-wrap items-end gap-3">
            <Field label="Stale after (days)"><input type="number" min={0} className={inputCls} value={stale} onChange={(e) => setStale(e.target.value)} /></Field>
            <Field label="PR review wait (hours)"><input type="number" min={0} className={inputCls} value={prHours} onChange={(e) => setPrHours(e.target.value)} /></Field>
            <Button type="submit" disabled={saveSettings.isPending || (!stale && !prHours)}>Save thresholds</Button>
          </form>
        ) : (
          <p className="text-sm text-muted">Only admins can edit thresholds. Re-run the scan on the Risks page after a change.</p>
        )}
      </Card>
    </>
  );
}
