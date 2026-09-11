"""Vim-like scrollback over the chat history: pagination math and the
scroll-offset state machine. Combined with the other ChatUI mixins in
core.py.

``_render_message_into`` calls back into ``RenderingMixin``'s per-role
render methods (``_render_user_message`` etc.) so painting and measuring
a message can never disagree about what it looks like.
"""
from rich.align import Align
from rich.text import Text

from .message import Message


class ScrollbackMixin:
    def _render_message_into(self, content: Text, msg) -> None:
        """Append one message's rendering to `content`.

        The single place that decides how a message is painted, shared by
        `render_messages()` (paints) and `_message_height()` (measures) so
        the two can never disagree about what a message looks like.
        """
        role = msg.role if isinstance(msg, Message) else msg["role"]
        msg_content = msg.content if isinstance(msg, Message) else msg["content"]
        msg_time = msg.timestamp if isinstance(msg, Message) else msg["time"]
        msg_elapsed = msg.elapsed if isinstance(msg, Message) else msg.get("elapsed", "")
        msg_name = msg.name if isinstance(msg, Message) else msg.get("name", "")

        if role == "user":
            self._render_user_message(content, msg_content, msg_time)
        elif role == "tool_call":
            if self.show_logs:
                self._render_tool_call_message(content, msg_name, msg_content, msg_time)
        elif role == "tool_result":
            if self.show_logs:
                self._render_tool_result_message(content, msg_name, msg_content, msg_time)
        else:
            self._render_assistant_message(content, msg_content, msg_time, msg_elapsed)

    def _message_height(self, msg) -> int:
        """Rendered row count of one message, standalone.

        `render_lines` on a single message's Text always includes one
        trailing blank line (the message body ends in `\\n`) that vanishes
        once messages are concatenated — the `-1` here is what makes per-
        message heights additive when summed by the fit loops below.
        """
        content = Text()
        self._render_message_into(content, msg)
        height = len(self.console.render_lines(content)) - 1
        return max(0, height)

    def _content_budget(self) -> int:
        """Rows available for the message content inside the current frame.

        Mirrors `_print_frame()`'s composition: the messages panel's own
        header/blank/footer rows, the disclaimer (measured — it wraps at
        narrow widths), the blank line `_print_frame` prints after the
        disclaimer, the raw-mode prompt row (drawn outside Rich), and the
        logs panel (measured directly — `render()` only prints it as a
        side effect, so it must not be invoked here or the panel would be
        painted twice).
        """
        reserved = 3  # chat header Rule + blank Text + footer Rule
        disclaimer = Align.center(Text(self.disclaimer, style="bright_white"))
        reserved += len(self.console.render_lines(disclaimer))
        reserved += 1  # trailing blank line _print_frame prints
        reserved += 1  # raw-mode prompt row
        if self.show_logs:
            reserved += len(self.console.render_lines(self.render_logs_panel()))
        return max(1, self.console.size.height - reserved)

    def _fit_backward(self, end: int) -> int:
        """Count of messages ending at index `end` (exclusive) that fit, walking backward.

        At least 1 when `end > 0`, capped at `self.display_messages`.
        Falls back to the old fixed-count formula if measurement raises —
        this is reachable from the raw-mode input loop outside any `try`,
        so a raise here must not propagate.
        """
        if end <= 0:
            return 0
        try:
            msgs = list(self.messages)
            budget = self._content_budget()
            count = 0
            height_sum = 0
            idx = end - 1
            while idx >= 0 and count < self.display_messages:
                h = self._message_height(msgs[idx])
                candidate_total = height_sum + h + 1  # additive identity: sum(m_i) + 1
                if count > 0 and candidate_total > budget:
                    break
                height_sum += h
                count += 1
                idx -= 1
            return max(1, count)
        except Exception:
            return min(self.display_messages, end)

    def _fit_forward(self, start: int = 0) -> int:
        """Count of messages from index `start` that fit, walking forward.

        Mirror of `_fit_backward`; see its docstring for the shared logic
        and fallback behavior.
        """
        n = len(self.messages)
        if start >= n:
            return 0
        try:
            msgs = list(self.messages)
            budget = self._content_budget()
            count = 0
            height_sum = 0
            idx = start
            while idx < n and count < self.display_messages:
                h = self._message_height(msgs[idx])
                candidate_total = height_sum + h + 1  # additive identity: sum(m_i) + 1
                if count > 0 and candidate_total > budget:
                    break
                height_sum += h
                count += 1
                idx += 1
            return max(1, count)
        except Exception:
            return min(self.display_messages, n - start)

    def _window_size(self) -> int:
        """How many messages the current scroll offset's window actually shows."""
        max_offset = self._max_scroll_offset()
        offset = min(self._scroll_offset, max_offset)
        end = len(self.messages) - offset
        return self._fit_backward(end)

    def _forward_step(self) -> int:
        """How many messages fit starting at the current window's end.

        The forward-direction mirror of `_window_size()`. `scroll_down` uses
        this (not `_window_size()`) so the next window begins exactly where
        the current one ends — the same tiling argument that makes
        `scroll_up` gap-free (it moves back by exactly the window it is
        leaving), applied in the other direction. Using `_window_size()`
        here instead would page by the size of the window being *left*, and
        with variable message heights the freshly re-measured next window
        would not start where that left off — silently skipping messages
        that would then be unreachable at any offset.
        """
        max_offset = self._max_scroll_offset()
        offset = min(self._scroll_offset, max_offset)
        end = len(self.messages) - offset
        return self._fit_forward(end)

    def _max_scroll_offset(self) -> int:
        """Furthest an offset can move back before hitting the oldest message."""
        return max(0, len(self.messages) - self._fit_forward(0))

    def _reset_scroll(self) -> None:
        """Snap the view back to the bottom; called whenever new content arrives."""
        self._scroll_offset = 0

    def scroll_up(self, pages: int = 1) -> bool:
        """Move the visible window further back in history.

        Returns whether the offset actually changed (False at the top).
        """
        max_offset = self._max_scroll_offset()
        new_offset = min(max_offset, self._scroll_offset + pages * self._window_size())
        changed = new_offset != self._scroll_offset
        self._scroll_offset = new_offset
        return changed

    def scroll_down(self, pages: int = 1) -> bool:
        """Move the visible window toward the newest message.

        Returns whether the offset actually changed (False at the bottom).
        """
        new_offset = max(0, self._scroll_offset - pages * self._forward_step())
        changed = new_offset != self._scroll_offset
        self._scroll_offset = new_offset
        return changed

    def scroll_to_top(self) -> bool:
        """Jump to the oldest message. Returns whether the offset changed."""
        max_offset = self._max_scroll_offset()
        changed = max_offset != self._scroll_offset
        self._scroll_offset = max_offset
        return changed

    def scroll_to_bottom(self) -> bool:
        """Jump to the newest message. Returns whether the offset changed."""
        changed = self._scroll_offset != 0
        self._scroll_offset = 0
        return changed

    def _visible_messages(self) -> list:
        """The slice of `self.messages` the current scroll offset shows."""
        if not self.messages:
            return []
        max_offset = self._max_scroll_offset()
        offset = min(self._scroll_offset, max_offset)
        msgs = list(self.messages)
        end = len(msgs) - offset
        count = self._window_size()
        return msgs[end - count:end]
