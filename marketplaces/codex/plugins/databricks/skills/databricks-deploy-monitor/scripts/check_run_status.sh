#!/usr/bin/env bash
# Public entrypoint for the bounded Databricks Jobs run-status reader.
# Usage:
#   bash scripts/check_run_status.sh <run_id> <profile>
#   TOOL_INPUT='{"run_id":"12345","profile":"dev"}' bash scripts/check_run_status.sh

set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' '{"error":{"code":"dependency_unavailable","message":"python3 is required"},"tasks_complete":false,"outcome_complete":false,"is_success":false}' >&2
    exit 1
fi

exec python3 "$script_dir/check_run_status.py" "$@"
