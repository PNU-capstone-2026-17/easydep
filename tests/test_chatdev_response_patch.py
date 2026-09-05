from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evaluation.baselines.patch_chatdev_response_fields import (
    MARKER,
    patch_chat_agent,
)


SOURCE = '''from typing import Any, Dict
try:
    from openai.types.chat import ChatCompletion
    openai_new_api = True  # new openai api version
except ImportError:
    openai_new_api = False  # old openai api version

def convert(choice):
    first = ChatMessage(meta_dict=dict(), **dict(choice.message))
    second = ChatMessage(meta_dict=dict(), **dict(choice["message"]))
    return first, second
'''


def _source_tree(root: Path, content: str = SOURCE) -> Path:
    path = root / "camel" / "agents" / "chat_agent.py"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    return path


class ChatDevResponsePatchTests(unittest.TestCase):
    def test_patch_filters_unknown_provider_fields_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="easydep-chatdev-patch-test-") as directory:
            path = _source_tree(Path(directory))

            self.assertTrue(patch_chat_agent(path))
            patched = path.read_text(encoding="utf-8")
            self.assertIn(MARKER, patched)
            self.assertNotIn("**dict(choice.message)", patched)
            self.assertNotIn("**dict(choice[\"message\"])", patched)
            self.assertEqual(patched.count("_easydep_chat_message_kwargs("), 3)
            compile(patched, str(path), "exec")

            self.assertFalse(patch_chat_agent(path))
            self.assertEqual(path.read_text(encoding="utf-8"), patched)

    def test_patch_fails_closed_when_upstream_shape_changes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="easydep-chatdev-patch-test-") as directory:
            path = _source_tree(Path(directory), "# incompatible upstream source\n")

            with self.assertRaisesRegex(RuntimeError, "anchor not found"):
                patch_chat_agent(path)


if __name__ == "__main__":
    unittest.main()
