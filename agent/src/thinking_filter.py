"""Hides Nova's <thinking>...</thinking> text from the customer.

Nova writes its reasoning in <thinking> tags when it may call tools. AWS recommends
dropping that text rather than trying to stop the model producing it.
The reply is streamed in small chunks, and a tag can be split across two chunks
("<think" + "ing>"), so we hold back the last few characters until we know.
"""

OPEN, CLOSE = "<thinking>", "</thinking>"


class ThinkingFilter:
    def __init__(self):
        self.buffer = ""      # text we have not released yet
        self.inside = False   # are we inside a <thinking> block?

    def feed(self, chunk: str) -> str:
        """Add one streamed chunk; return the part that is safe to show the customer."""
        self.buffer += chunk
        visible = ""

        # Handle every complete tag in the buffer
        while True:
            tag = CLOSE if self.inside else OPEN
            pos = self.buffer.find(tag)
            if pos == -1:
                break
            if not self.inside:
                visible += self.buffer[:pos]         # text before <thinking> is customer text
            self.buffer = self.buffer[pos + len(tag):]
            self.inside = not self.inside

        # No complete tag left. Keep a tail that could be the start of a tag; release the rest.
        split = max(0, len(self.buffer) - (len(tag) - 1))
        ready, self.buffer = self.buffer[:split], self.buffer[split:]
        if not self.inside:
            visible += ready
        return visible

    def flush(self) -> str:
        """End of the reply: release whatever is left (unless it is unfinished thinking)."""
        rest, self.buffer = self.buffer, ""
        return "" if self.inside else rest
