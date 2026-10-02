"""search_policy: answers policy questions from the telco's published policy documents.

The knowledge base holds only public policy documents - roaming, fees, plan terms
and contract termination. No customer or account data is stored in it.

The runtime role is allowed bedrock:Retrieve on this one knowledge base ARN only.
"""
import os
import re

import boto3
from strands import tool

KB_ID = os.environ.get("KB_ID")

# The knowledge base region is configuration, not a constant: the platform stacks
# publish it to SSM and CloudFormation passes it in as KB_REGION. It defaults to the
# runtime's own region, which is where the knowledge base normally lives. It is
# overridable because the published evaluation results were measured with the
# knowledge base in another region - see docs/PROJECT_OVERVIEW.md, "Tested variant".
KB_REGION = os.environ.get("KB_REGION") or os.environ.get("AWS_REGION", "ap-southeast-1")

# Raised from 5 to 8 after the kb2 evaluation failure, where a two-part question
# ("what happens if I go over, and does unused data roll over?") returned five chunks
# all from fees-and-charges.md. The rollover rule lives in plan-terms.md, so the agent
# answered it from its own knowledge - right answer, no grounding (Faithfulness 0.75).
#
# numberOfResults was the lever rather than re-chunking: the four policy documents total
# under 8 KB, so chunk quality was not the problem - cross-document coverage was. Changing
# the chunking strategy forces a full re-ingest and would risk the scenarios already
# scoring 1.00, for a question that simply needed to see more documents.
MAX_RESULTS = 8

_client = None


def _get_client():
    """One client per process, created on first use (keeps cold start cheap)."""
    global _client
    if _client is None:
        _client = boto3.client("bedrock-agent-runtime", region_name=KB_REGION)
    return _client


def _document_name(uri: str) -> str:
    """Turn s3://bucket/contract-termination.md into "Contract Termination"."""
    stem = re.sub(r"\.[^.]+$", "", uri.rsplit("/", 1)[-1])
    return stem.replace("-", " ").replace("_", " ").title()


@tool
def search_policy(question: str) -> dict:
    """Search the telco's published policy documents and return the passages that answer the question.

    Use this for questions about rules and policies: roaming rates and daily passes,
    fees and charges, excess data, late payment, plan terms, notice periods, early
    termination and waivers. Do NOT use it for a specific customer's bill, plan or
    usage - those come from the account tools.

    Args:
        question: The customer's policy question, in their own words.

    Returns:
        The matching passages, each with the name of the document it came from.
    """
    if not KB_ID:
        return {"status": "error", "content": [{"text": "The policy knowledge base is not configured."}]}

    response = _get_client().retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": question},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": MAX_RESULTS}},
    )

    results = response.get("retrievalResults", [])
    if not results:
        return {"status": "success", "content": [{"text": "No policy document covers that question."}]}

    passages = []
    for item in results:
        uri = item.get("location", {}).get("s3Location", {}).get("uri", "")
        passages.append(f"[{_document_name(uri)}]\n{item['content']['text']}")

    return {"status": "success", "content": [{"text": "\n\n---\n\n".join(passages)}]}
