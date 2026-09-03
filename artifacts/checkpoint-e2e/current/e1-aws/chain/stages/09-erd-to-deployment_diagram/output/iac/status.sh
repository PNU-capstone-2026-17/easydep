#!/usr/bin/env bash
set -euo pipefail
export AWS_PROFILE=${AWS_PROFILE:-default}
tofu output -json
