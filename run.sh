#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"
exec /usr/bin/python3 -B -m neo_mint_theme "$@"
