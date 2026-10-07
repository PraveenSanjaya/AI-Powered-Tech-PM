"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { api, type Message } from "@/lib/api";
import { usePaths } from "@/components/shell";
import Markdown from "@/components/markdown";
import { Badge, Button, Card, ErrorState, Loading, Notice, inputCls } from "@/components/ui";
import { useSelection } from "@/lib/context";

const SUGGESTIONS = [
  "What threatens our sprint goal?",
  "Which tasks are blocked?",
  "Which pull requests need review?",
  "Which issues have no recent activity?",
  "What assigned work does each team member have?",
  "What changed since the previous update?",
  "Can you draft a sprint update?",
  "Prepare a Jira comment for ATLAS-101",
];

export default function AssistantPage() {
  const { ws, projectId } = usePaths();
  const { sprintId } = useSelection();
  const qc = useQueryClient();
  const [conv, setConv] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [lastAsked, setLastAsked] = useState("");

  const msgs = useQuery({
    queryKey: ["messages", ws, conv],
    queryFn: () => api<Message[]>(`${ws}/conversations/${conv}/messages`),
    enabled: conv !== null,
  });
  const ask = useMutation({
    mutationFn: async (q: string) => {
      let id = conv;
      if (id === null) {
        const c = await api<{ id: number }>(`${ws}/conversations`, { method: "POST", json: { project_id: projectId, sprint_id: sprintId } });
        id = c.id;
        setConv(id);
      }
      return api<Message>(`${ws}/conversations/${id}/messages`, { method: "POST", json: { text: q } });
    },
    onMutate: (q) => setLastAsked(q),
    onSuccess: () => {
      setText("");
      qc.invalidateQueries({ queryKey: ["messages"] });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["messages"] }),
  });
  async function clear() {
    if (conv !== null) await api(`${ws}/conversations/${conv}`, { method: "DELETE" });
    setConv(null);
    qc.removeQueries({ queryKey: ["messages"] });
  }
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (text.trim()) ask.mutate(text.trim());
  };

  return (
    <>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">AI assistant</h1>
        <Button variant="secondary" onClick={clear} disabled={conv === null}>
          Clear conversation
        </Button>
      </div>
      <Notice>Answers are built from synchronized project data and cite evidence (EV-n). The assistant can only <em>draft</em> actions; nothing is sent until a manager approves it under Action approvals.</Notice>
      <Card>
        <div className="mb-3 flex flex-wrap gap-2" aria-label="Suggested questions">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="rounded-full border border-border px-3 py-1 text-xs hover:bg-bg" onClick={() => ask.mutate(s)} disabled={ask.isPending}>
              {s}
            </button>
          ))}
        </div>
        <div className="space-y-3" role="log" aria-live="polite" aria-label="Conversation">
          {conv === null && !ask.isPending && <p className="text-sm text-muted">Ask a question about the sprint to start.</p>}
          {msgs.isLoading && <Loading />}
          {msgs.data?.map((m) => (
            <div key={m.id} className={`rounded-md p-3 ${m.role === "user" ? "ml-8 bg-bg" : "border border-border"}`} data-role={m.role}>
              <p className="mb-1 text-xs font-semibold text-muted">{m.role === "user" ? "You" : "Assistant"}</p>
              {m.role === "user" ? <p className="text-sm">{m.content}</p> : <Markdown text={m.content} citations={m.meta.citations} />}
              {m.role === "assistant" && (
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
                  {m.meta.label && <Badge kind={m.meta.generator === "demo-template" ? "demo" : "info"}>{m.meta.label}</Badge>}
                  {m.meta.fallback && <Badge kind="medium">LLM unavailable: showing template answer</Badge>}
                  {m.meta.freshness?.stale && <Badge kind="medium">stale data</Badge>}
                  {m.meta.proposal_id && (
                    <Link href={`/actions?id=${m.meta.proposal_id}`} className="font-medium text-accent underline">
                      Review proposed action #{m.meta.proposal_id}
                    </Link>
                  )}
                </div>
              )}
              {m.role === "assistant" && m.meta.citations && m.meta.citations.length > 0 && (
                <details className="mt-2 text-xs">
                  <summary className="cursor-pointer">Evidence ({m.meta.citations.length})</summary>
                  <ul className="mt-1 space-y-1">
                    {m.meta.citations.map((c) => (
                      <li key={c.id} data-testid="citation">
                        <strong>EV-{c.id}</strong> {c.title} — {c.provider}, {new Date(c.timestamp).toLocaleString()}{" "}
                        <a href={c.url} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                          source
                        </a>{" "}
                        {c.is_demo && <Badge kind="demo">demo link</Badge>}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ))}
          {ask.isPending && (
            <p role="status" className="text-sm text-muted">
              Collecting evidence and drafting an answer…
            </p>
          )}
          {ask.isError && (
            <div>
              <ErrorState error={ask.error} onRetry={() => ask.mutate(lastAsked)} />
              <p className="mt-1 text-xs text-muted">Your conversation is saved; retrying will not lose it.</p>
            </div>
          )}
        </div>
        <form onSubmit={submit} className="mt-4 flex gap-2">
          <label className="sr-only" htmlFor="q">
            Ask about the sprint
          </label>
          <input id="q" className={`${inputCls} flex-1`} value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about the sprint…" maxLength={2000} />
          <Button type="submit" disabled={ask.isPending || !text.trim()}>
            Ask
          </Button>
        </form>
      </Card>
    </>
  );
}
