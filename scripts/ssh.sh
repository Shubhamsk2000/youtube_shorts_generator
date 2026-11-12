#!/usr/bin/env bash
# Log in to the server (or run one command there):  scripts/ssh.sh   |   scripts/ssh.sh 'df -h /'
source "$(dirname "$0")/_server.sh"
exec ssh "${SSH_OPTS[@]}" "$SERVER" "$@"
