#!/usr/bin/env bash
# Open the server's Studio in your browser through SSH (nothing is exposed to the internet).
# Leave this running; Ctrl+C closes the tunnel.   scripts/tunnel.sh [local port, default 8800]
source "$(dirname "$0")/_server.sh"
PORT="${1:-8800}"
echo "Studio on the server → http://localhost:$PORT   (Ctrl+C to close)"
exec ssh "${SSH_OPTS[@]}" -N -L "$PORT:127.0.0.1:8000" "$SERVER"
