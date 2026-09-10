"""Offline replay test for `dsh_tui.driver.make_driver`.

Feeds the real captured event sequence from
`investigation/probe/probe-output-2-tool.txt` through the driver against a
recording stub (not the real `ChatUI` — no Rich rendering, no terminal).
No network, no credentials: the fixture is a static text file already on
disk from the earlier probe session.

`probe-output-2-tool.txt` is `notification_probe.py`'s own console log, not
raw JSON: full payloads for `tool/call`/`tool/result` (the probe script
prints those in full), truncated 160-char previews for every other event
type. `_load_probe_events` parses the full JSON where the log line has it
and reconstructs a minimal-but-faithful event for the rest -- sufficient
because the driver ignores every type it doesn't recognize, and for
`assistant/message` (which the driver does act on) the probe's own
`#N, X chars: '...'` companion line carries the exact text via `repr()`,
which `ast.literal_eval` recovers losslessly.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from dsh_tui.driver import make_driver

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "investigation" / "probe" / "probe-output-2-tool.txt"
)

_EVENT_LINE_RE = re.compile(r"^\[[\d.]+\] session\.event (\S*) (\{.*)$")
_PREVIEW_RE = re.compile(r"^\s*-> assistant/message #(\d+), (\d+) chars: (.*)$")


def _load_probe_events(path: Path) -> list[SimpleNamespace]:
    lines = path.read_text().splitlines()
    notifications: list[SimpleNamespace] = []
    for i, line in enumerate(lines):
        m = _EVENT_LINE_RE.match(line)
        if not m:
            continue
        etype, json_str = m.groups()
        try:
            event: dict[str, Any] = json.loads(json_str)["event"]
        except json.JSONDecodeError:
            event = {"type": etype}
            if etype == "assistant/message":
                preview_match = _PREVIEW_RE.match(lines[i + 1])
                assert preview_match, f"expected assistant/message preview after: {line!r}"
                text = ast.literal_eval(preview_match.group(3))
                event["data"] = {"message": {"content": [{"type": "text", "text": text}]}}
        notifications.append(SimpleNamespace(method="session.event", payload={"event": event}))
    return notifications


class RecordingChatUI:
    """Stub `ChatUI`: records every call made on it, renders nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


def test_replay_probe_2_tool_call_sequence():
    events = _load_probe_events(FIXTURE_PATH)
    assert len(events) == 19  # matches probe-output-2-tool.txt's own reported session.event count

    chat_ui = RecordingChatUI()
    on_notification = make_driver(chat_ui)
    for event in events:
        on_notification(event)

    assert chat_ui.calls == [
        # turn/start marks both the elapsed-time clock and the thinking spinner.
        ("turn_start", (), {}),
        ("start_thinking", (), {}),
        # First assistant/message (0 chars) is suppressed: stop_thinking fires,
        # but no stream_start/stream_token/stream_end.
        ("stop_thinking", (), {}),
        ("add_tool_call", ("bash", {"command": "echo hello-from-dsh-tool", "description": "Echo test string"}), {}),
        ("show_tool_start", ("bash", {"command": "echo hello-from-dsh-tool", "description": "Echo test string"}), {}),
        ("start_tool_spinner", ("bash", {"command": "echo hello-from-dsh-tool", "description": "Echo test string"}), {}),
        ("stop_tool_spinner", (), {}),
        ("add_tool_result", ("bash", "hello-from-dsh-tool\n"), {}),
        ("show_tool_done", ("bash", "hello-from-dsh-tool\n"), {"success": True}),
        # Thinking spinner restarts after the tool result so the remainder of
        # the turn (waiting on the final assistant/message) isn't dead air.
        ("start_thinking", (), {}),
        ("stop_thinking", (), {}),
        ("stream_start", (), {}),
        ("stream_token", ("\n\nDone. The command ran successfully and printed:\n\n```\nhello-from-dsh-tool\n```",), {}),
        ("stream_end", (), {}),
    ]


def test_empty_first_assistant_message_is_suppressed():
    events = _load_probe_events(FIXTURE_PATH)
    chat_ui = RecordingChatUI()
    on_notification = make_driver(chat_ui)
    for event in events:
        on_notification(event)

    stream_calls = [c for c in chat_ui.calls if c[0].startswith("stream_")]
    assert len(stream_calls) == 3  # exactly one stream_start/stream_token/stream_end triplet
    assert stream_calls[0][0] == "stream_start"
    assert stream_calls[1] == ("stream_token", ("\n\nDone. The command ran successfully and printed:\n\n```\nhello-from-dsh-tool\n```",), {})
    assert stream_calls[2][0] == "stream_end"


def test_tool_result_is_error_renders_as_failure():
    """`isError: true` never appeared in a real probe run (both captured runs
    succeeded); synthesized here from the same payload shape NOTES.md
    documents (`data.message.content[0]`: `toolCallId`, `isError`,
    `content[0].text`), with `isError` flipped, to exercise the failure path."""
    call_event = SimpleNamespace(
        method="session.event",
        payload={
            "event": {
                "type": "tool/call",
                "data": {
                    "turn": 1,
                    "step": 1,
                    "callId": "chatcmpl-tool-synthetic-error",
                    "name": "bash",
                    "arguments": '{"command":"exit 1"}',
                },
            }
        },
    )
    result_event = SimpleNamespace(
        method="session.event",
        payload={
            "event": {
                "type": "tool/result",
                "data": {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "source": {"kind": "tool", "callId": "chatcmpl-tool-synthetic-error"},
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "chatcmpl-tool-synthetic-error",
                                "content": [{"type": "text", "text": "command failed"}],
                                "isError": True,
                            }
                        ],
                        "role": "user",
                        "id": "synthetic",
                    },
                },
            }
        },
    )

    chat_ui = RecordingChatUI()
    on_notification = make_driver(chat_ui)
    on_notification(call_event)
    on_notification(result_event)

    show_tool_done_calls = [c for c in chat_ui.calls if c[0] == "show_tool_done"]
    assert show_tool_done_calls == [
        ("show_tool_done", ("bash", "command failed"), {"success": False}),
    ]


def test_tool_result_unmatched_call_id_defaults_name_to_tool():
    """A `tool/result` whose `toolCallId` was never buffered by a `tool/call`
    (unlikely, but not impossible if a call/result pair spans a reconnect)
    must not render the literal string "None" as the tool name."""
    result_event = SimpleNamespace(
        method="session.event",
        payload={
            "event": {
                "type": "tool/result",
                "data": {
                    "turn": 1,
                    "step": 1,
                    "message": {
                        "source": {"kind": "tool", "callId": "unmatched-call-id"},
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "unmatched-call-id",
                                "content": [{"type": "text", "text": "some result"}],
                                "isError": False,
                            }
                        ],
                        "role": "user",
                        "id": "synthetic",
                    },
                },
            }
        },
    )

    chat_ui = RecordingChatUI()
    on_notification = make_driver(chat_ui)
    on_notification(result_event)

    show_tool_done_calls = [c for c in chat_ui.calls if c[0] == "show_tool_done"]
    assert show_tool_done_calls == [
        ("show_tool_done", ("tool", "some result"), {"success": True}),
    ]
