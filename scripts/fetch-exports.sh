#!/usr/bin/env bash
# Download only the NEW finished shorts from the server into data/server-exports/.
# Every downloaded file is remembered in data/server-exports/.fetched-<kind>.txt, so it is not downloaded
# again even after you delete or move your local copy.
#   scripts/fetch-exports.sh              download what hasn't been fetched before
#   scripts/fetch-exports.sh --mark-seen  mark everything on the server as fetched, download nothing
#   scripts/fetch-exports.sh --all        ignore the record, download everything missing locally
source "$(dirname "$0")/_server.sh"
mode=${1:-new}
for kind in shorts anime; do
  src="$APP_DIR/data/$kind/exports"
  dest="$ROOT/data/server-exports/$kind"
  seen="$ROOT/data/server-exports/.fetched-$kind.txt"
  mkdir -p "$dest"; touch "$seen"
  # files on the server right now (relative paths); skip a kind that has never exported anything
  listing=$(ssh "${SSH_OPTS[@]}" "$SERVER" "test -d $src && cd $src && find . -type f ! -name '.*' | sed 's|^\./||' | sort") || {
    echo "$kind: nothing exported on the server yet"; continue; }
  new=$(comm -23 <(printf '%s\n' "$listing" | sed '/^$/d') <(sort -u "$seen"))
  if [ -z "$new" ]; then echo "$kind: no new files"; continue; fi
  if [ "$mode" = "--mark-seen" ]; then
    printf '%s\n' "$new" >> "$seen"; echo "$kind: marked $(printf '%s\n' "$new" | wc -l) files as fetched"; continue
  fi
  echo "$kind: $(printf '%s\n' "$new" | wc -l) new files"
  [ "$mode" = "--all" ] && list="$listing" || list="$new"
  # --no-perms etc: the local drive is NTFS and can't store Linux permissions
  printf '%s\n' "$list" | rsync -rth --no-perms --no-owner --no-group --info=progress2 --files-from=- \
    -e "ssh ${SSH_OPTS[*]}" "$SERVER:$src/" "$dest/"
  printf '%s\n' "$new" >> "$seen"
done
echo "Done: they are in data/server-exports/"
