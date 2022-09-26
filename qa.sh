#!/usr/bin/env bash
set -euxo pipefail

misc/pylint.sh
misc/shellcheck.sh
test/test.sh

echo 'OK'
