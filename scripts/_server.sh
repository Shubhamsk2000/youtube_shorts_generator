# Shared by the laptop-side scripts: reads .deploy.env (your server's address and key, never committed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "$ROOT/.deploy.env" ]]; then
  cat >&2 <<'MSG'
Missing .deploy.env in the project folder. Create it with your server's details:

  SERVER=ubuntu@<public IPv4 of the instance>
  KEY=<path to your .pem key file>
  APP_DIR=yt_video_gen

(The public IP changes every time the instance starts: update SERVER then.)
MSG
  exit 1
fi
# shellcheck disable=SC1091
source "$ROOT/.deploy.env"
: "${SERVER:?set SERVER in .deploy.env}" "${KEY:?set KEY in .deploy.env}"
APP_DIR="${APP_DIR:-yt_video_gen}"
KEY="${KEY/#\~/$HOME}"
SSH_OPTS=(-i "$KEY" -o ServerAliveInterval=30 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
