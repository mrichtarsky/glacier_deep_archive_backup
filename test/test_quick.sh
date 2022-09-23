#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH=$ROOT

pushd "$ROOT" >/dev/null
trap 'popd >/dev/null' EXIT

pytest-3 -v -v --showlocals --capture=no --full-trace -m 'not longrunner' "$@"
