#!/usr/bin/env bash
# Upload videos to the server (resumable, shows progress):
#   scripts/send.sh video.mp4 [more.mkv ...]   -> data/shorts/uploads/
# ("scripts/send.sh shorts video.mp4" still works.)
source "$(dirname "$0")/_server.sh"
[[ "${1:-}" == "shorts" ]] && shift
dest="data/shorts/uploads/"
files=("$@")
(( ${#files[@]} )) || { echo "no files given" >&2; exit 2; }
missing=0
for f in "${files[@]}"; do
  [[ -f "$f" ]] || { echo "not found: $f" >&2; missing=1; }
done
(( missing )) && { echo 'Names with spaces need quotes: scripts/send.sh "My Stream - part 2.mp4"' >&2; exit 2; }
ssh "${SSH_OPTS[@]}" "$SERVER" "mkdir -p $APP_DIR/$dest"
# --partial keeps what already arrived; --timeout turns a stalled connection into a quick failure,
# and the loop resumes from where it stopped (only the missing part is sent again).
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if rsync -ah --partial --timeout=60 --info=progress2 -e "ssh ${SSH_OPTS[*]}" "${files[@]}" "$SERVER:$APP_DIR/$dest"; then
    break
  fi
  (( attempt == 10 )) && { echo "Upload kept failing; check your internet and run the same command again (it resumes)." >&2; exit 1; }
  echo "Connection dropped, resuming in 10 s (attempt $((attempt + 1))/10)..." >&2
  sleep 10
done
echo "Done. To process one, open Studio and upload it, or run on the server: .venv/bin/shorts make $dest<file>"
