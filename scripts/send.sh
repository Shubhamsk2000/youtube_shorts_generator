#!/usr/bin/env bash
# Upload files to the server's data folders (resumable, shows progress):
#   scripts/send.sh anime  ep01.mkv ep02.mkv     -> data/anime/episodes/
#   scripts/send.sh music  song.mp3 [--mood hype] -> data/anime/music/[hype/]
#   scripts/send.sh shorts video.mp4              -> data/shorts/uploads/
source "$(dirname "$0")/_server.sh"
kind="${1:?anime | music | shorts}"; shift
mood=""
files=()
while [[ $# -gt 0 ]]; do
  case "$1" in --mood) mood="$2/"; shift ;; *) files+=("$1") ;; esac
  shift
done
case "$kind" in
  anime) dest="data/anime/episodes/" ;;
  music) dest="data/anime/music/$mood" ;;
  shorts) dest="data/shorts/uploads/" ;;
  *) echo "first argument: anime, music or shorts" >&2; exit 2 ;;
esac
(( ${#files[@]} )) || { echo "no files given" >&2; exit 2; }
missing=0
for f in "${files[@]}"; do
  [[ -f "$f" ]] || { echo "not found: $f" >&2; missing=1; }
done
(( missing )) && { echo 'Names with spaces need quotes: scripts/send.sh anime "data/anime/episodes/[Anime Time] ... - 02.mkv"' >&2; exit 2; }
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
echo "Done. They show up in Studio (Anime → episodes / Music / Shorts)."
