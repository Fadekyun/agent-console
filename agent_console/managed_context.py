"""Lightweight managed-session routing checks, without opening local state."""
from dataclasses import dataclass, field
import os
from urllib.parse import urlsplit

REQUIRED_MARKERS = ('AGENT_CONSOLE_REPORTING_URL', 'AGENT_CONSOLE_SESSION_ID',
                    'AGENT_CONSOLE_EVIDENCE_CAPABILITY')
MARKERS = (*REQUIRED_MARKERS, 'AGENT_CONSOLE_SESSION_NAME', 'AGENT_CONSOLE_CONTEXT_FILE')
CONTEXT_ERROR = (
    'Managed session reporting context is incomplete or invalid. '
    'Use the configured AGENT_CONSOLE_REPORTING_URL and the session ID/capability '
    'provided by the Console launcher, or restart the session to refresh them. '
    'No local reader or writer fallback was attempted.'
)


@dataclass(frozen=True)
class ManagedContext:
    reporting_url: str
    session_id: str
    capability: str = field(repr=False)


def has_managed_markers():
    # Empty values are the existing local-owner/fixture convention for clearing
    # inherited markers. Any nonempty marker, even whitespace, requires checking.
    return any(os.getenv(key) for key in MARKERS)


def managed_context(*, required=False):
    if not has_managed_markers() and not required:
        return None
    values = [os.getenv(key, '') for key in REQUIRED_MARKERS]
    if any(not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) for value in values):
        raise PermissionError(CONTEXT_ERROR)
    url, identity, capability = values
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {'http', 'https'} and parsed.hostname
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment)
        parsed.port  # Reject malformed/out-of-range ports without echoing input.
    except ValueError:
        valid = False
    if not valid:
        raise PermissionError(CONTEXT_ERROR)
    return ManagedContext(url.rstrip('/'), identity, capability)
