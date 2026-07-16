import asyncio
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field


APP_DIR = Path(os.getenv("AGENT_CONSOLE_HOME", "/var/lib/agent-console"))
DB_PATH = Path(os.getenv("AGENT_CONSOLE_DB", APP_DIR / "agent-console.db"))
WORKSPACE = Path(os.getenv("AGENT_CONSOLE_WORKSPACE", "/srv/agent-workspace"))
INTERNAL_PROXY_TOKEN = os.getenv("INTERNAL_PROXY_TOKEN", "")
REQUIRE_CF_ACCESS = os.getenv("REQUIRE_CF_ACCESS", "1") == "1"
ALLOWED_EMAILS = {
    email.strip().lower()
    for email in os.getenv("ALLOWED_EMAILS", "").split(",")
    if email.strip()
}
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "12000"))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "1800"))

RUNNERS = {
    "codex": {
        "label": "Codex",
        "command": ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check"],
    },
    "gemini": {
        "label": "Gemini",
        "command": ["gemini", "--prompt", "--skip-trust", "--approval-mode", "default"],
    },
}


class SubmitJob(BaseModel):
    runner: str = Field(pattern="^(codex|gemini)$")
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma journal_mode=wal")
    conn.execute(
        """
        create table if not exists jobs (
          id text primary key,
          runner text not null,
          prompt text not null,
          status text not null,
          submitted_by text,
          created_at text not null,
          started_at text,
          finished_at text,
          exit_code integer,
          log text not null default ''
        )
        """
    )
    return conn


def require_proxy_and_access(
    x_internal_proxy_token: str | None = Header(default=None),
    cf_access_authenticated_user_email: str | None = Header(default=None),
) -> str:
    if not INTERNAL_PROXY_TOKEN:
        raise HTTPException(status_code=503, detail="INTERNAL_PROXY_TOKEN is not configured")
    if x_internal_proxy_token != INTERNAL_PROXY_TOKEN:
        raise HTTPException(status_code=403, detail="forbidden")

    email = (cf_access_authenticated_user_email or "").strip().lower()
    if REQUIRE_CF_ACCESS:
        if not email:
            raise HTTPException(status_code=401, detail="Cloudflare Access header missing")
        if ALLOWED_EMAILS and email not in ALLOWED_EMAILS:
            raise HTTPException(status_code=403, detail="email not allowed")
    return email or "local-proxy"


def job_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "runner": row["runner"],
        "status": row["status"],
        "submitted_by": row["submitted_by"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "exit_code": row["exit_code"],
        "prompt": row["prompt"],
        "log": row["log"],
    }


async def append_log(job_id: str, text: str) -> None:
    conn = db()
    try:
        conn.execute("update jobs set log = log || ? where id = ?", (text, job_id))
        conn.commit()
    finally:
        conn.close()


