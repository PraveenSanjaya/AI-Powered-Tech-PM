"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, setCsrf, type Me } from "@/lib/api";
import { Button, Field, inputCls, Notice } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me =
        mode === "login"
          ? await api<Me>("/auth/login", { method: "POST", json: { email, password } })
          : await api<Me>("/auth/register", { method: "POST", json: { email, password, name, workspace_name: workspace } });
      setCsrf(me.csrf_token);
      qc.setQueryData(["me"], me);
      router.replace("/select");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center gap-4 p-4">
      <h1 className="text-2xl font-bold">AI-Powered Tech PM</h1>
      <Notice kind="demo">
        Development demo account: <code>manager@demo.example</code> / <code>demo-password-123</code> (after running the seed command). Demo data is fictional and integrations are simulated.
      </Notice>
      <div role="tablist" aria-label="Sign in or register" className="flex gap-2">
        {(["login", "register"] as const).map((m) => (
          <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)} className={`rounded px-3 py-1 text-sm ${mode === m ? "bg-accent text-accent-fg" : "border border-border"}`}>
            {m === "login" ? "Sign in" : "Register"}
          </button>
        ))}
      </div>
      <form onSubmit={submit} className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
        {mode === "register" && (
          <>
            <Field label="Your name">
              <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} />
            </Field>
            <Field label="Workspace name">
              <input className={inputCls} value={workspace} onChange={(e) => setWorkspace(e.target.value)} required maxLength={200} />
            </Field>
          </>
        )}
        <Field label="Email">
          <input type="email" autoComplete="username" className={inputCls} value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label={mode === "register" ? "Password (10+ characters)" : "Password"}>
          <input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} className={inputCls} value={password} onChange={(e) => setPassword(e.target.value)} required minLength={mode === "register" ? 10 : 1} />
        </Field>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
        <Button type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </Button>
      </form>
    </main>
  );
}
