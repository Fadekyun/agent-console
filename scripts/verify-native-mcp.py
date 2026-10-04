#!/usr/bin/env python3
"""Verify generated MCP config with installed Pi/Hermes and a loopback fixture.

No real credentials, model calls, external servers or host configuration are used.
Run with the Console Python, passing installed package/runtime paths explicitly.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console.auth import AuthRegistry
from agent_console.commandcode import BASE_URL, DEFAULT_MODEL, MCP_SERVERS
from agent_console.providers import provider_adapter


PI_PROBE = r"""
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
const [pkg, agentDir, cwd] = process.argv.slice(2);
assert.equal(JSON.parse(readFileSync(pkg + '/package.json')).version, '0.99.2');
const {loadMcpConfig} = await import(pathToFileURL(pkg + '/dist/extensions/mcp/config.js'));
const {McpServerConnection, createDefaultTransport} = await import(pathToFileURL(pkg + '/dist/extensions/mcp/runtime.js'));
const loaded = loadMcpConfig({agentDir, cwd, projectTrusted: false});
assert.deepEqual(loaded.errors, []);
assert.equal(loaded.servers.length, 4);
for (const entry of loaded.servers) {
  const connection = new McpServerConnection({entry, cwd, createTransport: createDefaultTransport,
    credentials: {forServer() {throw Error('Unexpected OAuth');}}, onTools() {}});
  try {
    await connection.getClient();
    assert.equal(connection.state, 'connected');
    assert.deepEqual(connection.tools.map(t => t.name), ['fixture_echo']);
    const result = await connection.callTool('fixture_echo', {}, {});
    assert.equal(result.content[0].text, 'native-mcp-ok');
    assert.equal(connection.timeoutMs, entry.name === 'bushi' ? 1200000 : entry.name === 'n8n' ? 180000 : 120000);
  } finally { await connection.close(); }
}
// Agent-directory placement is not complete isolation: trusted project wins.
mkdirSync(cwd + '/.pi', {recursive: true});
writeFileSync(cwd + '/.pi/mcp.json', JSON.stringify({mcpServers: {
  n8n: {url: 'http://127.0.0.1:1/project', enabled: false}
}}));
const trusted = loadMcpConfig({agentDir, cwd, projectTrusted: true});
assert.deepEqual(trusted.errors, []);
assert.equal(trusted.servers.find(s => s.name === 'n8n').scope, 'project');
assert.equal(trusted.servers.find(s => s.name === 'n8n').config.enabled, false);
const disabledDir = agentDir + '/disabled';
mkdirSync(disabledDir);
writeFileSync(disabledDir + '/mcp.json', readFileSync(agentDir + '/disabled.json'));
const disabled = loadMcpConfig({agentDir: disabledDir, cwd, projectTrusted: false});
assert.deepEqual(disabled.errors, []);
assert.equal(disabled.servers.length, 4);
assert(disabled.servers.every(s => s.config.enabled === false && !s.config.headers));
console.log(JSON.stringify({harness: 'pi', version: '0.99.2', connected: 4, calls: 4,
  disabled_validated: 4, trusted_project_override_verified: true}));
"""

HERMES_PROBE = r"""
import asyncio, json, os, sys
sys.path.insert(0, sys.argv[1])
from hermes_cli import __version__
from tools.mcp_tool_config import _load_mcp_config
from tools.mcp_tool import MCPServerTask
assert __version__ == '0.21.4', __version__
configs = _load_mcp_config()
assert set(configs) == {'n8n', 'directus', 'openrouter', 'bushi'}, list(configs)
async def check():
    for name, config in configs.items():
        connection = MCPServerTask(name)
        try:
            await asyncio.wait_for(connection.start(config), timeout=10)
            tools = await connection.session.list_tools()
            assert [tool.name for tool in tools.tools] == ['fixture_echo']
            result = await connection.session.call_tool('fixture_echo', {})
            assert result.content[0].text == 'native-mcp-ok'
            assert connection.tool_timeout == (1200 if name == 'bushi' else 180 if name == 'n8n' else 120)
        finally:
            await connection.shutdown()
