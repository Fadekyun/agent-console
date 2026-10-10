#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
import shutil
import subprocess


BEGIN = "# BEGIN N100 AGENT CONSOLE"
END = "# END N100 AGENT CONSOLE"


def remove_block(text: str) -> str:
    output: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        marker = line.rstrip("\r\n")
        if marker == BEGIN:
            inside = True
            continue
        if marker == END and inside:
            inside = False
            continue
        if not inside:
            output.append(line)
    return "".join(output).lstrip("\r\n")


def quote_path(path: Path) -> str:
    return '"' + str(path).replace('"', '\\"') + '"'


def managed_block(tailscale_host: str, lan_ip: str, identity: Path, user: str) -> str:
    key = quote_path(identity)
    return f"""{BEGIN}
# Web console: https://{tailscale_host}/
# Off-LAN SSH reaches {lan_ip} through the Tailscale subnet route advertised by CT 969.
Host n100-lan
    HostName {lan_ip}
    User {user}
    IdentityFile {key}
    IdentitiesOnly yes

Host codex claude opencode hermes
    HostName {lan_ip}
    User {user}
    IdentityFile {key}
    IdentitiesOnly yes
    RequestTTY force
    RemoteCommand /home/{user}/bin/agent-selector --tool %n

Host codex-direct claude-direct opencode-direct hermes-direct
    HostName {lan_ip}
    User {user}
    IdentityFile {key}
    IdentitiesOnly yes
    RequestTTY force
    RemoteCommand /home/{user}/bin/agent-selector --direct %n

Host ai-*
    HostName {lan_ip}
    User {user}
    IdentityFile {key}
    IdentitiesOnly yes
    RequestTTY force
    RemoteCommand /home/{user}/bin/agent-selector --session %n
{END}
"""


def validate_aliases(config: Path) -> None:
    if shutil.which("ssh") is None:
        return
    for alias in ("n100-lan", "codex", "claude", "opencode", "hermes", "codex-direct", "ai-test"):
        subprocess.run(
            ["ssh", "-F", str(config), "-G", alias],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install N100 Agent Console SSH aliases")
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--tailscale-host")
    parser.add_argument("--lan-ip")
    parser.add_argument("--identity-file")
    parser.add_argument("--user", default=os.getenv("USER", "user"))
    parser.add_argument("--config", type=Path, default=Path.home() / ".ssh" / "config")
    args = parser.parse_args()

    config = args.config.expanduser().resolve()
    config.parent.mkdir(parents=True, exist_ok=True)
    existing = config.read_text(encoding="utf-8") if config.exists() else ""
    without_managed = remove_block(existing)

    if args.action == "uninstall":
        config.write_text(without_managed, encoding="utf-8")
        config.chmod(0o600)
        print("Removed only the N100 Agent Console managed SSH block.")
        return 0

    if not args.tailscale_host or not args.lan_ip or not args.identity_file:
        parser.error("install requires --tailscale-host, --lan-ip, and --identity-file")
    identity = Path(args.identity_file).expanduser().resolve()
    if not identity.is_file():
        parser.error(f"private key not found: {identity}")

    if config.exists():
        backup = config.with_name(config.name + ".bak." + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(config, backup)
    else:
        backup = None
    config.write_text(
        managed_block(args.tailscale_host, args.lan_ip, identity, args.user)
        + "\n"
        + without_managed,
        encoding="utf-8",
    )
    config.chmod(0o600)
    validate_aliases(config)
    print("Installed: n100-lan, codex, claude, opencode, hermes, *-direct, ai-*")
    if backup:
        print(f"Backup: {backup}")
    print(f"Web console: https://{args.tailscale_host}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
