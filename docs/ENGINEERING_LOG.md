# Engineering log

Defects found while deploying and testing the stack, each recorded with its
symptom, cause and fix. None of them was visible from documentation or schemas
alone.

---

## 1. Guardrail topic definitions are capped at ~200 characters

**Symptom.** `telco-support-guardrail` failed on create:

> One or more of your guardrail topic definitions exceeds the maximum allowed
> length.

**Cause.** The CloudFormation schema advertises `maxLength: 1000` on
`TopicConfig.Definition`. The service enforces far less on the default (CLASSIC)
topic tier. My definition was ~290 characters.

**Fix.** Shortened to 165 characters. The service limit applies, not the
maximum advertised in the schema.

---

## 2. A denied topic applied to output blocked legitimate answers

**Symptom.** Asking "how much is my bill?" as a signed-in customer returned the
guardrail's blocked-output message instead of the bill.

**Cause.** The `OtherCustomerAccounts` denied topic was active on both input and
output. On output, the classifier sees text describing an account and a bill and
cannot tell *whose* account it is, so it blocked the customer's own bill.

**Fix.** `InputAction: BLOCK`, `OutputAction: NONE`, `OutputEnabled: false`. The
topic is about what the customer *asks for*, so it belongs on the input side only.

---

## 3. Editing a guardrail does not change what the agent uses

**Symptom.** After fixing defect 2 and redeploying, the behaviour was unchanged.

**Cause.** A published guardrail version is an immutable snapshot. Editing the
policies updates `DRAFT`; the pinned version keeps serving the old rules. The
`AWS::Bedrock::GuardrailVersion` resource had no property change, so
CloudFormation did not publish a new version.

**Fix.** A `ConfigRevision` parameter, interpolated into the version's
description. Bumping it forces a new immutable version. Visible and deliberate,
rather than depending on a hash of the config.

---

## 4. `!Ref` on an S3 Vectors bucket returns its ARN

**Symptom.** The knowledge base stack failed:

> `#/VectorBucketName: expected maxLength: 63, actual: 82`

**Cause.** `!Ref` on `AWS::S3Vectors::VectorBucket` returns the ARN, not the
name. 82 characters is the ARN length.

**Fix.** Restate the bucket name with the same `!Sub` expression used to create
it, and use `DependsOn` for ordering.

---

## 5. A gateway target without a credential provider calls the API unsigned

**Symptom.** Every tool call returned
`403 {"message":"Missing Authentication Token"}`. The agent fell back to
"Sorry, I can't get that information right now."

**The misleading part.** That message normally means *no such route*. The route
existed: a direct SigV4 call to `/demo/plans` returned 200 while `/plans`
returned 403, so the path looked wrong. It was not. API Gateway returns the same
message for an **unsigned** request to an `AWS_IAM`-protected method.

Ruled out along the way: the gateway role's `execute-api:Invoke` permission
(`simulate-principal-policy` said `allowed`), the role trust policy (CloudTrail
showed `AssumeRole` succeeding), and `basePath` (every variant failed).

**Cause.** `AWS::BedrockAgentCore::GatewayTarget` has no default credential
provider. Without one the gateway calls the backend with no SigV4 signature.

**Fix.** `CredentialProviderConfigurations: [{ CredentialProviderType: GATEWAY_IAM_ROLE }]`.
An explicit `IamCredentialProvider` block is rejected for an ApiGateway target —
*"only MCP Server, OpenAPI, and Passthrough targets can configure
IamCredentialProvider"* — the type alone is enough.

---

## 6. `{{resolve:ssm:...}}` is not a stack diff

**Symptom.** After publishing guardrail version 2, every invocation failed with
*"The guardrail identifier or version provided in the request does not exist."*

**Cause.** The runtime's environment variables used `{{resolve:ssm:...}}`
dynamic references. Those are resolved at deploy time, but CloudFormation does
not treat a changed SSM **value** as a stack change when the template and
parameters are identical. The deploy reported success and changed nothing, so
the runtime stayed pinned to version 1 — which had just been replaced.

**Fix.** `AWS::SSM::Parameter::Value<String>` parameters instead. These resolve
into the stack's parameter set, so a changed value is a real diff and the
runtime is updated.

