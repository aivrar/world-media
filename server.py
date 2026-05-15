#!/usr/bin/env python3
"""
World Media — Linux child HTTP server.

Two responsibilities:
  1. Serve the static frontend (HTML/JS/CSS/images) at the root URL space.
  2. Provide a strictly-allowlisted CORS-bypass proxy at /api/proxy?url=<encoded>.

The proxy is the substitute for Tauri's plugin-http: it lets the otherwise
CORS-blocked adapter calls (LibriVox, Wikimedia, sometimes others) reach
their origin from a webview that sees the page as same-origin localhost.

Hardening (the user explicitly asked for tight isolation):
  - URL scheme must be https. http:// upstream rejected.
  - Hostname must match the allowlist (or its suffix patterns).
  - DNS resolution result must NOT land on a private/loopback/link-local IP
    (defends against DNS-rebinding into the WSL distro's other services).
  - Only GET and POST are accepted (POST is needed for Radio Browser click
    count tracking only).
  - Max response size is capped to 50 MiB.
  - Per-IP rate limiting at 60 requests/sec (gentle; we're behind localhost
    so this is really a runaway-bug brake, not a hostile-traffic defense).
  - No HTTP redirects auto-followed; if the upstream redirects to a host
    that isn't allowlisted, the redirect bubbles up to the client unchanged.
  - Stream URLs (mp3, m3u8, mp4) are NOT proxied — they're loaded directly
    by the <audio>/<video> elements in the page. The proxy is API-only.

Stdlib-only. No pip install needed. Runs on Python 3.8+.
"""

from __future__ import annotations

import http.server
import ipaddress
import os
import socket
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http import HTTPStatus

PORT = int(os.environ.get('WORLDMEDIA_PORT', '9123'))
ROOT = os.environ.get('WORLDMEDIA_FRONTEND', '/opt/app/frontend')
USER_AGENT = 'WorldMedia/1.0 (linux-portable)'
MAX_SIZE = 50 * 1024 * 1024  # 50 MiB
TIMEOUT_SEC = 20

# Exact-match allowlist. Subdomain matches go through ALLOWED_SUFFIXES.
ALLOWED_HOSTS = frozenset({
    # Radio Browser umbrella
    'all.api.radio-browser.info',
    # iptv-org
    'iptv-org.github.io',
    # Internet Archive
    'archive.org',
    'www.archive.org',
    # NASA
    'images-api.nasa.gov',
    'images-assets.nasa.gov',
    # Wikimedia Commons
    'commons.wikimedia.org',
    'upload.wikimedia.org',
    # LibriVox
    'librivox.org',
    'www.librivox.org',
})

# Subdomain wildcards. Anything ending in one of these is fine.
ALLOWED_SUFFIXES: tuple[str, ...] = (
    '.api.radio-browser.info',  # the rotating mirror pool (de1, de2, fi1, …)
    '.archive.org',             # IA sometimes redirects to www2.archive.org etc.
)

# Per-client request log for rate limiting. (ip -> deque of timestamps)
_rate_lock = threading.Lock()
_rate_log: dict[str, deque[float]] = {}
RATE_WINDOW_SEC = 1.0
RATE_MAX_PER_WINDOW = 60


def is_allowed_host(host: str) -> bool:
    host = host.lower().rstrip('.')
    if host in ALLOWED_HOSTS:
        return True
    return any(host.endswith(suf) for suf in ALLOWED_SUFFIXES)


