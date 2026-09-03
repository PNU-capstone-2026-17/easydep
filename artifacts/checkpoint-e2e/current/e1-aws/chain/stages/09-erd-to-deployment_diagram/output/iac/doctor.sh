#!/usr/bin/env bash
set -euo pipefail
export AWS_PROFILE=${AWS_PROFILE:-default}
command -v tofu >/dev/null
command -v docker >/dev/null
command -v aws >/dev/null
tofu version
