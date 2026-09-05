"""Patch ChatDev 1.1.6 to ignore additive provider response fields.

Cloudflare's OpenAI-compatible responses may expose fields that did not exist
when ChatDev 1.1.6's CAMEL message dataclass was written. The patch keeps only
the fields accepted by that dataclass instead of forwarding the entire provider
object as keyword arguments.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "# EASYDEP_RESPONSE_FIELD_FILTER"
ANCHOR = """except ImportError:
    openai_new_api = False  # old openai api version
"""
HELPER = '''

# EASYDEP_RESPONSE_FIELD_FILTER
_CHAT_MESSAGE_FIELDS = {
    "role",
    "content",
    "refusal",
    "function_call",
    "tool_calls",
}


def _easydep_chat_message_kwargs(message: Any) -> Dict[str, Any]:
    """Return only fields supported by ChatDev 1.1.6's ChatMessage."""
    values = dict(message)
    return {key: values[key] for key in _CHAT_MESSAGE_FIELDS if key in values}
'''


def patch_chat_agent(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        if text.count("_easydep_chat_message_kwargs(") < 3:
            raise RuntimeError(f"Incomplete existing response-field patch: {path}")
        return False

    if ANCHOR not in text:
        raise RuntimeError(f"ChatDev response parsing anchor not found: {path}")
    new_api = "**dict(choice.message)"
    old_api = "**dict(choice[\"message\"])"
    if text.count(new_api) != 1 or text.count(old_api) != 1:
        raise RuntimeError(f"Unexpected ChatDev response conversion shape: {path}")

    text = text.replace(ANCHOR, ANCHOR + HELPER, 1)
    text = text.replace(new_api, "**_easydep_chat_message_kwargs(choice.message)", 1)
    text = text.replace(
        old_api, "**_easydep_chat_message_kwargs(choice[\"message\"])", 1
    )
    compile(text, str(path), "exec")
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    chat_agent = args.source / "camel" / "agents" / "chat_agent.py"
    changed = patch_chat_agent(chat_agent)
    print("ChatDev response-field patch applied" if changed else "ChatDev response-field patch already present")


if __name__ == "__main__":
    main()
