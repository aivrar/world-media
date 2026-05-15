#!/bin/bash
# World Media — Linux child start.
# Launches the Python-based static-file server + CORS-bypass proxy on the
# configured port. The webview2 in the host exe loads http://<wsl-ip>:9123/.

set -u

# Per-run log surface. We append to one file (not rotated) so the user can
# inspect "what happened last time" easily; runs are separated by the date
# marker below. start.sh's own output goes here; server.py's stderr also
# tees here via the exec below.
mkdir -p /opt/app
LOG=/opt/app/.start.log
exec > >(tee -a "$LOG") 2>&1
echo
echo "[wm/start] === $(date -Iseconds) ==="

# Clean shutdown of any previous run. If a previous start.sh died but left
# the server bound, port 9123 would be taken and the next start would fail
# to bind. fuser -k drops anyone holding the port.
fuser -k 9123/tcp 2>/dev/null || true

# Save our PID so stop.sh can walk down from here.
echo $$ > /run/world-media.pid
echo "[wm/start] pid=$$"

# Sanity: dist files must be in place. If setup.sh somehow didn't run or the
# frontend was nuked, we want a loud message in the logs, not a silent 404
# from the server.
if [ ! -f /opt/app/frontend/index.html ]; then
    echo "[wm/start] FATAL: /opt/app/frontend/index.html missing"
    echo "[wm/start]        run setup.sh first or check the template packaging"
    exit 1
fi
if [ ! -f /opt/app/server.py ]; then
    echo "[wm/start] FATAL: /opt/app/server.py missing"
    exit 1
fi

# server.py reads these env vars; we set them explicitly so the script is
# explicit about its configuration (rather than relying on hard-coded values
# inside the Python).
export WORLDMEDIA_PORT=9123
export WORLDMEDIA_FRONTEND=/opt/app/frontend
export WORLDMEDIA_BIND=0.0.0.0

echo "[wm/start] launching server.py on :$WORLDMEDIA_PORT"
echo "[wm/start] frontend = $WORLDMEDIA_FRONTEND"
echo "[wm/start] ----- server log starts here -----"
# exec replaces the bash process with python3 so the controlling exe sees
# python3 as its child and clean kills walk down correctly.
exec python3 -u /opt/app/server.py
