#!/usr/bin/env bash
set -euo pipefail

IGNORES=(
    -d invalid-name
    -d missing-function-docstring
    -d missing-class-docstring
    -d missing-module-docstring
    -d redefined-outer-name
    -d too-few-public-methods
    -d too-many-locals
    -d unspecified-encoding
    -d wrong-import-order
)

# shellcheck disable=SC2046
pylint "${IGNORES[@]}" $(git ls-files '*.py')
