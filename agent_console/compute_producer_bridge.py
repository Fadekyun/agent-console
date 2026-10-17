"""Standalone stdlib-only scoped producer bridge and client (no agent authority).

Install this exact file root-owned. Forced command: python3 -I FILE bridge
--config /etc/agent-console/producers/NAME.json. Never interpolate the requested
SSH command. Client request failures are ambiguous; this module never retries.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

PREFIX = '/api/compute/producers'
MAX_REQUEST = 512 * 1024
MAX_RESPONSE = 1024 * 1024
CHUNK = 256 * 1024
MAX_ARTIFACT = 2 * 1024 ** 3
SECONDS = 25
IDENTIFIER = r'[A-Za-z0-9_-]{1,100}'
DIGEST = r'[a-f0-9]{64}'


class TransportError(ValueError):
    """Request outcome may be unknown. Never retry a publication automatically."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def decode(data):
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('duplicate JSON key')
            obj[key] = value
        return obj
    value = json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def protected_file(path, *, secret=False, root_only=True):
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError('absolute configured path required')
    # A safe file must also be protected against ancestor rename/replacement.
    for parent in Path(path).parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('configuration directory is not protected')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        owners = {0} if root_only else {0, os.geteuid()}
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in owners
                or info.st_mode & (0o027 if secret else 0o022) or info.st_size > 65536):
            raise ValueError('configured file is not protected')
        return stream.read(65537)


def load_config(path):
    return decode(protected_file(path, secret=True))


def validate_request(value):
    if (set(value) - {'version', 'method', 'path', 'body'}
            or type(value.get('version')) is not int or value['version'] != 1
            or value.get('method') not in {'GET', 'POST'} or not isinstance(value.get('path'), str)):
        raise ValueError('invalid producer envelope')
    method, path = value['method'], value['path']
    if len(path) > 500 or not path.startswith(PREFIX + '/'):
        raise ValueError('route not permitted')
    route = path[len(PREFIX):]
    if method == 'POST':
        allowed = [r'/jobs', rf'/artifacts/{DIGEST}',
                   rf'/jobs/{IDENTIFIER}/(?:publication|consume)', rf'/publications/{IDENTIFIER}/finish']
        if not isinstance(value.get('body'), dict):
            raise ValueError('POST body object required')
    else:
        allowed = [rf'/jobs/{IDENTIFIER}', rf'/publications/{IDENTIFIER}',
                   rf'/jobs/{IDENTIFIER}/artifacts/{DIGEST}(?:\?offset=[0-9]{{1,10}}&length=[0-9]{{1,6}})?']
        if value.get('body') is not None:
            raise ValueError('GET body not permitted')
    if not any(re.fullmatch(pattern, route) for pattern in allowed):
        raise ValueError('route not permitted')
    if '?' in route:
        offset, length = (int(part.split('=')[1]) for part in route.split('?', 1)[1].split('&'))
        if not 0 <= offset <= MAX_ARTIFACT or not 1 <= length <= CHUNK:
            raise ValueError('artifact coordinates exceed bounds')
    if len(canonical(value)) + 1 > MAX_REQUEST:
        raise ValueError('request exceeds bound')
    return value


def credentials(config):
    producer, token = config.get('producer'), config.get('token')
    if (not isinstance(producer, str) or not re.fullmatch(IDENTIFIER, producer)
            or not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9._~-]{32,128}', token)):
        raise ValueError('invalid producer credentials')
    return {'X-Compute-Producer': producer, 'Authorization': 'Bearer ' + token,
            'Content-Type': 'application/json', 'Accept': 'application/json'}


