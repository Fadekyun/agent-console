#!/usr/bin/env python3
"""Jev ghost probe — one bounded synthetic TypeSafe/Jev cycle.

Keeps the shared Jev credential + typed-answer path under continuous test while
other work happens. Reads TYPESAFE_API_KEY only from the inherited environment
(never the secret file, never prints the value). Appends sanitized results to a
JSONL history for review in Agent Console.

No hostnames, IPs, usernames, or secret values are recorded.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
STATE_DIR = Path(os.environ.get(
    "AGENT_CONSOLE_JEV_GHOST_DIR",
    os.path.expanduser("~/.local/share/agent-console/jev-ghost"),
))
RUNS_FILE = STATE_DIR / "runs.jsonl"
STATE_FILE = STATE_DIR / "state.json"
LATEST_FILE = STATE_DIR / "latest.json"
SUMMARY_FILE = STATE_DIR / "summary.json"
LOCK_FILE = STATE_DIR / "run.lock"
MAX_RUNS = 500  # trim history

# Each scenario: short state + 2 typed questions with assertions.
SCENARIOS = [
    {
        "id": "email_intent",
        "state": {"message": "Hi, can you send me the November schedule when you have a moment?"},
        "questions": {
            "is_request": {"type": "noul",
                           "instructions": "Is this message asking for something to be provided?",
                           "criteria": {"true": "Asks for something", "false": "No ask"}},
            "intent": {"type": "choice",
                       "instructions": "Which category best describes the message?",
                       "criteria": {"greeting": "Pure greeting", "question": "Information question",
                                    "request": "Asks the recipient to do or send something",
                                    "other": "None of the above"}},
        },
        "expect": {"is_request.noul_min": 0.5, "intent.choice_in": ["request"]},
    },
    {
        "id": "ticket_routing",
        "state": {"ticket": {"text": "I was charged twice for the same order and need a refund."}},
        "questions": {
            "category": {"type": "choice",
                         "instructions": "Which queue should this support ticket go to?",
                         "criteria": {"billing": "Payment, charges, or refunds",
                                      "technical": "Bugs or failures",
                                      "account": "Login or account access",
                                      "other": "None of the above"}},
            "urgent": {"type": "noul",
                       "instructions": "Does this ticket describe an urgent money-loss or access-loss problem?",
                       "criteria": {"true": "Money or access loss", "false": "Not urgent"}},
        },
        "expect": {"category.choice_in": ["billing"], "urgent.noul_min": 0.5},
    },
    {
        "id": "claim_check",
        "state": {"claim": "The order shipped on Tuesday.",
                  "evidence": "Tracking shows the parcel left the warehouse on Wednesday."},
        "questions": {
            "supported": {"type": "noul",
                          "instructions": "Does the evidence support the claim as stated?",
                          "criteria": {"true": "Evidence supports the claim", "false": "Evidence contradicts or does not support"}},
            "position": {"type": "choice",
                         "instructions": "How does the evidence relate to the claim?",
                         "criteria": {"supports": "Confirms it", "contradicts": "Directly conflicts",
                                      "insufficient": "Not enough to judge"}},
        },
        "expect": {"supported.noul_max": 0.5, "position.choice_in": ["contradicts", "insufficient"]},
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        with path.open() as fh:
            return json.load(fh)
    except Exception:
        return default


def _atomic_write(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _acquire_lock() -> bool:
    """Single-writer guard; stale locks older than 10 minutes are reclaimed."""
    try:
        if LOCK_FILE.exists() and time.time() - LOCK_FILE.stat().st_mtime < 600:
            return False
        LOCK_FILE.write_text(str(os.getpid()))
        return True
    except Exception:
        return False


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def _skill_probe() -> dict:
    """Static discovery-path check: which roots currently resolve the shared skill."""
    roots = {
        "codex_native": "~/.codex/skills/typesafe-ai",
        "claude_native": "~/.claude/skills/typesafe-ai",
        "hermes_native": "~/.hermes/skills/homelab/typesafe-ai",
    }
    out = {}
    for name, raw in roots.items():
        try:
            out[name] = Path(os.path.expanduser(raw)).exists()
        except Exception:
            out[name] = False
    # canonical/context skill roots (pi and other isolating harness sessions)
    try:
        contexts = Path(os.path.expanduser("~/.local/share/agent-console/contexts"))
        out["canonical"] = contexts.is_dir() and any(contexts.glob("*/skills/typesafe-ai"))
    except Exception:
        out["canonical"] = False
    # per-session isolated roots (codex/codex-pro/hermes Console sessions) — known gap
    iso = Path(os.path.expanduser("~/.local/share/agent-console/skills-isolated"))
    try:
        out["isolated_roots_with_skill"] = sum(
            1 for p in iso.glob("*/typesafe-ai") if p.exists()
        )
    except Exception:
        out["isolated_roots_with_skill"] = 0
    return out


def _post(key: str, scenario: dict) -> tuple[int, dict]:
    payload = {"state": scenario["state"], "model": MODEL, "questions": scenario["questions"]}
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _evaluate(scenario: dict, body: dict) -> dict:
    answers = body.get("answers", {})
    checks: dict[str, bool] = {}
    for qid, q in scenario["questions"].items():
        a = answers.get(qid, {})
        checks[f"{qid}.type"] = a.get("type") == q["type"]
        if q["type"] == "noul":
            v = a.get("noul")
            checks[f"{qid}.range"] = isinstance(v, (int, float)) and 0.0 <= v <= 1.0
        else:
            checks[f"{qid}.option"] = a.get("choice") in q["criteria"]
            conf = a.get("confidence")
            checks[f"{qid}.confidence_range"] = isinstance(conf, (int, float)) and 0.0 <= conf <= 1.0
    for rule, value in scenario.get("expect", {}).items():
        qid, key = rule.split(".")
        a = answers.get(qid, {})
        if key == "choice_in":
            checks[rule] = a.get("choice") in value
        elif key == "noul_min":
            checks[rule] = isinstance(a.get("noul"), (int, float)) and a["noul"] >= value
        elif key == "noul_max":
            checks[rule] = isinstance(a.get("noul"), (int, float)) and a["noul"] <= value
    typed = {qid: {"type": a.get("type"),
                   **({"noul": a.get("noul")} if a.get("type") == "noul" else
                      {"choice": a.get("choice"), "confidence": a.get("confidence")})}
             for qid, a in answers.items()}
    return {"checks": checks, "typed": typed,
            "passed": sum(1 for v in checks.values() if v),
            "total": len(checks),
            "ok": all(checks.values()) and bool(checks)}


def _summarize(runs: list[dict]) -> dict:
    recent = runs[-50:]
    total = len(runs)
    passed = sum(1 for r in runs if r.get("status") == "pass")
    last = runs[-1] if runs else None
    streak = 0
    for r in reversed(runs):
        if r.get("status") == "pass":
            streak += 1
        else:
            break
    # per-day pass ratio over the recent window
    by_day: dict[str, dict[str, int]] = {}
    for r in recent:
        day = (r.get("at") or "")[:10]
        d = by_day.setdefault(day, {"pass": 0, "fail": 0})
        d["pass" if r.get("status") == "pass" else "fail"] += 1
    return {
        "total_runs": total,
        "passed": passed,
        "failed": total - passed,
        "pass_ratio": round(passed / total, 4) if total else None,
        "current_pass_streak": streak,
        "last_run": last,
        "recent_by_day": by_day,
    }


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if not _acquire_lock():
        print(json.dumps({"error": "another jev-ghost cycle is running"}))
        return 0
    try:
        key = os.environ.get("TYPESAFE_API_KEY", "")
        state = _read_json(STATE_FILE, {"cycle": 0})
        cycle = int(state.get("cycle", 0))
        scenario = SCENARIOS[cycle % len(SCENARIOS)]

        run: dict = {"at": _now(), "cycle": cycle, "scenario": scenario["id"]}
        run["key_present"] = bool(key)
        if not key:
            run.update({"status": "fail", "error": "credential_not_present",
                        "duration_ms": 0})
        else:
            started = time.time()
            try:
                status, body = _post(key, scenario)
                evald = _evaluate(scenario, body)
                run.update({
                    "status": "pass" if evald["ok"] else "fail",
                    "http_status": status,
                    "model": body.get("model"),
                    "typed": evald["typed"],
                    "checks_passed": evald["passed"],
                    "checks_total": evald["total"],
                    "failed_checks": [k for k, v in evald["checks"].items() if not v],
                    "usage": body.get("usage"),
                })
            except urllib.error.HTTPError as exc:
                run.update({"status": "fail", "http_status": exc.code,
                            "error": "http_error", "duration_ms": int((time.time() - started) * 1000)})
            except Exception as exc:  # noqa: BLE001
                run.update({"status": "fail", "error": type(exc).__name__,
                            "duration_ms": int((time.time() - started) * 1000)})
            run.setdefault("duration_ms", int((time.time() - started) * 1000))

        run["skill_paths"] = _skill_probe()

        # append + trim history
        with RUNS_FILE.open("a") as fh:
            fh.write(json.dumps(run, sort_keys=True) + "\n")
        try:
            os.chmod(RUNS_FILE, 0o600)
        except OSError:
            pass
        lines = RUNS_FILE.read_text().splitlines()
        if len(lines) > MAX_RUNS:
            RUNS_FILE.write_text("\n".join(lines[-MAX_RUNS:]) + "\n")

        runs = []
        for line in RUNS_FILE.read_text().splitlines():
            try:
                runs.append(json.loads(line))
            except Exception:
                continue

        _atomic_write(LATEST_FILE, run)
        _atomic_write(SUMMARY_FILE, _summarize(runs))
        _atomic_write(STATE_FILE, {"cycle": cycle + 1, "updated_at": _now()})
        print(json.dumps({k: run[k] for k in ("at", "cycle", "scenario", "status",
                                              "http_status", "model", "checks_passed",
                                              "checks_total", "failed_checks", "error")
                          if k in run}, sort_keys=True))
        return 0
    finally:
        _release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