This is the defect most likely to affect other projects: a dynamic reference
looks like live configuration, but a changed value alone does not update the
stack.

---

## 7. Batch evaluation needs CloudWatch Transaction Search

**Symptom.** The first evaluation run failed all 10 sessions:

> Provided input contains 3 log event(s) but no span documents. Log events alone
> cannot be evaluated — the corresponding span documents are required. Please
> ensure that transaction search is enabled and retry.

**Cause.** Transaction Search was off, so the X-Ray trace segment destination was
`XRay` and spans were never indexed into CloudWatch Logs where the evaluator
reads them.

**Fix.** `AWS::XRay::TransactionSearchConfig` in `platform/10-foundation.yaml`,
so it is part of the stack rather than a console step someone has to remember.
Note it is account-level and bills per GB of spans ingested.

---

## 8. Batch evaluation names reject hyphens

**Symptom.** `ValidationException: Value at 'batchEvaluationName' failed to
satisfy constraint: Member must satisfy regular expression pattern:
[a-zA-Z][a-zA-Z0-9_]{0,47}`

**Cause.** The generated name was `telco-golden-20261001-062710`.

**Fix.** Underscores. Cheap, but it cost a full invocation round plus the 180
second ingestion wait before failing.

---

## 9. The guardrail dashboard widget was keyed on the wrong dimensions

**Symptom.** The guardrail panel was empty while interventions were
occurring.

**Cause.** Guardrail metrics are emitted with **both** `GuardrailArn` and
`GuardrailVersion` dimensions. A widget keyed on the ARN alone matches nothing.

**Fix.** The version is now a stack parameter, passed from SSM, and part of the
widget. Found by querying the metric by hand rather than trusting the panel —
an empty graph looks identical to "nothing happened".

---

## 10. PII masking was configured in one direction only

**Symptom.** `ApplyGuardrail` with `source=INPUT` returned `action: NONE` and
left an NRIC untouched, while `source=OUTPUT` masked it correctly.

**Initial misdiagnosis.** This was first recorded as "ANONYMIZE is an
output-side control" and documented as a platform limitation. That was
incorrect.

**Cause.** `PiiEntityConfig` and `RegexConfig` take `InputAction`,
`InputEnabled`, `OutputAction` and `OutputEnabled` separately. Setting `Action`
alone did not enable the input direction.

**Fix.** `InputEnabled: true` and `InputAction: ANONYMIZE` on every entity and
regex. Input is now masked too, verified on both sources, so the model never
receives a raw NRIC rather than merely being told not to repeat one.

**Lesson.** Before recording a platform limitation, check the schema for an
option that has not been set.

---

## 11. Bedrock's PHONE entity misses bare Singapore numbers

**Symptom.** `+65 9123 4567` was masked as `{PHONE}`; `91234567` was not — and
the bare 8-digit form is what Singaporeans actually type, and what the mock data
contains.

**Fix.** A `singapore-mobile` regex, `\b[89][0-9]{7}\b`, beside the NRIC/FIN one.

**Side effect, accepted.** With enough surrounding context both the built-in
entity and the regex match, so a number can come back as
`{PHONE}{singapore-mobile}`. Removing the overlap would mean dropping the
built-in entity and losing international formats.

---

## 12. An interrupted tool call emits a span the evaluator cannot read

**Symptom.** Two sessions fail every evaluation run with
`Failed to parse event body for span with ID: ...`. Both are the
human-in-the-loop scenarios.

**Cause.** A completed tool call emits a span body with `input` and `output`.
`HumanInTheLoop` pauses the call before it runs, so the span has `input` only
and the evaluator rejects the half-span.

**Not fixed.** This is how an interrupted tool call is traced; avoiding it would
mean giving up the interrupt, which is the feature. Covered instead by
[integration tests](evidence/hitl-tests.md) against the deployed runtime.

**Consequence.** The two human-in-the-loop scenarios cannot be scored by batch
evaluation. After the q9 fix, q9 moved from *scored and failing* to
*not scorable*, which raised the averages. This is called out in [ANALYSIS.md](../agent/eval/ANALYSIS.md).
