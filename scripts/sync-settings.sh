#!/usr/bin/env bash
# Copy what git does NOT carry to the server: .env (Gemini key), your channel card settings and avatar.
# Run once after setup, and again whenever you change them on the laptop.
source "$(dirname "$0")/_server.sh"
cd "$ROOT"
files=(.env)
[[ -f configs/anime.local.yaml ]] && files+=(configs/anime.local.yaml)
[[ -f data/anime/avatar.png ]] && files+=(data/anime/avatar.png)
ssh "${SSH_OPTS[@]}" "$SERVER" "mkdir -p $APP_DIR/configs $APP_DIR/data/anime"
for f in "${files[@]}"; do
  scp -q "${SSH_OPTS[@]}" "$f" "$SERVER:$APP_DIR/$f"
  echo "copied $f"
done
ssh "${SSH_OPTS[@]}" "$SERVER" "chmod 600 $APP_DIR/.env && sudo systemctl restart studio 2>/dev/null || true"
echo "Done (Studio restarted on the server to pick them up)."