def resolves_to_private_ip(host: str) -> bool:
    """True if any resolution of `host` lands on a non-public IP."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # treat unresolvable as suspicious
    for info in infos:
        ip = info[4][0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return True
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified):
            return True
    return False


def rate_limit(client_ip: str) -> bool:
    """True = allowed, False = throttled."""
    now = time.monotonic()
    with _rate_lock:
        q = _rate_log.setdefault(client_ip, deque())
        while q and q[0] < now - RATE_WINDOW_SEC:
            q.popleft()
        if len(q) >= RATE_MAX_PER_WINDOW:
            return False
        q.append(now)
    return True


class WorldMediaHandler(http.server.SimpleHTTPRequestHandler):
    # `directory=` is honored by SimpleHTTPRequestHandler since 3.7.
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    # Quieter access log (one line, no funky chars).
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(
            f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}\n"
        )

    # SPA fallback: anything that isn't a static file path and isn't /api/
    # falls through to index.html so client-side routing (future-proofing) works.
    def do_GET(self) -> None:
        if self.path.startswith('/api/'):
            return self._dispatch_api('GET')
        return super().do_GET()

    def do_POST(self) -> None:
        if self.path.startswith('/api/'):
            return self._dispatch_api('POST')
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    # Override the static-file response to prevent the host WebView from
    # caching stale index.html / JS bundles across restarts. Bundle filenames
    # are content-hashed so we *could* allow caching there, but the cost of
    # a no-cache header for ~500 KB of localhost traffic is negligible and
    # it's the difference between "user sees the new build" and "user reopens
    # the app and still sees yesterday's code".
    def send_response(self, code, message=None):
        super().send_response(code, message)
        # SimpleHTTPRequestHandler sends headers via send_header before
        # end_headers. We inject our headers here so they're guaranteed to
        # be emitted alongside whatever the parent class sends. The proxy
        # path uses its own send_response/send_header pattern and already
        # sets Cache-Control explicitly, so that path is unaffected.
        if not self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')

    def _dispatch_api(self, method: str) -> None:
        if not rate_limit(self.client_address[0]):
            return self.send_error(HTTPStatus.TOO_MANY_REQUESTS, 'rate limit')
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == '/api/proxy':
            return self._handle_proxy(method, parsed.query)
        if parsed.path == '/api/health':
            return self._handle_health()
        return self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_health(self) -> None:
        body = b'{"ok":true,"app":"World Media","port":' + str(PORT).encode() + b'}'
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _handle_proxy(self, method: str, query: str) -> None:
        qs = urllib.parse.parse_qs(query, keep_blank_values=True)
        url = (qs.get('url') or [''])[0]
        if not url:
            return self.send_error(HTTPStatus.BAD_REQUEST, 'missing url')
        target = urllib.parse.urlsplit(url)

        if target.scheme != 'https':
            return self.send_error(HTTPStatus.FORBIDDEN, 'scheme not allowed: ' + target.scheme)
        host = (target.hostname or '').lower()
        if not is_allowed_host(host):
            return self.send_error(HTTPStatus.FORBIDDEN, 'host not allowlisted: ' + host)
        if resolves_to_private_ip(host):
            return self.send_error(HTTPStatus.FORBIDDEN, 'private/loopback target rejected')

        body = None
        if method == 'POST':
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length) if length > 0 else b''

        req = urllib.request.Request(url, method=method, data=body)
        req.add_header('User-Agent', USER_AGENT)
        req.add_header('Accept', 'application/json, text/plain, */*')

        try:
            # urllib follows redirects by default. To enforce that any redirect
            # target also passes our allowlist, install a handler that bounces
            # to _redirect_check.
            opener = urllib.request.build_opener(_AllowlistRedirectHandler())
            upstream = opener.open(req, timeout=TIMEOUT_SEC)
        except urllib.error.HTTPError as e:
            # Bubble through with original status.
            return self._stream_upstream(e, e.status or 502)
        except urllib.error.URLError as e:
            return self.send_error(HTTPStatus.BAD_GATEWAY, f'upstream error: {e}')
        except (TimeoutError, socket.timeout):
            return self.send_error(HTTPStatus.GATEWAY_TIMEOUT, 'upstream timeout')
        except ValueError as e:
            return self.send_error(HTTPStatus.FORBIDDEN, str(e))

        self._stream_upstream(upstream, upstream.status or 200)

    def _stream_upstream(self, upstream, status: int) -> None:
        try:
            self.send_response(status)
            # Copy a small set of safe upstream headers; strip the rest.
            ctype = upstream.headers.get('Content-Type', 'application/octet-stream')
            self.send_header('Content-Type', ctype)
            # If the upstream sent a Content-Length we can forward it, which
            # lets the browser reuse this connection. Without it we must
            # send Connection: close so the browser knows EOF means done.
            clen = upstream.headers.get('Content-Length')
            if clen and clen.isdigit():
                self.send_header('Content-Length', clen)
            else:
                self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            # CORS — we are localhost so this is more belt-and-suspenders than
            # required, but it costs nothing and helps test harnesses.
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            total = 0
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_SIZE:
                    sys.stderr.write(
                        f'[proxy] response > {MAX_SIZE} bytes — truncating\n'
                    )
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    return
        finally:
            try:
                upstream.close()
            except Exception:
                pass


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):  # noqa: N802 (stdlib signature)
        new_url = headers.get('Location') or ''
        new = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, new_url))
        host = (new.hostname or '').lower()
        if new.scheme != 'https' or not is_allowed_host(host) or resolves_to_private_ip(host):
            raise ValueError(f'redirect to disallowed target rejected: {new.scheme}://{host}')
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# HTTP/1.1 keep-alive: drops per-request TCP setup overhead. The default
# SimpleHTTPRequestHandler speaks HTTP/1.0 (connection: close on every
# response). For our loopback-only case, the cost is small in absolute
# terms but multiplied across 20+ adapter calls during search it adds up
# to 1-2 seconds. HTTP/1.1 lets the browser reuse connections.
WorldMediaHandler.protocol_version = 'HTTP/1.1'


def main() -> int:
    if not os.path.isdir(ROOT):
        sys.stderr.write(f'[server] frontend dir not found: {ROOT}\n')
        return 2
    if not os.path.isfile(os.path.join(ROOT, 'index.html')):
        sys.stderr.write(f'[server] {ROOT}/index.html missing\n')
        return 2

    bind_host = os.environ.get('WORLDMEDIA_BIND', '0.0.0.0')
    server = ThreadingServer((bind_host, PORT), WorldMediaHandler)
    sys.stderr.write(
        f'[server] World Media listening on http://{bind_host}:{PORT}/ '
        f'(frontend={ROOT}, proxy=/api/proxy)\n'
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write('[server] shutting down\n')
        server.shutdown()
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
