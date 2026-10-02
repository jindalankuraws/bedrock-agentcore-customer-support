import os
import logging
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

logger = logging.getLogger(__name__)

GATEWAY_URL_ENV = "AGENTCORE_GATEWAY_TELCO_GATEWAY_URL"

def get_gateway_mcp_client() -> MCPClient | None:
    """MCP client for the telco AgentCore Gateway, signed with IAM (SigV4)."""
    url = os.environ.get(GATEWAY_URL_ENV)
    if not url:
        logger.warning("%s not set - gateway tools unavailable", GATEWAY_URL_ENV)
        return None
    region = os.environ.get("AWS_REGION", "ap-southeast-1")
    return MCPClient(
        lambda: aws_iam_streamablehttp_client(url, aws_service="bedrock-agentcore", aws_region=region)
    )
