#!/bin/bash
# Rebuilds the Tauri frontend in ../World_media_app/world-media, copies the
# fresh dist/ into ./frontend/, and re-injects the WORLDMEDIA_PROXY hint
# into the new index.html.
#
# Vite overwrites index.html on every build, so the proxy script tag has to
# be re-added each time. This script is the one place that knows that.

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/../World_media_app/world-media"

if [ ! -d "$APP" ]; then
    echo "[sync] expected source tree at $APP" >&2
    exit 1
fi

echo "[sync] building $APP..."
(cd "$APP" && npm run build)

echo "[sync] copying dist → frontend..."
rm -rf "$HERE/frontend/assets" "$HERE/frontend/index.html"
cp -r "$APP/dist/." "$HERE/frontend/"

echo "[sync] injecting WORLDMEDIA_PROXY into index.html..."
INDEX="$HERE/frontend/index.html"
if grep -q WORLDMEDIA_PROXY "$INDEX"; then
    echo "[sync]   already present"
else
    # Inject right after </title> using awk (works on git-bash too).
    awk '
      /<\/title>/ {
        print
        print "    <script>"
        print "      // Portable Linux child: route adapter API fetches through the local"
        print "      // CORS-bypassing proxy served by server.py."
        print "      window.WORLDMEDIA_PROXY = \"/api/proxy?url=\";"
        print "    </script>"
        next
      }
      { print }
    ' "$INDEX" > "$INDEX.new"
    mv "$INDEX.new" "$INDEX"
    echo "[sync]   injected"
fi

echo "[sync] done"
