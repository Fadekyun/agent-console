"""Shared HTTP/WebSocket identity and browser-origin checks.

Only the socket peer can establish forwarded identity provenance. Serve with
Uvicorn --no-proxy-headers; X-Forwarded-For must never choose this peer.
"""
from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AuthContext:
    actor: str
    access_surface: str


class IdentityDenied(Exception):
    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def proxy_networks(raw):
    return tuple(ipaddress.ip_network(value.strip(), strict=False)
                 for value in raw.split(',') if value.strip())


def normalized_origin(value):
    """Return a canonical exact scheme/host/port origin; refuse URL extras."""
    if not isinstance(value, str) or value != value.strip() or any(ord(c) < 33 for c in value):
        raise ValueError('Origin must be an exact HTTP(S) origin')
    parsed = urlsplit(value)
    if (parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or '\\' in value):
        raise ValueError('Origin must contain only HTTP(S) scheme, host and port')
    host = parsed.hostname.lower().encode('idna').decode('ascii')
    port = parsed.port if parsed.port is not None else (443 if parsed.scheme.lower() == 'https' else 80)
    if not 1 <= port <= 65535:
        raise ValueError('Origin port is invalid')
    return parsed.scheme.lower(), host, port


def allowed_origins(raw):
    return frozenset(normalized_origin(value.strip()) for value in raw.split(',') if value.strip())


def authorize_identity(connection, *, expected_login, lan_network, trusted_proxies):
    if not expected_login:
        raise IdentityDenied(503, 'Tailscale login allowlist is not configured')
    peer = connection.client.host if connection.client else ''
    try:
        address = ipaddress.ip_address(peer)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
    except ValueError:
        address = None
    trusted_peer = address is not None and any(address in network for network in trusted_proxies)
    identities = connection.headers.getlist('tailscale-user-login')
    if identities:
        # Presence of even an empty or duplicate forwarded header is not a
        # fallback to LAN access: reject instead of trusting ambiguous input.
        if (not trusted_peer or len(identities) != 1 or identities[0].strip().lower() != expected_login):
            raise IdentityDenied(403, 'Forwarded identity requires an authorized login and trusted proxy peer')
        return AuthContext(actor=expected_login, access_surface='tailscale')
    if trusted_peer:
        raise IdentityDenied(403, 'Trusted proxy requests require an authenticated identity header')
    if address is None or address not in lan_network:
        raise IdentityDenied(403, 'Tailscale identity or trusted LAN is required')
    return AuthContext(actor=peer, access_surface='local-lan')


def authorize_browser_origin(connection, permitted):
    origins = connection.headers.getlist('origin')
    # Non-browser/native clients may omit Origin, but must still pass exactly
    # the same peer/identity checks as HTTP. Browsers always provide Origin.
    if not origins:
        return
    try:
        valid = len(origins) == 1 and normalized_origin(origins[0]) in permitted
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise IdentityDenied(403, 'WebSocket browser origin is not allowed')
