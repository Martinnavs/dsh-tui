'''
# Copyright 2025 Rowel Atienza. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Adapted from onit/src/ui/text.py (upstream repo: sibyl-oracles/onit).
# Changes from upstream, per Apache-2.0 Section 4(b):
#   1. stream_end() no longer imports decode_rate from onit's
#      src.model.serving.chat; tok/s is computed inline from
#      self._metrics so the module has no onit-package dependency.
#   2. The hardcoded "OnIt may produce inaccurate information..."
#      disclaimer string is now a constructor-configurable
#      `disclaimer` attribute instead of being repeated verbatim.
#   3. Added vim-like scrollback over the chat history (PgUp/PgDn,
#      Ctrl+U/Ctrl+F, Home/End) with a scroll-aware footer and a
#      raw-terminal-safe repaint path; render_messages() now renders
#      the scrolled slice instead of always the last N messages.
#   4. Added a vim-style scroll mode (standalone Esc toggles it; j/k
#      and arrows scroll, gg/G jump top/bottom, Enter/i/Esc return to
#      typing) so a mouse-wheel scroll that a terminal reports as plain
#      arrow-key bytes (indistinguishable from a real keypress) can't be
#      misread as input-history recall.
#   5. render_messages() now windows by measured rendered height
#      (Console.render_lines) instead of a fixed message count, so the
#      visible slice actually fits the terminal; display_messages is a
#      ceiling on that window rather than the count always shown.
#   6. Split the originally-monolithic module into this package: ChatUI's
#      responsibilities (scrollback math, frame rendering, spinners,
#      token streaming, fact-check display, raw-terminal input) are each
#      a mixin in their own file, assembled into the one ChatUI class
#      below. Behavior and the public API are unchanged.


Text-based UI components for the application.

If the `rich` library is available, it will be used to enhance the console output.
If not, it will fall back to standard print statements.

'''
import json
import threading
from collections import deque
from typing import Literal, Optional

from rich.console import Console
from rich.status import Status
from rich.theme import Theme

from .input import InputMixin
from .message import Message
from .rendering import RenderingMixin
from .scrollback import ScrollbackMixin
from .spinner import SpinnerMixin
from .status import StatusMixin
from .streaming import StreamingMixin
from datetime import datetime


