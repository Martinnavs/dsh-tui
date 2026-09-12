"""Vim-like scrollback over the chat history: a line buffer built once per
render, pagination math over that buffer, and the scroll-offset state
machine. Combined with the other ChatUI mixins in core.py.

``_render_message_into`` calls back into ``RenderingMixin``'s per-role
render methods (``_render_user_message`` etc.) so painting and measuring
a message can never disagree about what it looks like.

The visible window is now expressed in rendered *lines* (rows), not
messages: each message contributes zero or more rows to a single flat
line buffer, and the scroll offset is a count of rows from the bottom.
This is what makes a single message taller than the console reachable a
row at a time instead of being all-or-nothing.

Per-message line lists are cached keyed on ``Message`` object identity
(a strong ref is held so ``id()`` cannot be recycled while cached). This
cache is only ever correct because ``update_last_message``,
``verification_end`` and ``verification_correction`` *replace* the
``Message`` object rather than mutating it in place — a future change
that started mutating a cached ``Message`` in place would silently serve
stale rendered lines. Two cases are handled explicitly: legacy ``dict``
messages (mutated in place by ``update_last_message``) are never cached,
and ``tool_call`` entries are re-rendered when the context-usage label
embedded in their header has changed since they were cached.
"""
from bisect import bisect_left, bisect_right

from rich.align import Align
from rich.text import Text

from .message import Message


class _CacheEntry:
    __slots__ = ("msg", "lines", "ctx_label")

    def __init__(self, msg, lines, ctx_label=None):
        self.msg = msg
        self.lines = lines
        self.ctx_label = ctx_label


