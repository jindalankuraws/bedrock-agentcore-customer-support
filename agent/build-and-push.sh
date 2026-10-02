#!/usr/bin/env bash
# Agent team: build the ARM64 image and push it to the platform team's ECR repo.
# Publishes the resulting image URI (by digest) to SSM for platform stage 2.
#
# Runs between platform stage 1 and stage 2.
set -euo pipefail

REGION="${REGION:-ap-southeast-1}"
SSM_PREFIX="${SSM_PREFIX:-/telco-support}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

ECR_URI="$(aws ssm get-parameter --name "$SSM_PREFIX/ecr/repository-uri" \
  --region "$REGION" --query 'Parameter.Value' --output text 2>/dev/null || true)"
if [[ -z "$ECR_URI" || "$ECR_URI" == "None" ]]; then
  echo "ERROR: $SSM_PREFIX/ecr/repository-uri is not set." >&2
  echo "       Run platform/deploy-stage1.sh first." >&2
  exit 1
fi
REGISTRY="${ECR_URI%%/*}"

say "Logging in to $REGISTRY"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

say "Building linux/arm64 image  $ECR_URI:$TAG"
# --provenance=false keeps the push a plain image manifest; AgentCore resolves a
# single-platform image, not a buildx attestation index.
docker buildx build \
  --platform linux/arm64 \
  --provenance=false \
  -t "$ECR_URI:$TAG" \
  -t "$ECR_URI:latest" \
  -f "$HERE/Dockerfile" \
  --push \
  "$HERE"

# Pin by digest so a redeploy can never pick up a different build behind the tag.
DIGEST="$(aws ecr describe-images --region "$REGION" \
  --repository-name "${ECR_URI#*/}" \
  --image-ids "imageTag=$TAG" \
  --query 'imageDetails[0].imageDigest' --output text)"
IMAGE_URI="${ECR_URI}@${DIGEST}"

aws ssm put-parameter --name "$SSM_PREFIX/agent/image-uri" --value "$IMAGE_URI" \
  --type String --overwrite --region "$REGION" >/dev/null

say "Pushed"
cat <<EOF

  Tag    : $ECR_URI:$TAG
  Digest : $IMAGE_URI
  SSM    : $SSM_PREFIX/agent/image-uri

Next:  platform/deploy-stage2.sh
EOF
