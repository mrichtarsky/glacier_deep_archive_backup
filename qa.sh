#!/usr/bin/env bash
set -euxo pipefail

trap 'echo "FAIL"' EXIT

dev/pylint.sh
dev/shellcheck.sh
test/test_quick.sh

trap - EXIT

echo 'OK'