def http_call(endpoint, request, headers):
    parsed = urlsplit(endpoint)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {'', '/'} or not parsed.hostname):
        raise ValueError('endpoint must be an origin')
    if parsed.scheme == 'https':
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                timeout=SECONDS, context=ssl.create_default_context())
    elif parsed.scheme == 'http' and ipaddress.ip_address(parsed.hostname).is_loopback:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=SECONDS)
    else:
        raise ValueError('TLS or literal loopback endpoint required')
    deadline = time.monotonic() + SECONDS
    try:
        body = canonical(request['body']) if request['method'] == 'POST' else None
        connection.request(request['method'], request['path'], body=body, headers=headers)
        response = connection.getresponse()
        output = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError('producer response timeout')
            if connection.sock:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - len(output)))
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_RESPONSE:
                raise TransportError('producer response exceeds bound')
        # http.client neither follows redirects nor reads proxy environment.
        return {'status': response.status, 'body': decode(bytes(output))}
    except (OSError, http.client.HTTPException, ValueError):
        raise TransportError('producer HTTP response unavailable') from None
    finally:
        connection.close()


def bridge(request, config):
    validate_request(request)
    if set(config) != {'producer', 'token'}:
        raise ValueError('invalid bridge configuration')
    return http_call('http://127.0.0.1:3210', request, credentials(config))


def bounded_process(command, data):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
    output, sent, deadline = bytearray(), 0, time.monotonic() + SECONDS + 5
    try:
        with selectors.DefaultSelector() as selector:
            for stream, event in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError('producer SSH timeout')
                ready = selector.select(remaining)
                if not ready:
                    raise TransportError('producer SSH timeout')
                for key, event in ready:
                    if event & selectors.EVENT_WRITE:
                        sent += os.write(key.fd, data[sent:sent + 65536])
                        if sent == len(data):
                            selector.unregister(key.fileobj)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, min(65536, MAX_RESPONSE + 1 - len(output)))
                        if not chunk:
                            selector.unregister(key.fileobj)
                        output.extend(chunk)
                        if len(output) > MAX_RESPONSE:
                            raise TransportError('producer SSH response exceeds bound')
        if process.wait(timeout=max(.001, deadline-time.monotonic())):
            raise TransportError('producer SSH unavailable')
        response = decode(bytes(output))
        if (set(response) != {'status', 'body'} or type(response['status']) is not int
                or not 100 <= response['status'] <= 599 or not isinstance(response['body'], dict)):
            raise TransportError('invalid producer SSH response')
        return response
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise TransportError('producer SSH response unavailable') from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()


