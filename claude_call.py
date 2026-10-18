"""One Claude call that must answer through a tool. Shared by brief.py and private.py."""
from __future__ import annotations

import os
import sys


def forced_call(cfg: dict, system: str, tool: dict, user: str, max_tokens: int,
                 model: str | None = None, timeout: float = 180.0, max_retries: int = 2) -> tuple[dict, dict]:
    """One Claude call that must answer through `tool`. Returns (tool input, usage counts).
    `model` defaults to cfg["model"]. `timeout` (seconds) and `max_retries` go to the client.

    Some models reject a forced tool_choice ("tool" or "any") with a 400. For those models the call uses
    tool_choice "auto" and a system line that says to call the tool. If the answer still has no tool call,
    it asks one more time. Usage counts add up over all calls."""
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=timeout, max_retries=max_retries)
    model = model or cfg["model"]
    usage = {"input_tokens": 0, "output_tokens": 0}

    def ask(sys_text: str, choice: dict):
        msg = client.messages.create(
            model=model, max_tokens=max_tokens, system=sys_text, tools=[tool], tool_choice=choice,
            messages=[{"role": "user", "content": user}])
        usage["input_tokens"] += msg.usage.input_tokens
        usage["output_tokens"] += msg.usage.output_tokens
        return next((b for b in msg.content if b.type == "tool_use" and b.name == tool["name"]), None), msg

    if model not in _NO_FORCED_TOOL:
        try:
            block, msg = ask(system, {"type": "tool", "name": tool["name"]})
            if block is None:
                raise RuntimeError(f"no tool call in response (stop_reason={msg.stop_reason})")
            return dict(block.input), usage
        except Exception as exc:
            if "tool_choice" not in str(exc) or "not supported" not in str(exc):
                raise
            _NO_FORCED_TOOL.add(model)
            print(f"{model}: forced tool_choice not supported, using auto", file=sys.stderr)
    nudge = f"{system}\n\nAnswer only by calling the {tool['name']} tool. Do not answer in plain text."
    for _ in range(2):
        block, msg = ask(nudge, {"type": "auto"})
        if block is not None:
            return dict(block.input), usage
    raise RuntimeError(f"no tool call in response (stop_reason={msg.stop_reason})")


# Models that answered a forced tool_choice with a 400 in this run. They use tool_choice "auto".
_NO_FORCED_TOOL: set[str] = set()
