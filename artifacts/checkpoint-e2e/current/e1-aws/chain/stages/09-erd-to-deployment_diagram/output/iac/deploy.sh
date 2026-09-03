#!/usr/bin/env bash
set -euo pipefail
export AWS_PROFILE=${AWS_PROFILE:-default}
tofu init
export TF_VAR_image_digest_course_registration_app="${TF_VAR_image_digest_course_registration_app:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"
tofu apply -auto-approve -target=aws_ecr_repository.registry_course_registration_app
REGISTRY_URL=$(tofu output -raw registry_course_registration_app_url)
REGISTRY_HOST=$(printf "%s" "$REGISTRY_URL" | cut -d/ -f1)
aws ecr get-login-password --region "ap-northeast-2" | docker login --username AWS --password-stdin "$REGISTRY_HOST"
IMAGE_TAG="$REGISTRY_URL:easydep-course_registration_app"
docker build --pull -t "$IMAGE_TAG" ..
PUSH_OUTPUT=$(docker push "$IMAGE_TAG" 2>&1)
printf "%s\n" "$PUSH_OUTPUT"
IMAGE_DIGEST=$(printf "%s\n" "$PUSH_OUTPUT" | sed -n "s/.*digest: \(sha256:[0-9a-f]\{64\}\).*/\1/p" | tail -1)
[ -n "$IMAGE_DIGEST" ] || { echo "docker push did not report an immutable digest" >&2; exit 1; }
export TF_VAR_image_digest_course_registration_app="$IMAGE_DIGEST"
tofu apply "$@"
