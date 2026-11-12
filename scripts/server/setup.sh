#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server (AWS EC2) for Studio. Safe to run again.
#
#   cd ~/yt_video_gen && bash scripts/server/setup.sh
#
# Installs: ffmpeg, Node 22 (UI build + YouTube JS challenges), the Inter fonts (card + captions),
# a 2 GB swap file, the Python venv, the built UI, and the `studio` systemd service (localhost:8000 only).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_USER="$(whoami)"
cd "$APP_DIR"
say() { printf '\n\033[1;35m==> %s\033[0m\n' "$*"; }

if [[ "$(. /etc/os-release && echo "$ID")" != "ubuntu" ]]; then
  echo "This script expects Ubuntu (24.04 recommended)." >&2
  exit 1
fi

say "Disk"
root_gb=$(df -BG --output=size / | tail -1 | tr -dc '0-9')
if (( root_gb < 25 )); then
  echo "The disk is only ${root_gb} GB. Episodes, models and renders need ~30-40 GB:"
  echo "Grow it to 40 GB (no reinstall): see Growing the disk in docs/DEPLOY.md, then run this again."
  exit 1
fi
echo "${root_gb} GB: ok"

say "System packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ffmpeg git python3-venv python3-pip fontconfig unzip curl ca-certificates

say "Node.js 22"
if ! command -v node >/dev/null || (( $(node -p 'process.versions.node.split(".")[0]') < 20 )); then
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt-get install -y nodejs
fi
node --version

say "Fonts (Inter: tweet card and captions)"
if ! fc-list | grep -qi "Inter.*Black"; then
  sudo apt-get install -y fonts-inter || true
fi
if ! fc-list | grep -qi "Inter.*Black"; then
  tmp=$(mktemp -d)
  curl -fsSL -o "$tmp/inter.zip" https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip
  unzip -q "$tmp/inter.zip" -d "$tmp/inter"
  mkdir -p ~/.local/share/fonts/inter
  find "$tmp/inter" -name 'Inter-*.ttf' -not -path '*Variable*' -exec cp {} ~/.local/share/fonts/inter/ \;
  fc-cache -f >/dev/null
  rm -rf "$tmp"
fi
fc-match "Inter Black"

say "Swap (2 GB: Whisper + rendering on 4 GB RAM)"
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
swapon --show

say "Python environment"
[[ -d .venv ]] || python3 -m venv .venv
PIP_CACHE_DIR="$APP_DIR/.cache/pip" .venv/bin/pip install -q --upgrade pip
PIP_CACHE_DIR="$APP_DIR/.cache/pip" .venv/bin/pip install -q -e ".[dev]"

say "Web UI"
(cd web && npm ci --cache "$APP_DIR/.cache/npm" --no-audit --no-fund && npm run build)

say "Secrets"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from the example: copy your real one from the laptop (run scripts/sync-settings.sh on the laptop, docs/DEPLOY.md step 7)."
fi

say "Service"
sed -e "s#__USER__#$APP_USER#g" -e "s#__DIR__#$APP_DIR#g" deploy/studio.service \
  | sudo tee /etc/systemd/system/studio.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now studio
sleep 3
.venv/bin/shorts doctor || true
if curl -fsS http://127.0.0.1:8000/api/status >/dev/null; then
  say "Done. Studio is running on the server (port 8000, localhost only)."
  echo "Open it from your laptop with:  scripts/tunnel.sh"
else
  echo "Studio did not answer yet. Logs:  journalctl -u studio -n 50 --no-pager" >&2
  exit 1
fi
