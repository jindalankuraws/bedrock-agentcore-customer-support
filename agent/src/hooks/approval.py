from strands.hooks import BeforeToolCallEvent, AfterToolCallEvent, HookProvider, HookRegistry
import logging

# Same logger as app.logger, so these lines show up in the runtime log
log = logging.getLogger("bedrock_agentcore.app")


class ToolCallLogger(HookProvider):
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def before(self, event: BeforeToolCallEvent) -> None:
        log.info("TOOL CALL   %s input=%s", event.tool_use["name"], event.tool_use["input"])

    def after(self, event: AfterToolCallEvent) -> None:
        log.info("TOOL RESULT %s status=%s cancelled=%s time=%.2fs", event.tool_use["name"],
                 event.result.get("status"), event.cancel_message, event.duration or 0)


class DeclinedToolMessage(HookProvider):
    """When the customer says "no", tell the model plainly that nothing was done.

    Without this, the model gets Strands' raw "CONFIRMATION_FAILED: Approve ...?" text,
    misreads it, and may tell the customer the action was done.
    """
    def register_hooks(self, registry: HookRegistry, **kwargs) -> None:
        registry.add_callback(AfterToolCallEvent, self.rewrite)

    def rewrite(self, event: AfterToolCallEvent) -> None:
        if event.cancel_message and event.cancel_message.startswith("CONFIRMATION_FAILED"):
            # status "success": the tool call finished with a clear outcome (declined), not a system failure,
            # so the model does not fall back to "Sorry, I can't get that information"
            event.result = {**event.result, "status": "success", "content": [{"text":
                "The customer said NO. This action was NOT done. "
                "Tell the customer it was cancelled and ask if they need anything else."}]}
