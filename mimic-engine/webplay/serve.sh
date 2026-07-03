#!/bin/sh
# Serve the mimic-engine test page locally, then open it in the browser.
cd "$(dirname "$0")"
PORT="${1:-8763}"
echo "Serving at http://localhost:$PORT  (Ctrl-C to stop)"
(sleep 1 && open "http://localhost:$PORT" 2>/dev/null) &
exec python3 -m http.server "$PORT"
