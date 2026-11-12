#!/usr/bin/env bash
# Bring the server to the latest code (or a given version), rebuild only what changed, restart, verify.
#
#   bash scripts/server/update.sh             # latest main
#   bash scripts/server/update.sh --force     # even if a job is running (it pauses; Resume continues it)
#   bash scripts/server/update.sh --ref abc123  # a specific commit or tag (roll back)
#
# Normally run from the laptop with scripts/deploy.sh, which calls this over SSH.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$APP_DIR"
FORCE=0
REF=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1 ;;
    --ref) REF="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
say() { printf '\033[1;35m==> %s\033[0m\n' "$*"; }

# 1. never cut a running render short by accident
if [[ $FORCE -eq 0 ]] && curl -fsS http://127.0.0.1:8000/api/status 2>/dev/null \
    | python3 -c 'import json,sys; q=json.load(sys.stdin)["queue"]; sys.exit(0 if q["current"] or q["queued"] else 1)'; then
  echo "A job is running or queued. Wait for it, or rerun with --force (the job pauses and can be resumed)." >&2
  exit 3
fi

# 2. fetch the code
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "The server has local code edits (git status). Code is changed on the laptop, never here:" >&2
  git status --short --untracked-files=no >&2
  echo "Discard them with:  git checkout -- . && bash scripts/server/update.sh" >&2
  exit 4
fi
before=$(git rev-parse HEAD)
git fetch --quiet origin
if [[ -n "$REF" ]]; then
  git checkout --quiet --detach "$REF"
else
  git checkout --quiet main 2>/dev/null || true
  git merge --ff-only --quiet origin/main
fi
after=$(git rev-parse HEAD)
if [[ "$before" == "$after" ]]; then
  say "Already at $(git log -1 --format='%h %s')"
else
  say "$(git log -1 --format='%h %s' "$before") -> $(git log -1 --format='%h %s')"
  git --no-pager log --oneline "$before..$after" | sed 's/^/    /' || true
fi
changed=$(git diff --name-only "$before" "$after")

# 3. rebuild only what changed
if grep -q '^pyproject.toml$' <<<"$changed"; then
  say "Python dependencies changed: installing"
  PIP_CACHE_DIR="$APP_DIR/.cache/pip" .venv/bin/pip install -q -e ".[dev]"
fi
if grep -q '^web/' <<<"$changed" || [[ ! -f web/studio/dist/index.html ]]; then
  say "Web UI changed: building"
  (cd web && npm ci --cache "$APP_DIR/.cache/npm" --no-audit --no-fund --silent && npm run build --silent)
fi
if grep -q '^deploy/studio.service$' <<<"$changed"; then
  say "Service file changed: reinstalling"
  sed -e "s#__USER__#$(whoami)#g" -e "s#__DIR__#$APP_DIR#g" deploy/studio.service \
    | sudo tee /etc/systemd/system/studio.service >/dev/null
  sudo systemctl daemon-reload
fi

# 4. restart and check it really came back
say "Restarting Studio"
sudo systemctl restart studio
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/api/status >/dev/null 2>&1; then
    say "Studio is up: $(git log -1 --format='%h %s')"
    exit 0
  fi
  sleep 1
done
echo "Studio did not come back. Last log lines:" >&2
journalctl -u studio -n 40 --no-pager >&2
echo "Roll back with:  bash scripts/server/update.sh --force --ref $before" >&2
exit 1
