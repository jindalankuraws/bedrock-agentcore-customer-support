import os
from strands.models.bedrock import BedrockModel

MODEL_ID = os.environ.get("MODEL_ID", "apac.amazon.nova-pro-v1:0")

def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials."""
    return BedrockModel(model_id=MODEL_ID, 
                        region_name="ap-southeast-1",
                        temperature=0,
                        max_tokens=1024,
                        additional_request_fields={'inferenceConfig': {"topK": 1}},
                        # --- Guardrail (comment out this block to disable) ---
                        # Blocks prompt attacks, harmful content and questions about other
                        # customers' accounts. Masks phone, email, NRIC/FIN and Singapore
                        # mobile numbers in BOTH directions, so the model never receives the
                        # raw value and cannot repeat it. Defined in platform/20-guardrail.yaml.
                        guardrail_id=os.environ.get("GUARDRAIL_ID"),
                        guardrail_version=os.environ.get("GUARDRAIL_VERSION"),
                        guardrail_latest_message=True,  # check only the customer's latest message, not tool results
                        # --- end guardrail ---
                        )
