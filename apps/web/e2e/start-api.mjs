// Prepares a clean e2e database (migrate + demo seed) and then runs the API in the foreground.
// Env: E2E_PYTHON (python interpreter with API deps), E2E_DATABASE_URL (a DISPOSABLE postgres database).
import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const apiDir = path.resolve(here, "../../../services/api");
const py = process.env.E2E_PYTHON || (process.platform === "win32" ? path.join(apiDir, ".venv/Scripts/python.exe") : path.join(apiDir, ".venv/bin/python"));
const env = {
  ...process.env,
  DATABASE_URL: process.env.E2E_DATABASE_URL || "postgresql+psycopg://test:test@localhost:5433/techpm_e2e",
  CREDENTIAL_ENCRYPTION_KEY: process.env.CREDENTIAL_ENCRYPTION_KEY || "kP3m8o5QqVwV9a4t1xT0tX1u0m6Qk2yZ0hZzv7mJj1c=",
  APP_ENV: "development",
  DEMO_MODE: "true",
  LLM_PROVIDER: "demo",
  LOGIN_MAX_ATTEMPTS: "1000",
};
const run = (args) => {
  const r = spawnSync(py, args, { cwd: apiDir, env, stdio: "inherit" });
  if (r.status !== 0) process.exit(r.status ?? 1);
};
run(["-c", "from sqlalchemy import text; from app.core.db import get_engine; c=get_engine().begin().__enter__(); c.execute(text('drop schema public cascade; create schema public'))"]);
run(["-m", "app.cli", "migrate"]);
run(["-m", "app.cli", "seed"]);
const server = spawn(py, ["-m", "uvicorn", "app.main:app", "--port", "8000"], { cwd: apiDir, env, stdio: "inherit" });
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => server.kill());
server.on("exit", (code) => process.exit(code ?? 0));
