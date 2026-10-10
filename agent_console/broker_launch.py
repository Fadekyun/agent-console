"""Native harness settings containing broker URLs and environment references."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .broker_client import BROKER_URL, CAPABILITY


def active(environment):
    return bool(environment.get(BROKER_URL) and environment.get(CAPABILITY))


def mcp_servers(environment):
    from .commandcode import MCP_SERVERS
    return [{**server, "url": environment[BROKER_URL] + "/mcp/" + server["name"],
             "token_env": CAPABILITY} for server in MCP_SERVERS]


def opencode_config(environment, config):
    if not active(environment):
        return config
    # OpenCode merges this per-process config after account/project settings.
    result = dict(config)
    result["mcp"] = {**result.get("mcp", {}), **{
        server["name"]: {"type": "remote", "url": server["url"], "enabled": True,
                         "oauth": False,
                         "headers": {"Authorization": "Bearer {env:" + CAPABILITY + "}"},
                         "timeout": server["request_timeout_ms"]}
        for server in mcp_servers(environment)}}
    providers = dict(result.get("provider", {}))
    router = dict(providers.get("openrouter", {}))
    router["options"] = {**router.get("options", {}),
                         "baseURL": environment[BROKER_URL] + "/model/openrouter/v1",
                         "apiKey": "{env:" + CAPABILITY + "}"}
    providers["openrouter"] = router
    result["provider"] = providers
    return result


def refresh_codex(home, environment):
    """Replace covered tables in the managed home; native -c deeply merges."""
    if not active(environment):
        return
    import tomlkit
    path = Path(home) / "config.toml"
    if path.is_symlink():
        raise ValueError("Managed Codex broker configuration cannot be a symbolic link")
    try:
        data = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
        if "mcp_servers" not in data:
            data["mcp_servers"] = tomlkit.table()
        for server in mcp_servers(environment):
            data["mcp_servers"][server["name"]] = {
                "url": server["url"], "bearer_token_env_var": CAPABILITY,
                "tool_timeout_sec": server["request_timeout_ms"] // 1000,
            }
        encoded = tomlkit.dumps(data)
    except (ValueError, TypeError, tomlkit.exceptions.TOMLKitError):
        raise ValueError("Managed Codex configuration is invalid; broker settings were not written") from None
    fd, temporary = tempfile.mkstemp(prefix=".broker-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def argv_for(tool, argv, environment, config_dir):
    if not active(environment):
        return argv
    if tool in {"codex", "codex-pro"}:
        args = list(argv)
        # Keep the pinned model/mode/resume args; replace only our four MCP
        # override tables on explicit restart, so flags do not accumulate.
        names = {s["name"] for s in mcp_servers(environment)}
        cleaned, i = [], 0
        while i < len(args):
            if (args[i] in {"-c", "--config"} and i + 1 < len(args)
                    and any(args[i + 1].startswith("mcp_servers." + name + "=") for name in names)):
                i += 2
            else:
                cleaned.append(args[i]); i += 1
        additions = []
        for server in mcp_servers(environment):
            # URLs stay pinned even if project settings change. Covered tables
            # in the managed native home are replaced by refresh_codex first.
            table = '{ url = ' + json.dumps(server["url"]) + ', bearer_token_env_var = ' + json.dumps(CAPABILITY)
            table += ', tool_timeout_sec = ' + str(server["request_timeout_ms"] // 1000) + ' }'
            additions += ["-c", "mcp_servers." + server["name"] + "=" + table]
        return [cleaned[0], *additions, *cleaned[1:]]
    if tool == "claude":
        from .environment import write_private
        path = Path(config_dir) / "broker-mcp.json"
        write_private(path, {"mcpServers": {s["name"]: {
            "type": "http", "url": s["url"],
            "headers": {"Authorization": "Bearer ${" + CAPABILITY + "}"}}
            for s in mcp_servers(environment)}})
        args = list(argv)
        # The managed option is stable across restarts and keeps other MCP files.
        if str(path) not in args:
            args.extend(["--mcp-config", str(path)])
        return args
    return argv


def refresh_native(tool, environment):
    """Explicit restart updates covered native entries, retaining other config."""
    if not active(environment) or tool not in {"pi", "hermes"}:
        return
    from .commandcode import (pi_mcp_config, hermes_mcp_servers,
                              ensure_pi_auth_env_reference, write_private_json)
    servers = mcp_servers(environment)
    base_url = environment[BROKER_URL] + "/model/commandcode/v1"
    if tool == "pi":
        root = Path(environment["PI_CODING_AGENT_DIR"])
        path = root / "models.json"
        models = json.loads(path.read_text())
        models["providers"]["commandcode"].update(baseUrl=base_url, apiKey="$" + CAPABILITY)
        write_private_json(path, models)
        ensure_pi_auth_env_reference(root / "auth.json", variable=CAPABILITY)
        mcp_path = root / "mcp.json"
        mcp = json.loads(mcp_path.read_text()) if mcp_path.exists() else {}
        mcp["mcpServers"] = {**mcp.get("mcpServers", {}), **pi_mcp_config(servers)["mcpServers"]}
        write_private_json(mcp_path, mcp)
    else:
        path = Path(environment["HERMES_HOME"]) / "config.yaml"
        config = json.loads(path.read_text())
        config["model"].update(base_url=base_url, api_key="${" + CAPABILITY + "}")
        config["mcp_servers"] = {**config.get("mcp_servers", {}), **hermes_mcp_servers(servers)}
        write_private_json(path, config)
