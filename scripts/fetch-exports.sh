#!/usr/bin/env bash
# Download the finished shorts from the server into data/server-exports/ (only new files are copied).
source "$(dirname "$0")/_server.sh"
mkdir -p "$ROOT/data/server-exports/anime" "$ROOT/data/server-exports/shorts"
rsync -ah --info=progress2 -e "ssh ${SSH_OPTS[*]}" "$SERVER:$APP_DIR/data/anime/exports/" "$ROOT/data/server-exports/anime/"
rsync -ah --info=progress2 -e "ssh ${SSH_OPTS[*]}" "$SERVER:$APP_DIR/data/shorts/exports/" "$ROOT/data/server-exports/shorts/"
echo "Shorts are in data/server-exports/"
