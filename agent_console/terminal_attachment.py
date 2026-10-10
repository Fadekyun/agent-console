"""Blocking terminal work runs outside the ASGI event loop."""
import fcntl
import os
import pty
import re
import struct
import subprocess
import termios
from contextlib import suppress

from .admission import admission_lock
from .validation import validate_session_name
from .tmux import session_missing_error


class AttachmentSessionEnded(RuntimeError):
    pass


def _current(manager, session_id, tmux):
    with manager.database.connect(busy_timeout_ms=500) as conn:
        current = conn.execute('SELECT tmux_name, socket_scope, execution_kind FROM sessions WHERE id=?', (session_id,)).fetchone()
    if current is None or current['socket_scope'] != tmux.scope:
        raise RuntimeError('connected session identity is unavailable')
    return current


def prepare_attachment(manager, session_id, tmux):
    master = slave = process = None
    try:
        # The same cross-process lock guards rename and name reuse. SQLite's
        # transaction ends before tmux or process creation can block.
        with admission_lock(manager.settings.state_dir):
            current = _current(manager, session_id, tmux)
            name = validate_session_name(current['tmux_name'])
            try:
                runtime_id = tmux.run('display-message', '-p', '-t', tmux.pane_target(name), '#{session_id}', timeout=2).stdout.strip()
            except RuntimeError as error:
                if session_missing_error(error):
                    raise AttachmentSessionEnded('session ended during attachment') from None
                raise
            if not re.fullmatch(r'\$[0-9]+', runtime_id):
                if not tmux.exists(name):
                    raise AttachmentSessionEnded('session ended during attachment')
                raise RuntimeError('invalid tmux session identity')
            master, slave = pty.openpty()
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
            process = subprocess.Popen(tmux.command('attach-session', '-t', runtime_id),
                stdin=slave, stdout=slave, stderr=slave, close_fds=True, start_new_session=True,
                env={**os.environ, 'TERM': 'xterm-256color'})
        return master, process, current['execution_kind'] == 'integration-plan'
    except BaseException:
        if process is not None and process.poll() is None:
            process.terminate()
            with suppress(subprocess.TimeoutExpired):
                process.wait(3)
        if master is not None:
            os.close(master)
        raise
    finally:
        if slave is not None:
            os.close(slave)


def scroll_attachment(manager, session_id, tmux, lines):
    with admission_lock(manager.settings.state_dir):
        current = _current(manager, session_id, tmux)
        tmux.scroll_history(validate_session_name(current['tmux_name']), lines)
