"""Outbound-request guard (SSRF defence) for URLs that come from data rather than from the operator's own configuration.

Used for dataset terms pages and dataset file downloads, whose URLs originate in the registry or in an official manifest. A URL is accepted only if it is http(s), has no
embedded credentials, and EVERY address its host resolves to is a public, globally routable address: loopback, private (RFC 1918 / ULA), link-local (including the cloud
metadata address 169.254.169.254), carrier-grade NAT, multicast, unspecified and reserved ranges are refused, as are IPv4-mapped forms of them. Redirects are re-checked at each
hop. Operator-configured endpoints (Supabase, an LLM server) are NOT passed through this guard: the operator chose them, and a self-hosted model on a private address is legitimate.

Known limit, stated plainly: the name is resolved here and again by the HTTP client, so a hostile DNS server could answer differently the second time (DNS rebinding).
Closing that fully needs connection pinning or an egress proxy; deployments should also restrict the backend's egress at the network level (docs/PRODUCTION_CONFIGURATION.md).
Test hook: COSMO_ALLOW_LOCAL_HTTP=1 permits plain http to 127.0.0.1/localhost only."""
from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from urllib.parse import urlparse


class Blocked(ValueError):
    """The destination is not allowed. The message is generic on purpose."""


def _public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def assert_public(url: str, env=os.environ, resolver=None) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise Blocked("That address is not allowed.")
    if env.get("COSMO_ALLOW_LOCAL_HTTP") == "1" and u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost"):
        return
    try:
        infos = (resolver or socket.getaddrinfo)(u.hostname, u.port or (443 if u.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise Blocked("That address could not be resolved.") from None
    addrs = {i[4][0] for i in infos}
    if not addrs or not all(_public(a) for a in addrs):
        raise Blocked("That address is not allowed.")


class _GuardedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        assert_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener() -> urllib.request.OpenerDirector:
    """An opener that re-checks every redirect target."""
    return urllib.request.build_opener(_GuardedRedirects)
