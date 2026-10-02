#!/usr/bin/env bash
# Platform stage 1: everything the agent image does NOT depend on.
#   ECR -> guardrail -> knowledge base -> IAM -> gateway
# Everything deploys into one region by default; there is no separate step.
# Publishes the SSM parameters the agent team and stage 2 read.
#
# The agent team then builds and pushes an image (agent/build-and-push.sh),
# and stage 2 creates the runtime that runs it.
set -euo pipefail

PROJECT="${PROJECT:-telco-support}"
REGION="${REGION:-ap-southeast-1}"
# The knowledge base deploys alongside everything else. Override KB_REGION and
# EMBEDDING_MODEL together to reproduce the tested variant (us-east-1 +
# amazon.titan-embed-text-v2:0) - see docs/PROJECT_OVERVIEW.md, "Tested variant".
KB_REGION="${KB_REGION:-$REGION}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-cohere.embed-multilingual-v3}"
SSM_PREFIX="${SSM_PREFIX:-/telco-support}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

MOCK_API_STACK="${MOCK_API_STACK:-mock-telco-api}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

out() {  # out <stack> <region> <OutputKey>
  aws cloudformation describe-stacks --stack-name "$1" --region "$2" \
    --query "Stacks[0].Outputs[?OutputKey=='$3'].OutputValue" --output text
}

put_param() {  # put_param <name> <value>
  aws ssm put-parameter --name "$1" --value "$2" --type String --overwrite \
    --region "$REGION" >/dev/null
  echo "  ssm $1 = $2"
}

# --- 0. The mock backend must exist first: the gateway target points at it ----
say "Checking the mock telco API stack ($MOCK_API_STACK)"
if ! aws cloudformation describe-stacks --stack-name "$MOCK_API_STACK" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: stack '$MOCK_API_STACK' not found in $REGION." >&2
  echo "       Deploy it first:  cd mock-telco-api && sam build && sam deploy --guided" >&2
  exit 1
fi
MOCK_API_ID="$(out "$MOCK_API_STACK" "$REGION" RestApiId)"
if [[ -z "$MOCK_API_ID" || "$MOCK_API_ID" == "None" ]]; then
  echo "ERROR: could not read output 'RestApiId' from $MOCK_API_STACK." >&2
  exit 1
fi
echo "  mock API: $MOCK_API_ID"

# --- 1. Foundation: ECR + base SSM -------------------------------------------
say "1/5 Foundation (ECR repository, model parameter)"
aws cloudformation deploy \
  --stack-name "${PROJECT}-foundation" \
  --template-file "$HERE/10-foundation.yaml" \
  --parameter-overrides "ProjectName=$PROJECT" "SsmPrefix=$SSM_PREFIX" \
  --region "$REGION" --no-fail-on-empty-changeset

ECR_URI="$(out "${PROJECT}-foundation" "$REGION" RepositoryUri)"
ECR_ARN="$(out "${PROJECT}-foundation" "$REGION" RepositoryArn)"
echo "  ECR: $ECR_URI"

# --- 2. Guardrail -------------------------------------------------------------
say "2/5 Guardrail"
aws cloudformation deploy \
  --stack-name "${PROJECT}-guardrail" \
  --template-file "$HERE/20-guardrail.yaml" \
  --parameter-overrides "SsmPrefix=$SSM_PREFIX" \
  --region "$REGION" --no-fail-on-empty-changeset

# --- 3. Knowledge base -------------------------------------------------------
say "3/5 Knowledge base in $KB_REGION ($EMBEDDING_MODEL)"
aws cloudformation deploy \
  --stack-name "${PROJECT}-knowledge-base" \
  --template-file "$HERE/30-knowledge-base.yaml" \
  --parameter-overrides "ProjectName=$PROJECT" "EmbeddingModelId=$EMBEDDING_MODEL" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$KB_REGION" --no-fail-on-empty-changeset

KB_ID="$(out "${PROJECT}-knowledge-base" "$KB_REGION" KnowledgeBaseId)"
KB_ARN="$(out "${PROJECT}-knowledge-base" "$KB_REGION" KnowledgeBaseArn)"
KB_BUCKET="$(out "${PROJECT}-knowledge-base" "$KB_REGION" PolicyDocsBucket)"
KB_DS_ID="$(out "${PROJECT}-knowledge-base" "$KB_REGION" DataSourceId)"

# The identifiers are written to SSM explicitly rather than exported from the
# stack, so this still works when KB_REGION is overridden: CloudFormation cannot
# write a parameter into another region.
say "Publishing knowledge base parameters into $REGION"
put_param "$SSM_PREFIX/kb/id"     "$KB_ID"
put_param "$SSM_PREFIX/kb/arn"    "$KB_ARN"
put_param "$SSM_PREFIX/kb/region" "$KB_REGION"

say "Uploading policy documents and starting ingestion"
aws s3 sync "$ROOT/kb/" "s3://$KB_BUCKET/" --delete --region "$KB_REGION"
INGESTION_ID="$(aws bedrock-agent start-ingestion-job \
  --knowledge-base-id "$KB_ID" --data-source-id "$KB_DS_ID" \
  --region "$KB_REGION" --query 'ingestionJob.ingestionJobId' --output text)"
echo "  ingestion job: $INGESTION_ID"

printf '  waiting for ingestion'
while :; do
  STATUS="$(aws bedrock-agent get-ingestion-job \
    --knowledge-base-id "$KB_ID" --data-source-id "$KB_DS_ID" \
    --ingestion-job-id "$INGESTION_ID" --region "$KB_REGION" \
    --query 'ingestionJob.status' --output text)"
  case "$STATUS" in
    COMPLETE) echo " done"; break ;;
    FAILED|STOPPED) echo " $STATUS"; echo "ERROR: ingestion did not complete." >&2; exit 1 ;;
    *) printf '.'; sleep 10 ;;
  esac
done

# --- 4. IAM (needs the guardrail and KB ARNs, so it comes after them) --------
say "4/5 IAM roles"
aws cloudformation deploy \
  --stack-name "${PROJECT}-iam" \
  --template-file "$HERE/40-iam.yaml" \
  --parameter-overrides "ProjectName=$PROJECT" "SsmPrefix=$SSM_PREFIX" \
                        "EcrRepositoryArn=$ECR_ARN" "MockApiId=$MOCK_API_ID" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" --no-fail-on-empty-changeset

# --- 5. Gateway (runtime needs its URL, so it must exist before stage 2) -----
say "5/5 Gateway and API Gateway target"
aws cloudformation deploy \
  --stack-name "${PROJECT}-gateway" \
  --template-file "$HERE/50-gateway.yaml" \
  --parameter-overrides "ProjectName=$PROJECT" "SsmPrefix=$SSM_PREFIX" \
                        "MockApiId=$MOCK_API_ID" \
  --region "$REGION" --no-fail-on-empty-changeset

say "Stage 1 complete"
cat <<EOF

  ECR repository : $ECR_URI
  Knowledge base : $KB_ID ($KB_REGION)
  Gateway URL    : $(out "${PROJECT}-gateway" "$REGION" GatewayUrl)

Next:
  1. agent/build-and-push.sh          # build the ARM64 image and push it
  2. platform/deploy-stage2.sh <image-uri>
EOF
