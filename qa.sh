#!/usr/bin/env bash
set -euxo pipefail

trap 'echo "FAIL"' EXIT

misc/pylint.sh
misc/shellcheck.sh
test/test_quick.sh

trap - EXIT

echo 'OK'
