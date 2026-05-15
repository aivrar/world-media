#!/usr/bin/env python3
"""
World Media — linbox bridge supervisor.

The linbox template's start_bridge (main.c) launches THIS file inside the
WSL distro via:

    BRIDGE_PORT=… TQ_AUTH_TOKEN=… TQ_APP_DIR=… TQ_BRIDGE_LOG_DIR=… \\
    setsid nohup python3 -u bridge_watchdog.py …

Responsibilities:
  1. Launch bridge.py as a child process.
  2. Poll http://127.0.0.1:$BRIDGE_PORT/api/live every few seconds. If
     three consecutive probes fail, restart bridge.py.
  3. Flap protection: if we restart more than MAX_RESTARTS times in
     FLAP_WINDOW seconds, write a death marker and exit non-zero.
  4. Write our own PID and the bridge's PID so the template's stop path
     (also in main.c, function stop_bridge_with_backend) can find and
     kill us via /tmp/linux_template/watchdog.pid and bridge.pid.
  5. Survive parent death — setsid is set by the template launcher.

Stdlib only.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE_PORT = int(os.environ.get('BRIDGE_PORT', '10123'))
APP_DIR = os.environ.get('TQ_APP_DIR', HERE)
LOG_DIR = os.environ.get('TQ_BRIDGE_LOG_DIR', '/tmp/linux_template')

os.makedirs(LOG_DIR, exist_ok=True)

WATCHDOG_PID_FILE = os.path.join(LOG_DIR, 'watchdog.pid')
BRIDGE_PID_FILE   = os.path.join(LOG_DIR, 'bridge.pid')
WATCHDOG_LOG      = os.path.join(LOG_DIR, 'watchdog.log')
BRIDGE_LOG        = os.path.join(LOG_DIR, 'bridge.log')
SHUTDOWN_MARKER   = os.path.join(LOG_DIR, 'shutdown.intent')

POLL_INTERVAL_SEC = 2.0
FAIL_THRESHOLD = 3
MAX_RESTARTS = 5
FLAP_WINDOW_SEC = 60.0


def _log(msg: str) -> None:
    line = f'[{time.strftime("%H:%M:%S")}] {msg}\n'
    sys.stderr.write(line)
    sys.stderr.flush()
    try:
        with open(WATCHDOG_LOG, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass


def _write_pid(path: str, pid: int) -> None:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(str(pid))
    except Exception as e:
        _log(f'pid file write failed ({path}): {e}')


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _single_instance() -> bool:
    """Return True if no other watchdog is alive. If one is, exit cleanly."""
    if not os.path.exists(WATCHDOG_PID_FILE):
        return True
    try:
        prev = int(open(WATCHDOG_PID_FILE).read().strip() or '0')
    except Exception:
        return True
    if prev > 0 and _pid_alive(prev):
        _log(f'another watchdog is already alive (pid {prev}); exiting')
        return False
    return True


def _probe_live() -> bool:
    """One-shot /api/live probe."""
    try:
        with urllib.request.urlopen(
            f'http://127.0.0.1:{BRIDGE_PORT}/api/live', timeout=2.0,
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return False


def _python_exe() -> str:
    # On the target (WSL Ubuntu) python3 is canonical and what setup.sh
    # installs. On a Windows host (only relevant for local dev smoke
    # tests of this script) we may only have `python`.
    for cand in ('python3', 'python'):
        try:
            subprocess.check_call(
                [cand, '-c', 'import sys'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return cand
        except (OSError, subprocess.CalledProcessError):
            continue
    return 'python3'  # let Popen raise the clearer error


def _spawn_bridge() -> subprocess.Popen:
    bridge_py = os.path.join(APP_DIR, 'bridge.py')
    if not os.path.isfile(bridge_py):
        _log(f'FATAL: {bridge_py} missing')
        sys.exit(2)
    env = os.environ.copy()
    env['BRIDGE_PORT'] = str(BRIDGE_PORT)
    env['TQ_APP_DIR'] = APP_DIR
    # Append-only bridge log — handy for debugging silent failures.
    log_fh = open(BRIDGE_LOG, 'a', buffering=1, encoding='utf-8')
    popen_kwargs = {
        'env': env,
        'stdout': log_fh,
        'stderr': subprocess.STDOUT,
    }
    # POSIX: setsid so the bridge survives parent death and signals
    # propagate cleanly down the process group. Windows: a separate
    # console; this is only ever taken in local dev smoke tests.
    if os.name == 'posix':
        popen_kwargs['start_new_session'] = True
    else:
        popen_kwargs['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    proc = subprocess.Popen([_python_exe(), '-u', bridge_py], **popen_kwargs)
    _write_pid(BRIDGE_PID_FILE, proc.pid)
    _log(f'spawned bridge.py pid={proc.pid} on :{BRIDGE_PORT}')
    return proc


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        for _ in range(20):
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        proc.kill()
    except Exception:
        pass


def main() -> int:
    if not _single_instance():
        return 0
    # A shutdown.intent from a previous launch must not poison this one.
    # If the marker is on disk before we've even spawned bridge.py, it's
    # stale — purge it so the loop below doesn't read it and exit instantly.
    try:
        if os.path.exists(SHUTDOWN_MARKER):
            os.remove(SHUTDOWN_MARKER)
    except OSError:
        pass
    _write_pid(WATCHDOG_PID_FILE, os.getpid())
    _log(f'watchdog up, pid={os.getpid()}, bridge_port={BRIDGE_PORT}')

    bridge = _spawn_bridge()
    consecutive_failures = 0
    restart_history: list[float] = []

    def _on_signal(signum, _frame):
        _log(f'signal {signum}, terminating bridge and exiting')
        _terminate(bridge)
        try: os.remove(WATCHDOG_PID_FILE)
        except OSError: pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    while True:
        time.sleep(POLL_INTERVAL_SEC)

        # User-initiated shutdown leaves a marker. Don't respawn anything
        # in that case — exit cleanly so the process tree dies and the
        # template's WebView sees the connection drop and tears down.
        if os.path.exists(SHUTDOWN_MARKER):
            _log('shutdown marker detected, exiting without respawn')
            _terminate(bridge)
            try: os.remove(WATCHDOG_PID_FILE)
            except OSError: pass
            try: os.remove(SHUTDOWN_MARKER)
            except OSError: pass
            return 0

        # Death of the child without a probe failing — restart immediately.
        if bridge.poll() is not None:
            _log(f'bridge.py exited with code {bridge.returncode}')
            bridge = _spawn_bridge()
            restart_history.append(time.time())
            consecutive_failures = 0
            # Flap check.
            restart_history = [t for t in restart_history
                               if time.time() - t < FLAP_WINDOW_SEC]
            if len(restart_history) > MAX_RESTARTS:
                _log(f'FLAP: {len(restart_history)} restarts within {FLAP_WINDOW_SEC}s — giving up')
                return 1
            continue

        # HTTP probe.
        if _probe_live():
            consecutive_failures = 0
            continue
        consecutive_failures += 1
        if consecutive_failures < FAIL_THRESHOLD:
            continue

        _log(f'bridge unresponsive after {consecutive_failures} probes — restarting')
        _terminate(bridge)
        bridge = _spawn_bridge()
        restart_history.append(time.time())
        consecutive_failures = 0
        restart_history = [t for t in restart_history
                           if time.time() - t < FLAP_WINDOW_SEC]
        if len(restart_history) > MAX_RESTARTS:
            _log(f'FLAP: {len(restart_history)} restarts within {FLAP_WINDOW_SEC}s — giving up')
            return 1


if __name__ == '__main__':
    sys.exit(main())
