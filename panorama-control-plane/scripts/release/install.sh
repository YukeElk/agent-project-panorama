#!/bin/sh
set -eu
node_path=node
python_path=python3
while [ "$#" -gt 0 ]; do
  [ "$#" -ge 2 ] || { echo 'Usage: sh install.sh [--node PATH] [--python PATH]' >&2; exit 1; }
  case "$1" in
    --node) node_path=$2 ;;
    --python) python_path=$2 ;;
    *) echo 'Usage: sh install.sh [--node PATH] [--python PATH]' >&2; exit 1 ;;
  esac
  shift 2
done
node_path=$(command -v "$node_path") || { echo 'Node.js is required; install it separately.' >&2; exit 1; }
python_path=$(command -v "$python_path") || { echo 'Python 3.11+ is required; install it separately.' >&2; exit 1; }
base=$(CDPATH= cd -P -- "$(dirname -- "$0")" && pwd)
exec "$node_path" "$base/offline.mjs" install --python "$python_path"
