#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
env_file=${MONDAY_ENV_FILE:-"$repo_root/engine/.env"}
npx_bin=${NPX_BIN:-/Users/amexicotte/.nvm/versions/node/v22.15.0/bin/npx}

if [ ! -r "$env_file" ]; then
  echo "monday MCP: credential file is not readable: $env_file" >&2
  exit 1
fi

monday_token=$(awk -F= '
  $1 == "MONDAY_API_TOKEN" {
    value = $0
    sub(/^[^=]*=/, "", value)
    print value
    exit
  }
' "$env_file")

if [ -z "$monday_token" ]; then
  echo "monday MCP: MONDAY_API_TOKEN is missing" >&2
  exit 1
fi

if [ ! -x "$npx_bin" ]; then
  echo "monday MCP: npx is not executable: $npx_bin" >&2
  exit 1
fi

export MONDAY_TOKEN=$monday_token
unset monday_token

exec "$npx_bin" -y @mondaydotcomorg/monday-api-mcp@3.3.0 --read-only
