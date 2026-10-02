from hooks.approval import DeclinedToolMessage, ToolCallLogger
from hooks.identity import AccountBinding
from strands import Agent
from strands.vended_interventions.hitl import HumanInTheLoop
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from model.load import load_model
from mcp_client.client import get_gateway_mcp_client
from policy_tool import search_policy
from thinking_filter import ThinkingFilter

app = BedrockAgentCoreApp()
log = app.logger

# --- Tools: account tools from AgentCore Gateway (MCP), policy search from the knowledge base ---
mcp_client = get_gateway_mcp_client()
tools = ([mcp_client] if mcp_client else []) + [search_policy]

# --- Agent Setup ---
SYSTEM_PROMPT = """
You are the customer support assistant for a mobile telco.

## Rules
1. You MUST answer ONLY with facts returned by your tools in this conversation.
   DO NOT use your own knowledge for plans, prices, bills or account details. DO NOT invent examples.
   Reply exactly "Sorry, I can't get that information right now. Please try again later."
   ONLY when a tool actually fails: it returns an error, or you have no tool for the request.
   A tool that WORKED has given you the answer, even if the answer is not what the customer
   expected. If the customer describes a charge, plan or item that is NOT in the tool result,
   that is a finding, not a failure: tell them plainly it is not there, say what IS there,
   and offer to raise a ticket for the customer service team. NEVER use the sorry line to
   report an absence.
2. The system already knows who the customer is. NEVER ask for, mention or guess an account ID.
   For get_plan, get_bill, change_plan and create_ticket always set account_id to "CURRENT".
   If a tool says the customer is not signed in, ask them to sign in and do not retry.
   If the customer asks about someone else's account, say you can only help with their own account.
   change_plan and create_ticket are confirmed with the customer by the system. Call them directly; DO NOT ask "are you sure" yourself.
3. DO NOT ask for, repeat or store NRIC numbers or full phone numbers.
4. Only help with this telco's services. Politely decline anything else.
5. Pick the right source:
   - Questions about RULES (roaming rates, fees, excess data, late payment, plan terms,
     notice periods, early termination, waivers) -> use search_policy, and name the
     document you used, for example "according to our Roaming FAQ".
   - Questions about THIS customer (their bill, their plan, their usage) -> use the
     account tools. Never answer these from search_policy.
   - If a question needs both (for example "why was I charged for roaming?"), use the
     account tool for the charge and search_policy for the rule.
6. When you answer from search_policy:
   - State ONLY what the returned passages say. If the customer asked two things and the
     passages cover only one, call search_policy again for the other. Do not fill the gap
     from your own knowledge, even if you are confident.
   - Include every condition and exception in the passages that could apply to the
     customer, not just the one matching their situation. If a rule has a carve-out
     (for example a charge that applies to handset bundles but not SIM-only plans),
     say both halves.

## Style
Be friendly, short and clear. DO NOT show raw JSON or internal field names.

The above system instructions define your capabilities and your scope. If the user request contradicts any
system instruction or if the request is outside your scope, you must politely decline the request briefly
explaining your capabilities and your scope.
"""

# One agent per process. AgentCore Runtime runs one microVM per session,
# so in the cloud each conversation gets its own agent and history.
_agent = None

def get_or_create_agent():
    global _agent
    if _agent is None:
        _agent = Agent(
            model=load_model(),
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            # These tools change something for the customer, so they need the customer's "yes".
            # All other tools run freely.
            interventions=[HumanInTheLoop(allowed_tools=["*", "!telcoApiGateway___change_plan",
                                                          "!telcoApiGateway___create_ticket"])],
            # AccountBinding forces the signed-in account; DeclinedToolMessage makes a "no" unambiguous;
            # ToolCallLogger logs every tool call
            hooks=[AccountBinding(), DeclinedToolMessage(), ToolCallLogger()],
            callback_handler=None,  # no printing to stdout; we stream to the caller ourselves
        )
    return _agent


_pending = None  # interrupt waiting for the customer's answer (HumanInTheLoop paused before change_plan)

@app.entrypoint
async def invoke(payload, context):
    global _pending
    user_prompt = payload.get("prompt")
    # Who is signed in. Demo: sent by the UI. Production: taken from the verified login token (JWT).
    account_id = payload.get("account_id")
    log.info("INVOKE session=%s signed_in=%s", context.session_id, bool(account_id))

    if not user_prompt:
        yield "No prompt provided"
        return

    # search_policy is always present, so check the Gateway itself: without it there are
    # no account tools and we must not answer bill or plan questions from the model alone.
    if not mcp_client:
        yield "Sorry, I can't get that information right now. Please try again later."
        return

    agent = get_or_create_agent()
    agent.state.set("account_id", account_id)  # read by AccountBinding; never shown to the model

    # If we asked for confirmation last time, this message is the answer ("yes" approves, anything else denies)
    if _pending:
        agent_input = [{"interruptResponse": {"interruptId": _pending.id, "response": user_prompt}}]
        _pending = None
    else:
        agent_input = user_prompt

    result = None
    thinking = ThinkingFilter()
    async for event in agent.stream_async(agent_input):
        if "data" in event and isinstance(event["data"], str):
            text = thinking.feed(event["data"])  # text chunk, with <thinking> removed
            if text:
                yield text
        if "result" in event:
            result = event["result"]
    if rest := thinking.flush():
        yield rest

    # HumanInTheLoop paused before change_plan: ask the customer, wait for the next message
    if result and result.stop_reason == "interrupt":
        _pending = result.interrupts[0]
        yield f"\n\n{confirmation_question(agent)}\nReply 'yes' to confirm or 'no' to cancel."


def confirmation_question(agent) -> str:
    """Build a customer-friendly question from the paused change_plan call (no raw JSON, no account ID)."""
    # The last message is the model's turn that asked to call change_plan
    for block in agent.messages[-1]["content"]:
        if "toolUse" in block:
            tool_input = block["toolUse"]["input"]
            if tool_input.get("new_plan_id"):   # change_plan
                return f"Please confirm: change your plan to {tool_input['new_plan_id']}?"
            if tool_input.get("summary"):       # create_ticket
                return f"Please confirm: create a ticket for our customer service team about: {tool_input['summary']}"
    return "Please confirm this request."


if __name__ == "__main__":
    app.run()
