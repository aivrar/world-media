#!/bin/bash
# World Media — clean shutdown. Safe to run standalone (kills whatever is
# bound to the app's port) or from a control sidecar.

set +e
PIDFILE=/run/world-media.pid

# 1. Walk the process tree from our recorded PID. Catches any python3 child
#    and its threads/subprocesses.
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        # Kill descendants first, then the leader.
        for child in $(pgrep -P "$PID" 2>/dev/null); do
            kill "$child" 2>/dev/null
        done
        sleep 0.2
        kill "$PID" 2>/dev/null
        sleep 0.4
        kill -9 "$PID" 2>/dev/null || true
    fi
    rm -f "$PIDFILE"
fi

# 2. Belt + suspenders: anything still bound to 9123.
fuser -k 9123/tcp 2>/dev/null || true

# 3. Final paranoid sweep — anything literally running our server.py.
pkill -9 -f '/opt/app/server.py' 2>/dev/null || true

echo "[wm/stop] done"
