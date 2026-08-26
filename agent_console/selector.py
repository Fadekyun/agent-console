from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

from .manager import SessionManager
from .validation import PROFILES, TOOLS, validate_session_name, validate_tool


DIRECT_NAMES = {
    "codex": "codex-nocode",
    "codex-pro": "codex-pro-nocode",
    "claude": "claude",
    "opencode": "opencode",
    "hermes": "hermes",
}


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def choose(label: str, values: list[str], default: str) -> str:
    print(label)
    for index, value in enumerate(values, 1):
        marker = " (default)" if value == default else ""
        print(f"  {index}. {value}{marker}")
    raw = ask("Selection", str(values.index(default) + 1))
    try:
        return values[int(raw) - 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("invalid selection") from exc


def confirm_twice(prompt: str) -> bool:
    first = ask(f"{prompt} [y/N]").lower() in {"y", "yes"}
    if not first:
        return False
    return ask("Are you sure? [y/N]").lower() in {"y", "yes"}


def new_session(manager: SessionManager, scoped_tool: str | None) -> None:
    tool = scoped_tool or choose("Tool", sorted(TOOLS), "codex")
    contexts = manager.auth_contexts(tool)
    available = [item["name"] for item in contexts if item["status"] != "disabled"]
    if not available:
        reason = next(
            (item.get("reason") for item in contexts if item.get("reason")),
            "no enabled authentication context",
        )
        raise RuntimeError(f"{tool} unavailable: {reason}")
    default_context = next(
        (
            item["name"]
            for item in contexts
            if item["default"] and item["name"] in available
        ),
        available[0],
    )
    auth_context = choose("Authentication context", available, default_context)
    if tool == "opencode":
        agent_mode = choose("OpenCode mode", ["plan", "build"], "plan")
    elif tool in {"codex", "codex-pro"}:
        agent_mode = choose("Codex mode", ["auto", "plan"], "auto")
    else:
        agent_mode = None
    profile = choose("Profile", sorted(PROFILES), "general")
    name = ask("Session name (blank for generated)") or None
    task = ask("Initial task (optional)") or None
    repository = ask("Repository", str(manager.settings.workspace_root))
    default_worktree = profile in {"coder", "bugfix"}
    worktree = ask("Create isolated worktree?", "yes" if default_worktree else "no").lower() in {
        "y",
        "yes",
    }
    session = manager.create(
        tool=tool,
        profile=profile,
        name=name,
        task=task,
        repository=repository,
        worktree=worktree,
        creator_surface="SSH",
        auth_context=auth_context,
        agent_mode=agent_mode,
    )
    manager.attach(session["tmux_name"])


def manage_session(manager: SessionManager, name: str) -> None:
    action = choose("Action", ["inspect", "interrupt", "restart-agent", "kill", "back"], "inspect")
    if action == "back":
        return
    if action == "inspect":
        print(json.dumps(manager.inspect(name), indent=2, default=str))
    elif action == "interrupt":
        manager.interrupt(name)
        print("Interrupt sent; tmux remains running.")
    elif action == "restart-agent":
        manager.restart(name)
        print("Agent process restarted in the existing tmux session.")
    elif action == "kill":
        session = manager.inspect(name)
        if not confirm_twice(f"Kill {name}?"):
            print("Cancelled.")
            return
        manager.kill(name, allow_unmanaged=not session["managed"])
        print("Session killed. Associated worktrees were not removed.")


def interactive(manager: SessionManager, scoped_tool: str | None) -> int:
    while True:
        sessions = manager.list_sessions()
        if scoped_tool:
            direct_name = DIRECT_NAMES.get(scoped_tool)
            sessions = [
                item
                for item in sessions
                if item.get("tool") == scoped_tool or item["tmux_name"] == direct_name
            ]
        print(f"\n{(scoped_tool or 'Agent').title()} sessions\n")
        for index, session in enumerate(sessions, 1):
            kind = "legacy" if not session["managed"] else session.get("profile") or "managed"
            print(f"  {index}. Resume {session['tmux_name']} [{kind}; {session['status']}]")
        offset = len(sessions)
        print(f"  {offset + 1}. Start new session")
        print(f"  {offset + 2}. View or manage a session")
        print(f"  {offset + 3}. Open plain Zsh shell")
        print(f"  {offset + 4}. Exit")
        raw = ask("Selection")
        try:
            selected = int(raw)
        except ValueError:
            print("Invalid selection.")
            continue
        if 1 <= selected <= len(sessions):
            manager.attach(sessions[selected - 1]["tmux_name"])
        elif selected == offset + 1:
            new_session(manager, scoped_tool)
        elif selected == offset + 2:
            if not sessions:
                print("No sessions available.")
                continue
            name = ask("Exact session name")
            manage_session(manager, name)
        elif selected == offset + 3:
            os.execv("/usr/bin/zsh", ["zsh", "-l"])
        elif selected == offset + 4:
            return 0
        else:
            print("Invalid selection.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-selector")
    parser.add_argument("--tool", choices=sorted(TOOLS - {"shell"}))
    parser.add_argument("--direct")
    parser.add_argument("--session")
    args = parser.parse_args(argv)
    try:
        manager = SessionManager()
        if args.session:
            requested = args.session[3:] if args.session.startswith("ai-") else args.session
            manager.attach(validate_session_name(requested))
            return 0
        if args.direct:
            direct_tool = args.direct.removesuffix("-direct")
            name = DIRECT_NAMES[validate_tool(direct_tool)]
            if not manager.tmux_for_name(name).exists(name):
                session = manager.create(
                    tool=direct_tool,
                    profile="general",
                    name=name,
                    creator_surface="SSH",
                )
                name = session["tmux_name"]
            manager.attach(name)
            return 0
        if not sys.stdin.isatty():
            raise RuntimeError("interactive selector requires a TTY")
        return interactive(manager, args.tool)
    except (FileNotFoundError, FileExistsError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"agent-selector: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