class ProducerClient:
    """One call per operation; durable callers reconcile unknown outcomes."""
    def __init__(self, config):
        self.config = config
        self.command = None
        if config.get('transport') == 'ssh-stdio':
            if set(config) != {'transport', 'ssh_host', 'ssh_identity_file', 'ssh_known_hosts_file'}:
                raise ValueError('invalid SSH client configuration')
            host = config['ssh_host']
            if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}@[A-Za-z0-9][A-Za-z0-9_.-]{0,252}', host):
                raise ValueError('SSH destination must be user@host')
            protected_file(config['ssh_identity_file'], secret=True, root_only=False)
            protected_file(config['ssh_known_hosts_file'])
            self.command = ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-i', config['ssh_identity_file'],
                '-o', 'UserKnownHostsFile=' + config['ssh_known_hosts_file'],
                '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1', '-o', 'LogLevel=ERROR',
                '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
                '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
                '-o', 'SendEnv=-*', '-o', 'ControlMaster=no', '-o', 'ControlPath=none', host]
        elif config.get('transport') == 'https':
            if set(config) != {'transport', 'endpoint', 'producer', 'token'}:
                raise ValueError('invalid HTTPS client configuration')
            credentials(config)
        else:
            raise ValueError('unsupported producer transport')

    @classmethod
    def from_file(cls, path):
        return cls(load_config(path))

    def request(self, method, path, body=None):
        value = {'version': 1, 'method': method, 'path': path}
        if body is not None:
            value['body'] = body
        validate_request(value)
        if self.command:
            return bounded_process(self.command, canonical(value) + b'\n')
        return http_call(self.config['endpoint'], value, credentials(self.config))

    def upload(self, filename):
        with open(filename, 'rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or not 0 < size <= MAX_ARTIFACT:
                raise ValueError('invalid upload size')
            artifact_id = hashlib.file_digest(stream, 'sha256').hexdigest()
            offset, steps = 0, 0
            while True:
                stream.seek(offset)
                chunk = stream.read(min(CHUNK, size-offset))
                result = self.request('POST', PREFIX + '/artifacts/' + artifact_id,
                    {'size': size, 'offset': offset, 'data': base64.b64encode(chunk).decode('ascii'),
                     'final': offset + len(chunk) == size})
                if not 200 <= result['status'] < 300:
                    return result
                body = result['body']
                next_offset = body.get('offset')
                if (type(next_offset) is not int or not 0 <= next_offset <= size
                        or type(body.get('complete')) is not bool):
                    raise TransportError('invalid upload receipt')
                if body['complete']:
                    if next_offset != size:
                        raise TransportError('incomplete upload receipt')
                    return {'status': 200, 'body': {'sha256': artifact_id, 'size': size}}
                steps += 1
                if steps > (size + CHUNK - 1) // CHUNK + 2 or (next_offset == offset and offset != size):
                    raise TransportError('upload made no progress')
                offset = next_offset

    def fetch(self, job_id, artifact_id, filename):
        if not re.fullmatch(IDENTIFIER, job_id) or not re.fullmatch(DIGEST, artifact_id):
            raise ValueError('invalid result binding')
        destination = Path(filename)
        # Exclusive creation prevents accidental overwrite; partial transfers are
        # removed on failure and never exposed as a completed result.
        fd, temporary = tempfile.mkstemp(prefix='.compute-fetch-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                offset, expected_size, sha = 0, None, hashlib.sha256()
                while True:
                    result = self.request('GET', PREFIX + '/jobs/' + job_id + '/artifacts/' + artifact_id
                        + '?offset=' + str(offset) + '&length=' + str(CHUNK))
                    if not 200 <= result['status'] < 300:
                        return result
                    body = result['body']
                    size = body.get('size')
                    if (body.get('artifact_id') != artifact_id or body.get('offset') != offset
                            or type(size) is not int or not 0 < size <= MAX_ARTIFACT
                            or (expected_size is not None and expected_size != size)
                            or type(body.get('eof')) is not bool):
                        raise TransportError('invalid artifact response')
                    expected_size = size
                    chunk = base64.b64decode(body['data'], validate=True)
                    if not chunk or len(chunk) > CHUNK or offset + len(chunk) > size:
                        raise TransportError('invalid artifact chunk')
                    stream.write(chunk)
                    sha.update(chunk)
                    offset += len(chunk)
                    if body['eof']:
                        if offset != size or sha.hexdigest() != artifact_id:
                            raise TransportError('artifact digest mismatch')
                        stream.flush()
                        os.fsync(stream.fileno())
                        break
            os.link(temporary, destination)
            directory = os.open(destination.parent, os.O_DIRECTORY | os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return {'status': 200, 'body': {'sha256': artifact_id, 'size': expected_size}}
        finally:
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('bridge', 'request', 'upload', 'fetch'))
    parser.add_argument('--config', required=True)
    parser.add_argument('--file')
    parser.add_argument('--job')
    parser.add_argument('--artifact')
    args = parser.parse_args()
    try:
        if args.action in {'bridge', 'request'}:
            # Forced SSH clients must close stdin after one bounded JSON object.
            signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TransportError('producer input timeout')))
            signal.alarm(10)
            data = sys.stdin.buffer.read(MAX_REQUEST + 1)
            signal.alarm(SECONDS + 5)
            if len(data) > MAX_REQUEST:
                raise ValueError('request exceeds bound')
            request = validate_request(decode(data))
            config = load_config(args.config)
            result = bridge(request, config) if args.action == 'bridge' else ProducerClient(config).request(
                request['method'], request['path'], request.get('body'))
        else:
            client = ProducerClient.from_file(args.config)
            if not args.file:
                raise ValueError('local file required')
            result = client.upload(args.file) if args.action == 'upload' else client.fetch(args.job, args.artifact, args.file)
        encoded = canonical(result)
        if len(encoded) + 1 > MAX_RESPONSE:
            raise TransportError('producer response exceeds bound')
        sys.stdout.buffer.write(encoded + b'\n')
        return 0
    except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
        # No credentials, caller content, paths, original command or exceptions.
        sys.stdout.write('{"status":502,"body":{"detail":"Producer bridge request unavailable; reconcile before retry"}}\n')
        return 0
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
