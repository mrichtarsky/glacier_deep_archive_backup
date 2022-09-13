#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC2046
shellcheck $(git ls-files '*.sh') backup_scratch backup_resume extract_archive restore