class ChatUI(
    ScrollbackMixin,
    RenderingMixin,
    SpinnerMixin,
    StreamingMixin,
    StatusMixin,
    InputMixin,
):
    def __init__(
        self,
        theme: str = "white",
        agent_cursor: str = "OnIt",
        max_messages: int = 100,
        display_messages: int = 15,
        max_history: int = 100,
        max_logs: int = 100,
        display_logs: int = 10,
        show_logs: bool = False,
        banner_title: str = "OnIt Chat Interface",
        disclaimer: str = (
            "dsh may produce inaccurate information. "
            "Verify important details independently."
        ),
    ) -> None:
        """
        Initialize ChatUI with bounded memory and configuration options.

        Args:
            theme: Color theme ("white" or "dark"/"black")
            agent_cursor: Name of the AI agent
            max_messages: Maximum number of messages to keep in memory (default: 100)
            display_messages: Ceiling on the number of recent messages displayed;
                the actual count is whatever fits the terminal (default: 15)
            max_history: Maximum number of input history items to keep (default: 100)
            max_logs: Maximum number of execution logs to keep in memory (default: 100)
            display_logs: Number of recent logs to display (default: 10)
            show_logs: Whether to show the execution logs panel (default: False)
            banner_title: Title text shown in the startup banner (default: "OnIt Chat Interface")
            disclaimer: Disclaimer text shown below the banner and above the input prompt
        """
        self.banner_title = banner_title
        self.disclaimer = disclaimer
        # Use deque for bounded memory - automatically removes old messages
        self.messages = deque(maxlen=max_messages)
        self.display_messages = display_messages
        # Messages scrolled back from the newest; 0 = bottom = today's behavior.
        self._scroll_offset = 0
        self.agent_cursor = agent_cursor
        # Corrections from a fact-check that finished after its answer did.
        # Held rather than printed: see verification_correction().
        self.pending_corrections: list[str] = []
        # Input history for arrow key navigation (like bash)
        self.input_history: deque[str] = deque(maxlen=max_history)
        self.history_index = -1  # -1 means not browsing history
        # Execution logs for debugging
        self.execution_logs: deque[dict] = deque(maxlen=max_logs)
        self.display_logs = display_logs
        self.show_logs = show_logs
        self.set_theme(theme)
        self.console = Console(theme=self.theme)
        self.status = Status(
            f"[{self.theme.styles.get('prompt', 'bold dark_blue')}] 💡 {agent_cursor}...[/]",
            spinner="dots",
            console=self.console
        )
        self._spinner_messages = [
            "On it...",
            "Analyzing your request...",
            "Thinking...",
            "Planning...",
            "Reasoning...",
            "Searching for answers...",
            "Connecting the dots...",
            "Analyzing the details...",
            "Crunching the numbers...",
            "Almost there...",
            "Piecing it together...",
            "Diving deeper...",
            "Finalizing...",
        ]
        self._spinner_step = 0
        self._spinner_timer: Optional[threading.Timer] = None
        self._spinner_stop_event: Optional[threading.Event] = None
        self._thinking_stop_event: Optional[threading.Event] = None
        self._thinking_thread: Optional[threading.Thread] = None
        self.model_name = ""  # auto-detected model name, set by chat()
        self.data_path = ""  # set when code files are saved to a data directory
        self._context_pct: float = 0.0  # context window usage 0-100, updated after each LLM call
        self._context_max_tokens: int = 0  # max context window size in tokens
        self._stream_header_printed = False  # lazy: header deferred until first visible token
        self._stream_pending = ""  # buffer tokens until first non-whitespace
        self._stream_think_started = False  # True while a think block is open
        self._tag_buf = ""  # buffer for partial tag detection across tokens
        self._trail_buf = ""  # buffer whitespace-only tokens to suppress trailing blank lines
        self._stream_cursor_shown = False  # blinking block cursor during streaming
        self._link_buf = ""  # buffer for detecting markdown links during streaming
        self._url_buf = ""  # URL chars swallowed in state 3, kept to restore a non-link
        self._link_state = 0  # 0=normal, 1=in label [..., 2=after ](, eating URL
        self._metrics: dict = {}  # live TurnMetrics sink; the source of tok/s
        self._stream_start_time = 0.0  # monotonic time when streaming started
        # Monotonic time the current turn was submitted.  The web UI times a
        # turn from the request, not from the first token, so tool calls and
        # thinking are inside the number it prints; this is that same clock.
        self._turn_start_time = 0.0
        self.initialize()

    def set_theme(self, theme: str) -> None:
        """Set theme for the agent UI"""

        if "black" in theme or "dark" in theme:
            self.theme = Theme({
                "info": "bold dark_cyan",
                "warning": "bold yellow",
                "error": "bold red",
                "prompt": "bold white",
                "timestamp": "white"
            })
        else:
            self.theme = Theme({
                "info": "bold dark_cyan",
                "warning": "bold dark_green",
                "error": "dark_orange3",
                "prompt": "bold dark_blue",
                "timestamp": "cyan"
            })

    def format_timestamp(self):
        return datetime.now().strftime("%I:%M %p %d %b")

    def add_message(
        self,
        role: Literal["user", "assistant", "system"],
        response: str,
        elapsed: str = ""
    ) -> None:
        """
        Add a message to the chat history.

        Args:
            role: The role of the message sender
            response: The message content (parameter name matches chat.py usage)
            elapsed: Optional elapsed time string
        """
        try:
            msg = Message(
                role=role,
                content=response,
                timestamp=self.format_timestamp(),
                elapsed=elapsed
            )
            self.messages.append(msg)
        except Exception as e:
            # Fallback: still add message even if there's an error
            # This ensures messages are never lost due to type issues
            self.messages.append({
                "role": role,
                "content": response,
                "time": self.format_timestamp(),
                "elapsed": elapsed
            })
        self._reset_scroll()

    def update_last_message(self, additional_content: str) -> None:
        """
        Update the last message by appending content (useful for streaming responses).

        Args:
            additional_content: Content to append to the last message
        """
        if self.messages:
            last_msg = self.messages[-1]
            if isinstance(last_msg, Message):
                # Create new Message with updated content (dataclasses are immutable by default)
                self.messages[-1] = Message(
                    role=last_msg.role,
                    content=last_msg.content + additional_content,
                    timestamp=last_msg.timestamp,
                    elapsed=last_msg.elapsed
                )
            else:
                # Legacy dict support
                last_msg["content"] += additional_content

    def clear_messages(self, keep_last: int = 0) -> None:
        """
        Clear messages, optionally keeping the last N messages.

        Args:
            keep_last: Number of recent messages to keep (default: 0 clears all)
        """
        if keep_last > 0:
            messages_to_keep = list(self.messages)[-keep_last:]
            self.messages.clear()
            self.messages.extend(messages_to_keep)
        else:
            self.messages.clear()
        self._reset_scroll()

    def add_tool_call(self, name: str, arguments: dict) -> None:
        """Add a tool invocation message inline in the chat history."""
        if not self.show_logs:
            return
        self.messages.append(Message(
            role="tool_call",
            content=json.dumps(arguments),
            timestamp=self.format_timestamp(),
            name=name,
        ))
        self._reset_scroll()

    def add_tool_result(self, name: str, result: str, truncate: int = 300) -> None:
        """Add a tool result message inline in the chat history."""
        if not self.show_logs:
            return
        display = result if len(result) <= truncate else result[:truncate] + "…"
        self.messages.append(Message(
            role="tool_result",
            content=display,
            timestamp=self.format_timestamp(),
            name=name,
        ))
        self._reset_scroll()

    def add_log(
        self,
        message: str,
        level: Literal["info", "warning", "error", "debug"] = "info"
    ) -> None:
        """
        Add a log entry to the execution logs.

        Args:
            message: The log message
            level: Log level - "info", "warning", "error", or "debug"
        """
        log_entry = {
            "message": message,
            "level": level,
            "timestamp": self.format_timestamp()
        }
        self.execution_logs.append(log_entry)

    def clear_logs(self, keep_last: int = 0) -> None:
        """
        Clear execution logs, optionally keeping the last N entries.

        Args:
            keep_last: Number of recent logs to keep (default: 0 clears all)
        """
        if keep_last > 0:
            logs_to_keep = list(self.execution_logs)[-keep_last:]
            self.execution_logs.clear()
            self.execution_logs.extend(logs_to_keep)
        else:
            self.execution_logs.clear()

    def set_show_logs(self, show: bool) -> None:
        """
        Enable or disable the execution logs panel.

        Args:
            show: True to show logs panel, False to hide
        """
        self.show_logs = show