async def run_job(job_id: str) -> None:
    conn = db()
    row = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
    if row is None:
        conn.close()
        return

    runner = row["runner"]
    prompt = row["prompt"]
    base_command = RUNNERS[runner]["command"]
    command = [*base_command, prompt]

    conn.execute(
        "update jobs set status = ?, started_at = ?, log = log || ? where id = ?",
        ("running", utc_now(), f"$ {' '.join(command[:-1])} <prompt>\n\n", job_id),
    )
    conn.commit()
    conn.close()

    started = time.monotonic()
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(WORKSPACE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
        )
        assert process.stdout is not None
        while True:
            if time.monotonic() - started > JOB_TIMEOUT_SECONDS:
                process.kill()
                await append_log(job_id, f"\n[agent-console] timed out after {JOB_TIMEOUT_SECONDS}s\n")
                exit_code = 124
                break
            line = await process.stdout.readline()
            if not line:
                exit_code = await process.wait()
                break
            await append_log(job_id, line.decode(errors="replace"))

        status = "succeeded" if exit_code == 0 else "failed"
        conn = db()
        conn.execute(
            "update jobs set status = ?, finished_at = ?, exit_code = ? where id = ?",
            (status, utc_now(), exit_code, job_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        await append_log(job_id, f"\n[agent-console] runner error: {exc}\n")
        conn = db()
        conn.execute(
            "update jobs set status = ?, finished_at = ?, exit_code = ? where id = ?",
            ("failed", utc_now(), 125, job_id),
        )
        conn.commit()
        conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    conn = db()
    conn.close()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz() -> str:
    return "ok\n"


@app.get("/", response_class=HTMLResponse)
async def index(_: str = Depends(require_proxy_and_access)) -> str:
    return INDEX_HTML


@app.get("/api/me")
async def me(email: str = Depends(require_proxy_and_access)) -> dict[str, Any]:
    return {"email": email, "runners": RUNNERS}


@app.post("/api/jobs")
async def submit_job(payload: SubmitJob, email: str = Depends(require_proxy_and_access)) -> dict[str, str]:
    job_id = str(uuid.uuid4())
    conn = db()
    conn.execute(
        """
        insert into jobs(id, runner, prompt, status, submitted_by, created_at)
        values (?, ?, ?, ?, ?, ?)
        """,
        (job_id, payload.runner, payload.prompt, "queued", email, utc_now()),
    )
    conn.commit()
    conn.close()
    asyncio.create_task(run_job(job_id))
    return {"id": job_id}


@app.get("/api/jobs")
async def list_jobs(_: str = Depends(require_proxy_and_access)) -> list[dict[str, Any]]:
    conn = db()
    rows = conn.execute(
        """
        select id, runner, status, submitted_by, created_at, started_at, finished_at,
               exit_code, prompt, log
        from jobs
        order by created_at desc
        limit 30
        """
    ).fetchall()
    conn.close()
    return [job_to_dict(row) for row in rows]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, _: str = Depends(require_proxy_and_access)) -> dict[str, Any]:
    conn = db()
    row = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_to_dict(row)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agent Console</title>
  <style>
    :root { color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f7f7f4; color: #202124; }
    main { max-width: 1120px; margin: 0 auto; padding: 24px; display: grid; gap: 18px; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
    h1 { font-size: 28px; margin: 0; letter-spacing: 0; }
    .meta { color: #5f6368; font-size: 14px; }
    .panel { background: #fff; border: 1px solid #d9d9d2; border-radius: 8px; padding: 16px; }
    .composer { display: grid; gap: 12px; }
    textarea { width: 100%; min-height: 170px; resize: vertical; box-sizing: border-box; border: 1px solid #c8c8c0; border-radius: 6px; padding: 12px; font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    select, button { height: 38px; border-radius: 6px; border: 1px solid #bdbdb6; background: #fff; color: #202124; padding: 0 12px; font: inherit; }
    button { background: #1f6feb; color: #fff; border-color: #1f6feb; cursor: pointer; }
    button:disabled { opacity: .55; cursor: wait; }
    .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .jobs { display: grid; gap: 12px; }
    .job { border: 1px solid #d9d9d2; border-radius: 8px; padding: 12px; background: #fff; }
    .job-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .badge { display: inline-flex; align-items: center; height: 24px; padding: 0 8px; border-radius: 999px; font-size: 12px; background: #ecebe4; color: #34352f; }
    .succeeded { background: #dff3e4; color: #146c2e; }
    .failed { background: #ffe0dc; color: #a5261a; }
    .running, .queued { background: #e6f0ff; color: #174ea6; }
    pre { overflow: auto; white-space: pre-wrap; word-break: break-word; background: #181a1f; color: #f1f3f4; border-radius: 6px; padding: 12px; max-height: 460px; }
    .prompt { color: #5f6368; margin: 8px 0; font-size: 13px; }
    @media (max-width: 720px) { main { padding: 14px; } header { align-items: flex-start; flex-direction: column; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Agent Console</h1>
      <div class="meta" id="identity">Checking access...</div>
    </div>
    <button id="refresh">Refresh</button>
  </header>

  <section class="panel composer">
    <div class="row">
      <select id="runner">
        <option value="codex">Codex</option>
        <option value="gemini">Gemini</option>
      </select>
      <button id="submit">Run Task</button>
    </div>
    <textarea id="prompt" placeholder="Describe the task to run in /srv/agent-workspace..."></textarea>
  </section>

  <section class="jobs" id="jobs"></section>
</main>
<script>
const jobsEl = document.querySelector("#jobs");
const identityEl = document.querySelector("#identity");
const promptEl = document.querySelector("#prompt");
const runnerEl = document.querySelector("#runner");
const submitEl = document.querySelector("#submit");
const refreshEl = document.querySelector("#refresh");

async function api(path, options = {}) {
  const res = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadIdentity() {
  const data = await api("/api/me");
  identityEl.textContent = `Signed in through Cloudflare Access as ${data.email}`;
}

async function loadJobs() {
  const jobs = await api("/api/jobs");
  jobsEl.innerHTML = jobs.map(job => `
    <article class="job">
      <div class="job-head">
        <strong>${escapeHtml(job.runner)} task</strong>
        <span class="badge ${escapeHtml(job.status)}">${escapeHtml(job.status)}${job.exit_code === null ? "" : " / " + job.exit_code}</span>
      </div>
      <div class="prompt">${escapeHtml(job.prompt.slice(0, 300))}${job.prompt.length > 300 ? "..." : ""}</div>
      <pre>${escapeHtml(job.log || "")}</pre>
    </article>
  `).join("") || `<div class="panel meta">No jobs yet.</div>`;
}

async function submitJob() {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  submitEl.disabled = true;
  try {
    await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({runner: runnerEl.value, prompt})
    });
    promptEl.value = "";
    await loadJobs();
  } finally {
    submitEl.disabled = false;
  }
}

submitEl.addEventListener("click", submitJob);
refreshEl.addEventListener("click", loadJobs);
loadIdentity().catch(err => identityEl.textContent = err.message);
loadJobs().catch(err => jobsEl.innerHTML = `<div class="panel">${escapeHtml(err.message)}</div>`);
setInterval(loadJobs, 4000);
</script>
</body>
</html>
"""
