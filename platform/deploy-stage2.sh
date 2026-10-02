#!/usr/bin/env bash
# Platform stage 2: the parts that depend on the agent image.
#   runtime + endpoint -> observability
#
# Usage: deploy-stage2.sh [image-uri]
# With no argument the image URI is read from SSM, where build-and-push.sh put it.
set -euo pipefail

PROJECT="${PROJECT:-telco-support}"
RUNTIME_NAME="${RUNTIME_NAME:-telco_support}"   # letters, digits, underscores only
REGION="${REGION:-ap-southeast-1}"
SSM_PREFIX="${SSM_PREFIX:-/telco-support}"
ENDPOINT_NAME="${ENDPOINT_NAME:-live}"
ALARM_EMAIL="${ALARM_EMAIL:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

out() {
  aws cloudformation describe-stacks --stack-name "$1" --region "$2" \
    --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text
}

IMAGE_URI="${1:-}"
if [[ -z "$IMAGE_URI" ]]; then
  IMAGE_URI="$(aws ssm get-parameter --name "$SSM_PREFIX/agent/image-uri" \
    --region "$REGION" --query 'Parameter.Value' --output text 2>/dev/null || true)"
fi
if [[ -z "$IMAGE_URI" || "$IMAGE_URI" == "None" ]]; then
  echo "ERROR: no image URI given and $SSM_PREFIX/agent/image-uri is not set." >&2
  echo "       Run agent/build-and-push.sh first, or pass the URI as an argument." >&2
  exit 1
fi
echo "Image: $IMAGE_URI"

if [[ -z "$ALARM_EMAIL" ]]; then
  echo "ERROR: set ALARM_EMAIL so the tool-error alarm can notify someone." >&2
  exit 1
fi

# --- 1. Runtime + endpoint ---------------------------------------------------
say "1/2 AgentCore runtime and endpoint"
aws cloudformation deploy \
  --stack-name "${PROJECT}-runtime" \
  --template-file "$HERE/60-runtime.yaml" \
  --parameter-overrides "ProjectName=$RUNTIME_NAME" "SsmPrefix=$SSM_PREFIX" \
                        "ImageUri=$IMAGE_URI" "EndpointName=$ENDPOINT_NAME" \
  --region "$REGION" --no-fail-on-empty-changeset

RUNTIME_ARN="$(out "${PROJECT}-runtime" "$REGION" RuntimeArn)"
LOG_GROUP="$(out "${PROJECT}-runtime" "$REGION" LogGroup)"
SERVICE_NAME="$(out "${PROJECT}-runtime" "$REGION" ServiceName)"
GATEWAY_ARN="$(out "${PROJECT}-gateway" "$REGION" GatewayArn)"
GUARDRAIL_ARN="$(aws ssm get-parameter --name "$SSM_PREFIX/guardrail/arn" \
  --region "$REGION" --query 'Parameter.Value' --output text)"
GUARDRAIL_VERSION="$(aws ssm get-parameter --name "$SSM_PREFIX/guardrail/version" \
  --region "$REGION" --query 'Parameter.Value' --output text)"
MODEL_ID="$(aws ssm get-parameter --name "$SSM_PREFIX/model/id" \
  --region "$REGION" --query 'Parameter.Value' --output text)"

# --- 2. Observability --------------------------------------------------------
say "2/2 Dashboard and tool-error alarm"
aws cloudformation deploy \
  --stack-name "${PROJECT}-observability" \
  --template-file "$HERE/70-observability.yaml" \
  --parameter-overrides "ProjectName=$PROJECT" "RuntimeArn=$RUNTIME_ARN" \
                        "GatewayArn=$GATEWAY_ARN" "GuardrailArn=$GUARDRAIL_ARN" \
                        "GuardrailVersion=$GUARDRAIL_VERSION" \
                        "ModelId=$MODEL_ID" "AlarmEmail=$ALARM_EMAIL" \
  --region "$REGION" --no-fail-on-empty-changeset

say "Stage 2 complete"
cat <<EOF

  Runtime ARN : $RUNTIME_ARN
  Endpoint    : $ENDPOINT_NAME
  Dashboard   : $(out "${PROJECT}-observability" "$REGION" DashboardUrl)

Confirm the SNS subscription in your email, then run the evaluation gate:

  cd agent && python eval/run_batch_eval.py \\
    --runtime-arn  "$RUNTIME_ARN" \\
    --log-group    "$LOG_GROUP" \\
    --service-name "$SERVICE_NAME" \\
    --endpoint     "$ENDPOINT_NAME" \\
    --region       "$REGION"
EOF
