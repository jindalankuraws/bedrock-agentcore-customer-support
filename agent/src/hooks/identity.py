"""Ties every account tool call to the signed-in customer.

The model never chooses the account ID:
  1. The UI sends the signed-in customer's account_id with each request.
  2. main.py stores it in agent.state (per-agent key/value storage, not visible to the model).
  3. This hook writes it into the tool input just before the tool runs.

So even if a prompt injection makes the model ask for another customer's bill,
the API is still called with the signed-in customer's account.
"""
import logging

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

log = logging.getLogger("bedrock_agentcore.app")

# Tools that take an account_id (tool names without the "telcoApiGateway___" prefix)
ACCOUNT_TOOLS = {"get_plan", "get_bill", "change_plan", "create_ticket"}


class AccountBinding(HookProvider):
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.drop_base_path)
        registry.add_callback(BeforeToolCallEvent, self.bind_account)

    def drop_base_path(self, event: BeforeToolCallEvent) -> None:
        # The Gateway adds a "basePath" input to every tool (the API Gateway stage, default "demo").
        # The model must never choose where calls go, so we always remove it and the Gateway uses the default.
        # (An empty basePath sent by the model once caused 403 errors.)
        event.tool_use["input"].pop("basePath", None)

    def bind_account(self, event: BeforeToolCallEvent) -> None:
        tool = event.tool_use["name"].split("___")[-1]
        if tool not in ACCOUNT_TOOLS:
            return  # e.g. get_all_plans needs no account

        account_id = event.agent.state.get("account_id")
        if not account_id:
            # Guest: cancel the call. This text goes back to the model as the tool result.
            event.cancel_tool = "The customer is not signed in. Ask them to sign in to see or change their account."
            return

        requested = event.tool_use["input"].get("account_id")
        if requested not in (None, "CURRENT", account_id):
            # The model tried another account (e.g. prompt injection). Log it, then overwrite.
            log.warning("ACCOUNT OVERRIDE tool=%s requested=%s used=%s", tool, requested, account_id)
        event.tool_use["input"]["account_id"] = account_id
