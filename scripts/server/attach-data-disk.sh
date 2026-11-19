#!/usr/bin/env bash
# Put the shorts workspace (downloads, clip videos, renders) and uploads on a second EBS volume.
#
#   bash scripts/server/attach-data-disk.sh               # auto: the one unmounted, empty disk (e.g. nvme1n1)
#   bash scripts/server/attach-data-disk.sh /dev/nvme1n1  # a specific disk
#
# Safe to rerun. It formats ONLY a disk with no filesystem/partition table on it (never one with data),
# mounts it at /mnt/data (fstab by UUID, `nofail`: the server still boots without it), moves
# data/shorts/workspace and data/shorts/uploads there, and leaves symlinks in their place, so nothing in
# the code or configs changes. Exports stay on the root disk (small; scripts/fetch-exports.sh reads them).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
MNT=/mnt/data
say() { printf '\033[1;35m==> %s\033[0m\n' "$*"; }

dev="${1:-}"
if [[ -z "$dev" ]] && ! mountpoint -q "$MNT"; then
  # whole disks with no partitions, no filesystem and no mountpoint
  mapfile -t cands < <(lsblk -dnpo NAME,TYPE | awk '$2=="disk"{print $1}' | while read -r d; do
    [[ -z "$(lsblk -nro MOUNTPOINT "$d" | tr -d '[:space:]')" && "$(lsblk -nro NAME "$d" | wc -l)" -eq 1 ]] && echo "$d"
  done)
  if [[ ${#cands[@]} -ne 1 ]]; then
    echo "Found ${#cands[@]} unmounted, unpartitioned disks (${cands[*]:-none}). Pass the one to use: $0 /dev/nvme1n1" >&2
    lsblk >&2
    exit 2
  fi
  dev="${cands[0]}"
fi

if ! mountpoint -q "$MNT"; then
  if [[ -z "$(sudo blkid -o value -s TYPE "$dev" || true)" ]]; then
    if [[ -n "$(sudo wipefs -n "$dev")" ]]; then
      echo "$dev has signatures on it (wipefs). Not formatting a disk that may hold data." >&2
      exit 3
    fi
    say "Formatting $dev (ext4, label shorts-data)"
    sudo mkfs.ext4 -q -L shorts-data -m 0 "$dev"
  fi
  uuid=$(sudo blkid -s UUID -o value "$dev")
  sudo mkdir -p "$MNT"
  if ! grep -q "UUID=$uuid" /etc/fstab; then
    say "Adding $MNT to /etc/fstab"
    echo "UUID=$uuid $MNT ext4 defaults,nofail,discard,x-systemd.device-timeout=10s 0 2" | sudo tee -a /etc/fstab >/dev/null
    sudo systemctl daemon-reload
  fi
  sudo mount "$MNT"
fi
sudo chown "$(id -u):$(id -g)" "$MNT"

move() {  # data/shorts/<name> -> /mnt/data/shorts/<name>, symlinked back
  local rel="data/shorts/$1" dst="$MNT/shorts/$1"
  mkdir -p "$dst" "$APP_DIR/data/shorts"
  if [[ -L "$APP_DIR/$rel" ]]; then
    say "$rel already -> $(readlink "$APP_DIR/$rel")"
    return
  fi
  if [[ -d "$APP_DIR/$rel" ]]; then
    say "Moving $rel to $dst ($(du -sh "$APP_DIR/$rel" | cut -f1))"
    rsync -a "$APP_DIR/$rel/" "$dst/"
    mv "$APP_DIR/$rel" "$APP_DIR/$rel.old"
    ln -s "$dst" "$APP_DIR/$rel"
    rm -rf "$APP_DIR/$rel.old"
  else
    ln -s "$dst" "$APP_DIR/$rel"
  fi
}

running=0
if systemctl is-active --quiet studio 2>/dev/null; then
  if curl -fsS http://127.0.0.1:8000/api/status 2>/dev/null \
      | python3 -c 'import json,sys; q=json.load(sys.stdin)["queue"]; sys.exit(0 if q["current"] or q["queued"] else 1)'; then
    echo "A job is running or queued: wait for it, then rerun." >&2
    exit 4
  fi
  running=1
  sudo systemctl stop studio
fi
move workspace
move uploads
[[ $running -eq 1 ]] && sudo systemctl start studio
say "Done: $(df -h "$MNT" | awk 'NR==2{print $4" free on "$1}')"
