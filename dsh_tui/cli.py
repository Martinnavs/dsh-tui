"""Entry point for the dsh-tui interactive terminal chat.

Reads all configuration from the environment -- provider id, model, and
DSH_HOME are set by ``scripts/run-dsh-tui.sh``, which renders
``settings.yaml.template`` into that DSH_HOME before this module ever runs.
This module never reads a secrets file itself; it only reads
``os.environ`` (and doesn't even need the API key directly -- the dsh
runtime subprocess reads ``DSH_TUI_API_KEY`` itself via ``apiKeyEnv`` in
``settings.yaml``, per the discipline in ``scripts/run-dsh-tui.sh``).

Main loop, per PLAN.md Task 4: ``harness.start_session(id)`` is called
**once per process** -- not per turn -- because that is what makes turn 2
see turn 1's context (confirmed live, investigation/NOTES.md "Probe 3"
finding (a)). Every subsequent turn reuses the same held ``Session``.
"""
from __future__ import annotations

import os
import sys
import uuid

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

from dsh_tui.chat_ui import ChatUI
from dsh_tui.driver import make_driver

DEFAULT_PROVIDER = "onit"
DEFAULT_MODEL = "Qwen/Qwen3.8-27B"
BYE = "\\bye"


def _dsh_home() -> str:
    dsh_home = os.environ.get("DSH_TUI_DSH_HOME")
    if not dsh_home:
        print(
            "DSH_TUI_DSH_HOME is not set -- run via scripts/run-dsh-tui.sh, "
            "which renders settings.yaml and sets it.",
            file=sys.stderr,
        )
        sys.exit(1)
    return dsh_home


def main() -> None:
    provider = os.environ.get("DSH_TUI_PROVIDER", DEFAULT_PROVIDER)
    model = os.environ.get("DSH_TUI_MODEL", DEFAULT_MODEL)
    dsh_home = _dsh_home()

    if not os.environ.get("DSH_TUI_API_KEY"):
        # The dsh runtime subprocess needs this (apiKeyEnv in settings.yaml);
        # failing fast here avoids a confusing auth error later. Never print
        # the value -- there is none to print, only its absence is checked.
        print(
            "DSH_TUI_API_KEY is not set -- run via scripts/run-dsh-tui.sh.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = DeepSeekHarnessConfig(
        provider=provider,
        model=model,
        profile="sdk",
        dsh_home=dsh_home,
    )

    chat_ui = ChatUI(
        agent_cursor="dsh",
        banner_title="dsh Chat Interface",
        disclaimer=(
            "dsh may produce inaccurate information. "
            "Verify important details independently."
        ),
        # show_logs=True: add_tool_call/show_tool_start/add_tool_result/
        # show_tool_done all early-return when show_logs is False (see
        # chat_ui/streaming.py), so this is required for acceptance criterion 3
        # (tool calls render inline, never invisibly) -- not optional.
        show_logs=True,
        # Default max_messages=100 caps scrollback at ~25 tool-using turns
        # (each appends 2+ messages with show_logs=True); raise it so the
        # scroll feature can reach further back into history.
        max_messages=1000,
    )
    driver = make_driver(chat_ui)

    session_id = f"dsh-tui-{uuid.uuid4().hex[:8]}"
    with DeepSeekHarness(config) as harness:
        session = harness.start_session(session_id)
        while True:
            user_input = chat_ui.get_user_input()
            if user_input == BYE:
                break
            try:
                session.run(user_input, on_notification=driver)
            except Exception as exc:  # noqa: BLE001 -- surface, don't crash the loop
                # The driver's own thinking-spinner state is private to its
                # closure (dsh_tui/driver.py), so an exception raised between
                # `turn/start` and the next event (e.g. a network failure
                # while the spinner is running) can leave it spinning here.
                # stop_thinking() is safe to call even when nothing is
                # spinning (chat_ui/spinner.py's _stop_thinking_spinner no-ops on
                # None state), so this is a harmless belt-and-suspenders call.
                chat_ui.stop_thinking()
                # notice() only prints -- get_user_input()'s next call clears
                # the console before this line could ever be read (see
                # chat_ui/input.py get_user_input()), and notice() never touches
                # self.messages, so without also recording it in history the
                # failure would leave no trace after the redraw. Follow
                # onit's own precedent of recording provider/turn failures
                # into the visible chat history.
                chat_ui.notice(f"Turn failed: {exc}", level="error")
                chat_ui.add_message("system", f"Turn failed: {exc}")


if __name__ == "__main__":
    main()
