#!/usr/bin/env bash
# Ship the code you pushed to GitHub to the server:  scripts/deploy.sh [--force] [--ref <commit>]
# (--force: even if a job is running; --ref: deploy an older commit to roll back)
source "$(dirname "$0")/_server.sh"
cd "$ROOT"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "You have uncommitted changes; the server only gets what is pushed to GitHub:" >&2
  git status --short --untracked-files=no >&2
  echo "Commit and push first (see docs/DEPLOY.md, 'When you change the code')." >&2
  exit 1
fi
git fetch --quiet origin
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" && " $* " != *" --ref "* ]]; then
  echo "Your local main is not the same as GitHub's: run 'git push' (or 'git pull') first." >&2
  git --no-pager log --oneline origin/main..HEAD | sed 's/^/  not pushed: /' >&2
  exit 1
fi
ssh "${SSH_OPTS[@]}" "$SERVER" "cd $APP_DIR && bash scripts/server/update.sh $*"
