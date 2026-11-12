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
ssh "${SSH_OPTS[@]}" "$SERVER" "mkdir -p $APP_DIR/$dest"
rsync -ah --partial --info=progress2 -e "ssh ${SSH_OPTS[*]}" "${files[@]}" "$SERVER:$APP_DIR/$dest"
echo "Done. They show up in Studio (Anime → episodes / Music / Shorts)."
