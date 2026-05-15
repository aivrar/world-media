#!/bin/bash
# World Media — Linux child setup.
# Idempotent. Re-runs are fast: if the stamp is present and matches, we exit.
#
# What this installs:
#   - python3   (used by /opt/app/server.py — stdlib-only, no pip deps)
#   - ca-certificates (so the proxy can verify TLS to archive.org etc.)
#
# That's the entire install footprint. The frontend is already baked into
# /opt/app/frontend by the linux_template.exe before this runs.

set -e

LOG=/opt/app/.setup.log
STAMP=/opt/app/.setup-complete
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "[wm/setup] === $(date -Iseconds) ==="

# Fast short-circuit: stamp file exists and python3 is on PATH → nothing to do.
if [ -f "$STAMP" ] && command -v python3 >/dev/null 2>&1; then
    echo "[wm/setup] already installed; python3=$(python3 --version 2>&1)"
    exit 0
fi

# Connectivity preflight — we need apt-get to talk to its mirror at least once.
echo "[wm/setup] preflight: connectivity"
if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 https://archive.ubuntu.com/ubuntu/ -o /dev/null \
      || echo "[wm/setup]   WARNING: Ubuntu apt mirror unreachable; apt may fail"
fi

# Install Python + TLS roots. Be quiet but don't hide errors.
export DEBIAN_FRONTEND=noninteractive
echo "[wm/setup] apt-get update"
apt-get update -qq

echo "[wm/setup] apt-get install python3 ca-certificates"
apt-get install -y -qq --no-install-recommends \
    python3 \
    ca-certificates

# Sanity check.
python3 -c "import http.server, urllib.request, ssl; print('[wm/setup] python ok')"

# Done — write stamp so the next start.sh skips this whole block.
touch "$STAMP"
echo "[wm/setup] done"
