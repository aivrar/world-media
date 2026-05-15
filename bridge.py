#!/usr/bin/env python3
"""
World Media — linbox bridge entrypoint.

The linbox template (E:\\linux\\template\\src\\main.c, function start_bridge)
requires every child app to provide `bridge.py` and `bridge_watchdog.py`.
The template launches the watchdog, which launches this file. After the
watchdog reports an open TCP port on BRIDGE_PORT and a successful
/api/live probe, the template's WebView navigates to
http://<distro-ip>:<bridge-port>/?tq_t=<token> and that becomes the
app's runtime URL.

For World Media the bridge IS the app: this process serves the static
frontend AND the CORS-bypass proxy (same logic as server.py). We don't
need a separate websockify/VNC stack — the frontend is a plain SPA.

Env vars set by the template launcher:
  BRIDGE_PORT      port to listen on (== app.json port + 1000)
  TQ_AUTH_TOKEN    bearer token the WebView will pass as ?tq_t=
                   (we accept it but don't enforce; this app is loopback-only)
  TQ_APP_DIR       this directory inside the distro (typically /opt/app
                   or /mnt/<drive>/<path>)
  TQ_BRIDGE_LOG_DIR  /tmp/linux_template by default
"""

from __future__ import annotations

import http.server
import os
import signal
import socket
import socketserver
import sys
import threading
import time

# Make the standalone server module importable regardless of where the
# launcher chdir'd us. We expect server.py to sit next to this file.
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

BRIDGE_PORT = int(os.environ.get('BRIDGE_PORT', '10123'))  # 9123 + 1000
APP_DIR = os.environ.get('TQ_APP_DIR', HERE)
FRONTEND = os.environ.get('WORLDMEDIA_FRONTEND', os.path.join(APP_DIR, 'frontend'))

# Point server.py at the bridge port + frontend dir BEFORE importing it,
# because server.py captures both as module-level constants on import.
os.environ['WORLDMEDIA_FRONTEND'] = FRONTEND
os.environ['WORLDMEDIA_PORT'] = str(BRIDGE_PORT)

# Reuse the entire request handler from server.py — same allowlisted
# proxy, same static-file serving, same rate-limit table.
import server as _wm_server  # noqa: E402


# Extend the server's handler with /api/live (template polls this for
# liveness before redirecting the WebView).
_orig_dispatch = _wm_server.WorldMediaHandler._dispatch_api


def _bridge_dispatch_api(self, method):
    import urllib.parse
    parsed = urllib.parse.urlsplit(self.path)
    if parsed.path == '/api/live':
        body = b'{"ok":true,"app":"World Media"}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
        return
    if parsed.path == '/api/shutdown' and method == 'POST':
        return _handle_shutdown(self)
    return _orig_dispatch(self, method)


def _handle_shutdown(handler):
    """User-initiated clean shutdown. Sequence:

      1. Reply 202 to the client so the browser sees a clean "OK".
      2. Drop a 'shutdown intent' marker so the watchdog (if it scans
         before reading our SIGTERM) won't respawn us.
      3. Spawn stop.sh detached — it kills anything bound to our ports.
      4. Signal the watchdog (SIGTERM): it tears down bridge.py + exits.
      5. As a last resort, schedule os._exit(0) on a short timer in
         case the watchdog signal path is wedged.

    No bridge state is mutated synchronously after step 1, so the
    client sees a complete HTTP response even though the process is
    actively dying.
    """
    body = b'{"ok":true,"status":"shutting-down"}'
    handler.send_response(202)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    try:
        handler.wfile.write(body)
        handler.wfile.flush()
    except Exception:
        pass

    log_dir = os.environ.get('TQ_BRIDGE_LOG_DIR', '/tmp/linux_template')
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, 'shutdown.intent'), 'w') as f:
            f.write(str(int(time.time())))
    except Exception:
        pass

    # stop.sh — anything specific to this app (port reaping etc.)
    stop_sh = os.path.join(APP_DIR, 'stop.sh')
    if os.path.isfile(stop_sh):
        try:
            import subprocess
            popen_kwargs = {
                'stdout': subprocess.DEVNULL,
                'stderr': subprocess.DEVNULL,
            }
            if os.name == 'posix':
                popen_kwargs['start_new_session'] = True
            subprocess.Popen(['bash', stop_sh], **popen_kwargs)
        except Exception as e:
            sys.stderr.write(f'[bridge] stop.sh spawn failed: {e}\n')

    # Tell the watchdog to exit (it will SIGTERM bridge.py on its way out).
    try:
        wpid_path = os.path.join(log_dir, 'watchdog.pid')
        if os.path.exists(wpid_path):
            wpid = int(open(wpid_path).read().strip() or '0')
            if wpid > 0:
                os.kill(wpid, signal.SIGTERM)
    except Exception as e:
        sys.stderr.write(f'[bridge] watchdog SIGTERM failed: {e}\n')

    # Last resort: hard exit on a short timer. Runs only if the
    # watchdog path didn't kill us in time.
    import threading
    threading.Timer(2.0, lambda: os._exit(0)).start()


_wm_server.WorldMediaHandler._dispatch_api = _bridge_dispatch_api


def _shutdown(signum, _frame):
    sys.stderr.write(f'[bridge] signal {signum} received, exiting\n')
    # Best-effort: run stop.sh in case the operator wired any teardown there.
    stop_sh = os.path.join(APP_DIR, 'stop.sh')
    if os.path.isfile(stop_sh):
        try:
            import subprocess
            subprocess.Popen(
                ['bash', stop_sh],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            sys.stderr.write(f'[bridge] stop.sh spawn failed: {e}\n')
    sys.stderr.flush()
    os._exit(0)


def main() -> int:
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if not os.path.isdir(FRONTEND):
        sys.stderr.write(f'[bridge] frontend dir missing: {FRONTEND}\n')
        return 2

    server = _wm_server.ThreadingServer(('0.0.0.0', BRIDGE_PORT),
                                        _wm_server.WorldMediaHandler)
    sys.stderr.write(
        f'[bridge] World Media listening on http://0.0.0.0:{BRIDGE_PORT}/ '
        f'(frontend={FRONTEND})\n'
    )
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _shutdown(signal.SIGINT, None)
    return 0


if __name__ == '__main__':
    sys.exit(main())
