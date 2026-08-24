#!/usr/bin/env bash
# build_cli.py için ince sarmalayıcı — imaj içinden doğrudan çalıştırmak için.
#   docker run --rm -v "$PWD/out:/work/out" -v "$PWD/MyGame.zip:/game.zip" \
#       winlator-port ./scripts/build_cli.sh --game /game.zip --app-id com.gameport
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
exec python3 scripts/build_cli.py "$@"
