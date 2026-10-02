#!/usr/bin/env bash
# Delete everything this project created, newest dependency first.
# Run after the screenshots and the recording are captured.
set -uo pipefail

PROJECT="${PROJECT:-telco-support}"
REGION="${REGION:-ap-southeast-1}"
# Same region as everything else by default; override only if the knowledge base
# was deployed elsewhere (the tested variant).
KB_REGION="${KB_REGION:-$REGION}"
SSM_PREFIX="${SSM_PREFIX:-/telco-support}"
MOCK_API_STACK="${MOCK_API_STACK:-mock-telco-api}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

if [[ "$KB_REGION" == "$REGION" ]]; then SCOPE="${REGION}"; else SCOPE="${REGION} and ${KB_REGION}"; fi
read -r -p "Delete all ${PROJECT} resources in ${SCOPE}? [y/N] " reply
[[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "Cancelled."; exit 0; }

drop() {  # drop <stack> <region>
  if aws cloudformation describe-stacks --stack-name "$1" --region "$2" >/dev/null 2>&1; then
    say "Deleting $1 ($2)"
    aws cloudformation delete-stack --stack-name "$1" --region "$2"
    aws cloudformation wait stack-delete-complete --stack-name "$1" --region "$2" \
      && echo "  deleted" || echo "  WARNING: delete did not complete cleanly - check the console"
  else
    echo "  $1 ($2) not present, skipping"
  fi
}

# S3 buckets must be emptied before CloudFormation can delete them.
say "Emptying the policy documents bucket"
KB_BUCKET="$(aws cloudformation describe-stacks --stack-name "${PROJECT}-knowledge-base" \
  --region "$KB_REGION" --query "Stacks[0].Outputs[?OutputKey=='PolicyDocsBucket'].OutputValue" \
  --output text 2>/dev/null || true)"
if [[ -n "$KB_BUCKET" && "$KB_BUCKET" != "None" ]]; then
  aws s3 rm "s3://$KB_BUCKET" --recursive --region "$KB_REGION" >/dev/null 2>&1 || true
  # Versioning is on, so delete the versions and markers too.
  aws s3api list-object-versions --bucket "$KB_BUCKET" --region "$KB_REGION" \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null \
    | python3 -c "
import json,subprocess,sys
d=json.load(sys.stdin)
objs=(d or {}).get('Objects') or []
for i in range(0,len(objs),1000):
    subprocess.run(['aws','s3api','delete-objects','--bucket','$KB_BUCKET','--region','$KB_REGION',
                    '--delete',json.dumps({'Objects':objs[i:i+1000]})],check=False,
                   stdout=subprocess.DEVNULL)
" 2>/dev/null || true
  echo "  emptied $KB_BUCKET"
fi

# ECR images block repository deletion.
say "Deleting container images"
REPO="${PROJECT}/agent"
IDS="$(aws ecr list-images --repository-name "$REPO" --region "$REGION" \
  --query 'imageIds[*]' --output json 2>/dev/null || echo '[]')"
if [[ "$IDS" != "[]" && -n "$IDS" ]]; then
  aws ecr batch-delete-image --repository-name "$REPO" --region "$REGION" \
    --image-ids "$IDS" >/dev/null 2>&1 && echo "  images deleted" || true
fi

drop "${PROJECT}-observability"  "$REGION"
drop "${PROJECT}-runtime"        "$REGION"
drop "${PROJECT}-gateway"        "$REGION"
drop "${PROJECT}-iam"            "$REGION"
drop "${PROJECT}-knowledge-base" "$KB_REGION"
drop "${PROJECT}-guardrail"      "$REGION"
drop "${PROJECT}-foundation"     "$REGION"
drop "$MOCK_API_STACK"           "$REGION"

say "Removing SSM parameters"
NAMES="$(aws ssm get-parameters-by-path --path "$SSM_PREFIX" --recursive \
  --region "$REGION" --query 'Parameters[].Name' --output text 2>/dev/null || true)"
if [[ -n "$NAMES" ]]; then
  # shellcheck disable=SC2086
  aws ssm delete-parameters --names $NAMES --region "$REGION" >/dev/null 2>&1 \
    && echo "  deleted: $NAMES"
fi

say "Teardown finished"
echo "The budget 'demo-monthly' is left in place; delete it with:"
echo "  aws budgets delete-budget --account-id \$(aws sts get-caller-identity --query Account --output text) --budget-name demo-monthly"