class ScrollbackMixin:
    def _render_message_into(self, content: Text, msg) -> None:
        """Append one message's rendering to `content`.

        The single place that decides how a message is painted, shared by
        `render_messages()` (paints) and the line-buffer builder below
        (measures) so the two can never disagree about what a message
        looks like.
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

    def _invalidate_line_cache(self) -> None:
        """Drop the per-message line cache. Called by `set_theme()` since
        styles change without changing any of the global cache key's
        components (console width, show_logs, model_name).
        """
        self._line_cache = {}
        self._line_cache_key = None

    def _ctx_label_for(self, msg) -> "str | None":
        if not isinstance(msg, Message) or msg.role != "tool_call":
            return None
        if self._context_pct > 0:
            return self._fmt_ctx_label()
        return None

    def _rebuild_line_buffer(self) -> None:
        """(Re)build the flat line buffer from `self.messages`.

        Reuses cached per-message line lists wherever the cached entry's
        `Message` is (by identity) still the one at that position and its
        cache-affecting inputs haven't changed. Rebuilding the flat
        concatenation itself is not skipped — it's measured at ~0.4ms warm
        for 1000 messages, which is cheap enough to just always do.
        """
        key = (self.console.size.width, self.show_logs, self.model_name)
        old_cache = getattr(self, "_line_cache", None)
        if old_cache is None or getattr(self, "_line_cache_key", None) != key:
            old_cache = {}

        new_cache = {}
        lines: list = []
        starts_lines: list = []
        starts_idx: list = []

        for i, msg in enumerate(self.messages):
            is_message = isinstance(msg, Message)
            entry = None
            if is_message:
                candidate = old_cache.get(id(msg))
                if candidate is not None and candidate.msg is msg:
                    if candidate.ctx_label == self._ctx_label_for(msg):
                        entry = candidate

            if entry is None:
                t = Text()
                self._render_message_into(t, msg)
                msg_lines = self.console.render_lines(t, pad=False, new_lines=True)[:-1]
                entry = _CacheEntry(msg, msg_lines, self._ctx_label_for(msg) if is_message else None)

            if is_message:
                new_cache[id(msg)] = entry

            if entry.lines:
                starts_lines.append(len(lines))
                starts_idx.append(i)
                lines.extend(entry.lines)

        self._line_cache = new_cache
        self._line_cache_key = key
        self._line_buffer = lines
        self._line_starts_lines = starts_lines
        self._line_starts_idx = starts_idx

    def _ensure_line_buffer(self) -> None:
        """Rebuild the line buffer, degrading to an empty buffer on any
        failure. Reachable from the raw-mode input loop outside any
        `try`, so a raise here must never propagate.
        """
        try:
            self._rebuild_line_buffer()
        except Exception:
            self._line_buffer = []
            self._line_starts_lines = []
            self._line_starts_idx = []
            self._line_cache = {}
            self._line_cache_key = None

    def _is_first_line(self, line_idx: int) -> bool:
        starts = self._line_starts_lines
        pos = bisect_right(starts, line_idx) - 1
        return pos >= 0 and starts[pos] == line_idx

    def _owning_message_index(self, line_idx: int):
        starts = self._line_starts_lines
        pos = bisect_right(starts, line_idx) - 1
        if pos < 0:
            return None
        return self._line_starts_idx[pos]

    def _window_bounds(self):
        """`(start, end, header_msg_index)` for the current scroll offset.

        `start`/`end` are line indices into `self._line_buffer` (half-open
        range). `header_msg_index` is the index into `self.messages` whose
        continuation header should be shown, or `None`.

        Single-step, non-oscillating rule: shift `start` forward by one row
        to make room for a continuation header only when the naive
        (budget-sized) window would begin mid-message; if that shift lands
        exactly on a message boundary, drop the header again rather than
        re-deriving a new window (see ticket 01, Action item 3).
        """
        self._ensure_line_buffer()
        total = len(self._line_buffer)
        if total == 0:
            return 0, 0, None

        budget = max(1, self._content_budget())
        offset = self._scroll_offset
        max_offset = self._max_scroll_offset()
        if offset < 0:
            offset = 0
        elif offset > max_offset:
            offset = max_offset

        end = total - offset
        tentative_start = max(0, end - budget)

        if tentative_start == 0 or budget <= 1 or self._is_first_line(tentative_start):
            return tentative_start, end, None

        start = tentative_start + 1
        if self._is_first_line(start):
            return start, end, None

        header_idx = self._owning_message_index(tentative_start)
        return start, end, header_idx

    def _max_scroll_offset(self) -> int:
        """Furthest an offset can move back before hitting the oldest line."""
        try:
            self._ensure_line_buffer()
            total = len(self._line_buffer)
            budget = max(1, self._content_budget())
            return max(0, total - budget)
        except Exception:
            return 0

    def _window_size(self) -> int:
        """Content-line count of the window at the current scroll offset."""
        try:
            start, end, _ = self._window_bounds()
            return max(0, end - start)
        except Exception:
            return 0

    def _forward_step(self) -> int:
        """Content-line count of the window that *starts* at the current
        window's end.

        `scroll_down` uses this (not `_window_size()`) so the next window
        begins at (or, in the rare header-boundary case, at most one row
        before) exactly where the current one ends — mirroring the
        message-granular tiling argument this method's docstring used to
        make, now expressed in lines. Using `_window_size()` here instead
        would page by the size of the window being *left*; with variable
        message heights the freshly re-measured next window would not
        start where that left off, silently skipping lines that would then
        be unreachable at any offset.
        """
        try:
            self._ensure_line_buffer()
            total = len(self._line_buffer)
            if total == 0:
                return 0
            budget = max(1, self._content_budget())
            max_offset = self._max_scroll_offset()
            offset = self._scroll_offset
            if offset < 0:
                offset = 0
            elif offset > max_offset:
                offset = max_offset
            end = total - offset
            if end >= total:
                return 0
            header_cost = 0
            if budget > 1 and end != 0 and not self._is_first_line(end):
                header_cost = 1
            step = max(1, budget - header_cost)
            return min(step, total - end)
        except Exception:
            return 0

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
        """The messages with >= 1 line in the current scroll offset's window."""
        try:
            if not self.messages:
                return []
            start, end, _ = self._window_bounds()
            if start >= end:
                return []
            starts_lines = self._line_starts_lines
            starts_idx = self._line_starts_idx
            lo = bisect_right(starts_lines, start) - 1
            if lo < 0:
                lo = 0
            hi = bisect_left(starts_lines, end)
            msgs = list(self.messages)
            return [msgs[i] for i in starts_idx[lo:hi]]
        except Exception:
            return []
