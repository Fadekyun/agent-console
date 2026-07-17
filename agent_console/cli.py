from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .manager import SessionManager
from .secrets_store import migrate_openrouter_secret, secret_status, set_openrouter_secret
from .skills import doctor_skills, sync_skills
from .validation import PROFILES, TOOLS


def emit(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def print_session_tree(tree: dict[str, Any]) -> None:
    def visit(nodes: list[dict[str, Any]], depth: int = 0) -> None:
        for node in nodes:
            detail = " · ".join(
                value
                for value in (
                    node.get("tool") or "legacy",
                    node.get("profile") or "legacy",
                    node.get("live_state") or "missing",
                    node.get("current_command") or "no process",
                )
                if value
            )
            print(f"{'  ' * depth}- {node['tmux_name']} ({detail})")
            if node.get("repository"):
                print(f"{'  ' * (depth + 1)}repository: {node['repository']}")
            if node.get("initial_task"):
                task = " ".join(str(node["initial_task"]).split())
                print(f"{'  ' * (depth + 1)}task: {task[:180]}")
            visit(node.get("children", []), depth + 1)

    visit(tree["roots"])


def confirm_typed(prompt: str, *, expected: str) -> bool:
    if not sys.stdin.isatty():
        return False
    return input(f"{prompt} Type {expected!r} to confirm: ").strip() == expected


def confirm_twice(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    first = input(f"{prompt} [y/N]: ").strip().lower() in {"y", "yes"}
    if not first:
        return False
    return input("Are you sure? [y/N]: ").strip().lower() in {"y", "yes"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="agentctl")
    root.add_argument("--json", action="store_true", help="emit JSON (currently the default format)")
    commands = root.add_subparsers(dest="command", required=True)

    session = commands.add_parser("session")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    session_commands.add_parser("list")
    tree = session_commands.add_parser("tree")
    tree.add_argument("--json", action="store_true")
    review = session_commands.add_parser("review")
    review.add_argument("name")
    review.add_argument("--lines", type=int, default=200)
    review.add_argument("--json", action="store_true")
    inspect = session_commands.add_parser("inspect")
    inspect.add_argument("name")
    context_show = session_commands.add_parser("context")
    context_show.add_argument("name", nargs="?")
    context_show.add_argument("--current", action="store_true")
    context_show.add_argument("--json", action="store_true")
    attention = session_commands.add_parser("attention")
    attention.add_argument("name", nargs="?")
    attention.add_argument("--current", action="store_true")
    attention.add_argument(
        "--state",
        required=True,
        choices=["normal", "needs_input", "blocked", "ready_for_review"],
    )
    attention.add_argument("--note")
    attach = session_commands.add_parser("attach")
    attach.add_argument("name")
    create = session_commands.add_parser("create")
    create.add_argument("--tool", required=True, choices=sorted(TOOLS))
    create.add_argument("--profile", default="general", choices=sorted(PROFILES))
    create.add_argument("--name")
    create.add_argument("--task")
    create.add_argument("--repository")
    create.add_argument("--worktree", action="store_true")
    create.add_argument("--auth-context")
    create.add_argument("--agent-mode", choices=["plan", "build", "auto"])
    create.add_argument("--provider", choices=["openrouter", "opencode-go"])
    create.add_argument("--model")
    interrupt = session_commands.add_parser("interrupt")
    interrupt.add_argument("name")
    restart = session_commands.add_parser("restart-agent")
    restart.add_argument("name")
    rename = session_commands.add_parser("rename")
    rename.add_argument("name")
    rename.add_argument("new_name")
    kill = session_commands.add_parser("kill")
    kill.add_argument("name")
    kill.add_argument("--yes", action="store_true")
    kill.add_argument("--allow-unmanaged", action="store_true")
    archive = session_commands.add_parser("archive")
    archive.add_argument("name")
    archive.add_argument("--kill", action="store_true")
    archive.add_argument("--yes", action="store_true")
    archive.add_argument("--allow-unmanaged", action="store_true")
    wait_children = session_commands.add_parser("wait-for-children")
    wait_children.add_argument("name")
    wait_children.add_argument("--timeout", type=int, default=None)
    wait_children.add_argument("--poll-interval", type=int, default=None)

    profile = commands.add_parser("profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list")
    profile_inspect = profile_commands.add_parser("inspect")
    profile_inspect.add_argument("name", choices=sorted(PROFILES))

    plan = commands.add_parser("plan")
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    plan_commands.add_parser("list")
    plan_inspect = plan_commands.add_parser("inspect")
    plan_inspect.add_argument("plan_id")
    plan_execute = plan_commands.add_parser("execute")
    plan_execute.add_argument("plan_id")
    plan_execute.add_argument("--profile", choices=["coder", "bugfix"], default="coder")
    plan_execute.add_argument("--name")
    plan_execute.add_argument("--allow-revision-change", action="store_true")
    plan_execute.add_argument("--yes", action="store_true")

    delegate = commands.add_parser("delegate")
    delegate.add_argument("profile", choices=["planner", "researcher", "reviewer", "scout"])
    delegate.add_argument("--parent", required=True)
    delegate.add_argument("--task", required=True)
    delegate.add_argument("--repository")
    delegate.add_argument("--tool", choices=sorted(TOOLS), default="codex")
    delegate.add_argument("--name")
    delegate.add_argument("--auth-context")
    delegate.add_argument("--agent-mode", choices=["plan", "build", "auto"])

    models = commands.add_parser("models")
    model_commands = models.add_subparsers(dest="models_command", required=True)
    model_list = model_commands.add_parser("list")
    model_list.add_argument("--provider", required=True, choices=["openrouter", "opencode-go"])
    model_list.add_argument("--refresh", action="store_true")
    model_list.add_argument("--json", action="store_true")
    estimate = model_commands.add_parser("estimate")
    estimate.add_argument("--provider", required=True, choices=["openrouter", "opencode-go"])
    estimate.add_argument("--uncached-input-tokens", type=int, default=0)
    estimate.add_argument("--cached-input-tokens", type=int, default=0)
    estimate.add_argument("--output-tokens", type=int, default=0)
    estimate.add_argument("--reasoning-tokens", type=int, default=0)

    skills = commands.add_parser("skills")
    skills_commands = skills.add_subparsers(dest="skills_command", required=True)
    skills_commands.add_parser("sync")
    skills_doctor = skills_commands.add_parser("doctor")
    skills_doctor.add_argument("--quiet", action="store_true")

    secrets = commands.add_parser("secrets")
    secrets_commands = secrets.add_subparsers(dest="secrets_command", required=True)
    secrets_set = secrets_commands.add_parser("set")
    secrets_set.add_argument("provider", choices=["openrouter"])
    secrets_set.add_argument("--credential", default="openrouter-main")
    secrets_commands.add_parser("status")
    secrets_commands.add_parser("migrate-openrouter")

    auth = commands.add_parser("auth")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_login = auth_commands.add_parser("login")
    auth_login.add_argument("tool", choices=sorted(TOOLS - {"shell"}))
    auth_login.add_argument("--context")
    context = auth_commands.add_parser("context")
    context_commands = context.add_subparsers(dest="context_command", required=True)
    context_list = context_commands.add_parser("list")
    context_list.add_argument("--tool", choices=sorted(TOOLS))
    context_add = context_commands.add_parser("add")
    context_add.add_argument("tool", choices=sorted(TOOLS))
    context_add.add_argument("name")
    context_add.add_argument("--provider", required=True)
    context_add.add_argument("--kind", required=True, choices=["oauth-native", "api-key", "none"])
    context_add.add_argument("--secret-ref")
    context_add.add_argument("--source-ref")
    context_add.add_argument("--disabled", action="store_true")
    context_add.add_argument("--verified", action="store_true")
    context_add.add_argument("--default", action="store_true")
    context_default = context_commands.add_parser("set-default")
    context_default.add_argument("tool", choices=sorted(TOOLS))
    context_default.add_argument("name")
    context_status = context_commands.add_parser("status")
    context_status.add_argument("--tool", choices=sorted(TOOLS))
    context_status.add_argument("--context")
    context_commands.add_parser("doctor")
    context_disable = context_commands.add_parser("disable")
    context_disable.add_argument("tool", choices=sorted(TOOLS))
    context_disable.add_argument("name")
    context_disable.add_argument("--reason", default="context disabled")

    commands.add_parser("doctor")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "skills":
            result = sync_skills() if args.skills_command == "sync" else doctor_skills()
            if not getattr(args, "quiet", False):
                emit(result)
            return 0 if result["ok"] else 1
        if args.command == "secrets":
            if args.secrets_command == "set":
                emit(set_openrouter_secret(credential=args.credential))
            elif args.secrets_command == "migrate-openrouter":
                emit(migrate_openrouter_secret())
            else:
                emit(secret_status())
            return 0
        manager = SessionManager()
        if args.command == "auth":
            if args.auth_command == "login":
                return manager.auth_login(args.tool, args.context)
            if args.context_command == "list":
                emit(manager.auth_contexts(args.tool))
            elif args.context_command == "add":
                emit(
                    manager.auth.add_context(
                        args.tool,
                        args.name,
                        provider=args.provider,
                        kind=args.kind,
                        secret_ref=args.secret_ref,
                        source_ref=args.source_ref,
                        enabled=not args.disabled,
                        verified=args.verified,
                        make_default=args.default,
                    )
                )
            elif args.context_command == "set-default":
                emit(manager.auth.set_default(args.tool, args.name))
            elif args.context_command == "status":
                emit(
                    manager.auth.get_context(args.tool, args.context)
                    if args.tool
                    else manager.tool_catalog()
                )
            elif args.context_command == "doctor":
                result = manager.auth.doctor()
                emit(result)
                return 0 if result["ok"] else 1
            elif args.context_command == "disable":
                emit(manager.auth.disable(args.tool, args.name, args.reason))
            return 0
        if args.command == "doctor":
            result = manager.doctor()
            emit(result)
            return 0 if result["ok"] else 1
        if args.command == "models":
            if args.models_command == "list":
                emit(manager.model_catalogue(args.provider, refresh=args.refresh))
            else:
                emit(manager.estimate_models(
                    args.provider,
                    uncached_input_tokens=args.uncached_input_tokens,
                    cached_input_tokens=args.cached_input_tokens,
                    output_tokens=args.output_tokens,
                    reasoning_tokens=args.reasoning_tokens,
                ))
            return 0
        if args.command == "session":
            if args.session_command == "list":
                emit(manager.list_sessions())
            elif args.session_command == "tree":
                tree_result = manager.session_tree()
                emit(tree_result) if args.json else print_session_tree(tree_result)
            elif args.session_command == "review":
                review_result = manager.review_session(args.name, lines=args.lines)
                if args.json:
                    emit(review_result)
                else:
                    session = review_result["session"]
                    print(
                        f"Session: {session['tmux_name']} · {session['live_state']} · "
                        f"{review_result['source']}"
                    )
                    print(review_result["notice"])
                    print("--- peer terminal output begins ---")
                    print(review_result["content"], end="" if review_result["content"].endswith("\n") else "\n")
                    print("--- peer terminal output ends ---")
            elif args.session_command == "inspect":
                emit(manager.inspect(args.name))
            elif args.session_command == "context":
                emit(manager.session_context(None if args.current else args.name))
            elif args.session_command == "attention":
                if args.current == bool(args.name):
                    raise ValueError("provide exactly one of NAME or --current")
                emit(
                    manager.set_attention(
                        None if args.current else args.name,
                        state=args.state,
                        note=args.note,
                    )
                )
            elif args.session_command == "attach":
                manager.attach(args.name)
            elif args.session_command == "create":
                emit(
                    manager.create(
                        tool=args.tool,
                        profile=args.profile,
                        name=args.name,
                        task=args.task,
                        repository=args.repository,
                        worktree=args.worktree,
                        auth_context=args.auth_context,
                        agent_mode=args.agent_mode,
                        provider=args.provider,
                        model=args.model,
                    )
                )
            elif args.session_command == "interrupt":
                emit(manager.interrupt(args.name))
            elif args.session_command == "restart-agent":
                emit(manager.restart(args.name))
            elif args.session_command == "rename":
                emit(manager.rename(args.name, args.new_name))
            elif args.session_command == "kill":
                if not args.yes and not confirm_twice("Kill the tmux session?"):
                    raise PermissionError("confirmation required")
                emit(manager.kill(args.name, allow_unmanaged=args.allow_unmanaged))
            elif args.session_command == "wait-for-children":
                result = manager.wait_for_children(
                    args.name,
                    timeout=args.timeout,
                    poll_interval=args.poll_interval,
                )
                if args.json:
                    emit(result)
                else:
                    outcome = result.get("outcome", "unknown")
                    exit_code = result.get("exit_code", 1)
                    print(f"Wait outcome: {outcome} (exit code {exit_code})")
                    for child in result.get("children", []):
                        status = child.get("wait_status", "unknown")
                        attn = child.get("attention_state", "normal")
                        name = child["tmux_name"]
                        note = f" — {child.get('attention_note', '')}" if child.get("attention_note") else ""
                        print(f"  {name}: {status} (attention: {attn}){note}")
                return result.get("exit_code", 1) or 0
            elif args.session_command == "archive":
                if args.kill and not args.yes and not confirm_twice(
                    "Archive and kill the tmux session?"
                ):
                    raise PermissionError("confirmation required")
                emit(
                    manager.archive(
                        args.name,
                        kill=args.kill,
                        allow_unmanaged=args.allow_unmanaged,
                    )
                )
        elif args.command == "profile":
            emit(
                manager.list_profiles()
                if args.profile_command == "list"
                else manager.inspect_profile(args.name)
            )
        elif args.command == "plan":
            if args.plan_command == "list":
                emit(manager.list_plans())
            elif args.plan_command == "inspect":
                emit(manager.inspect_plan(args.plan_id))
            elif args.plan_command == "execute":
                plan = manager.inspect_plan(args.plan_id)
                print(plan["plan"])
                print(f"\nRepository: {plan.get('repository') or 'from metadata'}")
                print(f"Implementation profile: {args.profile}")
                if not args.yes and not confirm_typed(
                    "Create a new isolated implementation session?", expected=args.plan_id
                ):
                    raise PermissionError("confirmation required")
                emit(
                    manager.execute_plan(
                        args.plan_id,
                        profile=args.profile,
                        name=args.name,
                        allow_revision_change=args.allow_revision_change,
                    )
                )
        elif args.command == "delegate":
            emit(
                manager.delegate(
                    profile=args.profile,
                    parent=args.parent,
                    task=args.task,
                    repository=args.repository,
                    tool=args.tool,
                    name=args.name,
                    auth_context=args.auth_context,
                    agent_mode=args.agent_mode,
                )
            )
        return 0
    except (FileNotFoundError, FileExistsError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
        print(f"agentctl: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
