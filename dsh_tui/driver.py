"""Maps dsh `session.event` notifications onto `ChatUI` calls.

Evolved from `investigation/probe/notification_probe.py:on_notification`.
Event shapes are the ones captured live in
`investigation/probe/probe-output-2-tool.txt` (see
`investigation/NOTES.md` for how they were verified). Every event type
with no MVP `ChatUI` equivalent is ignored outright, not stubbed.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from dsh_tui.chat_ui import ChatUI


def make_driver(chat_ui: ChatUI) -> Callable[[Any], None]:
    """Return an `on_notification` callback bound to `chat_ui`.

    `pending_tool_calls` buffers `callId -> name` so a later `tool/result`
    (which only carries `toolCallId`) can be rendered with the tool's name.
    """
    pending_tool_calls: dict[str, str] = {}
    state = {"thinking": False}

    def _stop_thinking_if_needed() -> None:
        if state["thinking"]:
            chat_ui.stop_thinking()
            state["thinking"] = False

    def on_notification(n: Any) -> None:
        if n.method != "session.event":
            return
        event = n.payload.get("event")
        if not isinstance(event, dict):
            return
        etype = event.get("type")
        data = event.get("data") or {}

        if etype == "turn/start":
            chat_ui.turn_start()
            chat_ui.start_thinking()
            state["thinking"] = True
            return

        if etype == "tool/call":
            call_id = data.get("callId")
            name = data.get("name")
            raw_arguments = data.get("arguments")
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
            except (TypeError, ValueError):
                arguments = {}
            if call_id is not None and name is not None:
                pending_tool_calls[call_id] = name
            _stop_thinking_if_needed()
            chat_ui.add_tool_call(name, arguments)
            chat_ui.show_tool_start(name, arguments)
            chat_ui.start_tool_spinner(name, arguments)
            return

        if etype == "tool/result":
            message = data.get("message") or {}
            content_list = message.get("content") or []
            first_content = content_list[0] if content_list else {}
            tool_call_id = first_content.get("toolCallId")
            name = pending_tool_calls.get(tool_call_id) or "tool"
            is_error = bool(first_content.get("isError"))
            inner_content = first_content.get("content") or []
            first_inner = inner_content[0] if inner_content else {}
            text = first_inner.get("text", "")
            chat_ui.stop_tool_spinner()
            chat_ui.add_tool_result(name, text)
            chat_ui.show_tool_done(name, text, success=not is_error)
            chat_ui.start_thinking()
            state["thinking"] = True
            return

        if etype == "assistant/message":
            message = data.get("message") or {}
            content_list = message.get("content") or []
            text = "".join(
                block.get("text", "")
                for block in content_list
                if isinstance(block, dict) and block.get("type") == "text"
            )
            _stop_thinking_if_needed()
            if not text:
                return
            chat_ui.stream_start()
            chat_ui.stream_token(text)
            chat_ui.stream_end()
            return

        # system/message, user/message, request/header, request/context,
        # session/title, agent/inbox/spliced, step/start, step/end, turn/end:
        # no MVP ChatUI equivalent, ignored.

    return on_notification