asyncio.run(check())
print(json.dumps(dict(harness='hermes', version=__version__, connected=4, calls=4)))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi-root', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--hermes-python', type=Path, required=True)
    args = parser.parse_args()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, status, body=None):
            raw = json.dumps(body).encode() if body is not None else b''
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self.reply(405, {'error': 'POST only'})

        def do_DELETE(self):
            self.reply(200, {})

        def do_POST(self):
            if self.headers.get('Authorization') != 'Bearer console-fixture-only':
                self.reply(401, {'error': 'fixture auth required'})
                return
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method = data.get('method')
            if 'id' not in data:
                self.reply(202)
                return
            if method == 'initialize':
                result = {'protocolVersion': data['params']['protocolVersion'],
                          'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'console-fixture', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'fixture_echo', 'description': 'Fixture only',
                                    'inputSchema': {'type': 'object', 'properties': {}}}]}
            elif method == 'tools/call':
                calls.append(self.path)
                result = {'content': [{'type': 'text', 'text': 'native-mcp-ok'}]}
            elif method == 'ping':
                result = {}
            else:
                self.reply(200, {'jsonrpc': '2.0', 'id': data['id'],
                                 'error': {'code': -32601, 'message': 'Unknown fixture method'}})
                return
            self.reply(200, {'jsonrpc': '2.0', 'id': data['id'], 'result': result})

    with tempfile.TemporaryDirectory(prefix='console-native-mcp-') as temporary:
        root = Path(temporary)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Deliberately clean environment; no inherited secret or profile source.
            env = {'PATH': os.defpath, 'HOME': str(root / 'home'),
                   'XDG_CONFIG_HOME': str(root / 'config'), 'LANG': 'C.UTF-8'}
            Path(env['HOME']).mkdir()
            for item in MCP_SERVERS:
                env[item['token_env']] = 'console-fixture-only'
                env[item['url_env']] = f'http://127.0.0.1:{server.server_port}/{item["name"]}'
            context_path = root / 'context.md'
            context_path.write_text('Native MCP verification fixture only.\n')
            project = root / 'project'
            project.mkdir()
            with patch.dict(os.environ, env, clear=True):
                registry = AuthRegistry(root / 'registry', home=root / 'home')
                context = dict(provider='commandcode', base_url=BASE_URL, model=DEFAULT_MODEL,
                               models=[DEFAULT_MODEL], verified=True, secret_ref='commandcode-main')
                specs = {tool: provider_adapter(tool, registry).build_launch_spec(
                    context=context, context_path=context_path, model=None, role='fixture',
                    profile='general', cwd=project, read_only=False, agent_mode=None)
                    for tool in ('pi', 'hermes')}
                from agent_console.commandcode import pi_mcp_config
                pi_dir = Path(specs['pi'].environment['PI_CODING_AGENT_DIR'])
                (pi_dir / 'disabled.json').write_text(json.dumps(pi_mcp_config([])))
            pi_script = root / 'probe.mjs'
            pi_script.write_text(PI_PROBE)
            commands = [
                ([str(args.node), str(pi_script), str(args.pi_root), str(pi_dir), str(project)],
                 {**env, **specs['pi'].environment}),
                ([str(args.hermes_python), '-c', HERMES_PROBE, str(args.hermes_root)],
                 {**env, **specs['hermes'].environment}),
            ]
            for command, child_env in commands:
                completed = subprocess.run(command, env=child_env, cwd=project, capture_output=True,
                                           text=True, timeout=60)
                if completed.returncode:
                    sys.stderr.write(completed.stderr)
                    raise SystemExit(completed.returncode)
                print(completed.stdout.strip())
            assert len(calls) == 8, calls
            print(json.dumps({'authenticated_fixture_calls': len(calls), 'external_calls': 0}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
