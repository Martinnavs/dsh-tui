"""Live turn display: turn timing, incremental token streaming (with its
tag/markdown-link filters), and the inline tool-call/tool-result blocks
printed during a run. Combined with the other ChatUI mixins in core.py.
"""
import json
import re
import time

from rich.text import Text


class StreamingMixin:
    # ── Turn timing ───────────────────────────────────────────────

    def turn_start(self) -> None:
        """Mark the instant the user's task was submitted.

        Everything the turn spends before the first token — instruction
        assembly, tool calls, retries — belongs to the elapsed time the user
        experiences, so the clock starts here rather than at ``stream_start``.
        """
        self._turn_start_time = time.monotonic()

    def _turn_elapsed(self) -> float:
        """Seconds since the turn was submitted, or since the stream started."""
        start = self._turn_start_time or self._stream_start_time
        return time.monotonic() - start if start else 0.0

    def set_metrics(self, sink: dict) -> None:
        """Adopt the run's live token/timing accounting (see TurnMetrics).

        Held by reference, not copied: the footer is printed from inside the
        run, so it has to see what the turn just recorded.
        """
        self._metrics = sink

    @staticmethod
    def format_meta(elapsed_secs: float, tok_s: float = 0.0) -> str:
        """Format the per-answer meta line the way the web UI does.

        ``12.34s · 21.5 tok/s`` — same fields, same separator, so a turn reads
        the same whether it was run in the terminal or in the browser.
        """
        parts = []
        if elapsed_secs > 0:
            parts.append(f"{elapsed_secs:.2f}s")
        if tok_s > 0:
            parts.append(f"{tok_s:.1f} tok/s")
        return " · ".join(parts)

    # ── Inline tool-call display (streaming mode) ──────────────────

    def show_tool_start(self, name: str, arguments: dict) -> None:
        """Print a bordered tool-call block inline (mirrors chat history style)."""
        if not self.show_logs:
            return
        self._erase_stream_cursor()  # pause blinking cursor during tool execution
        ts = self.format_timestamp()
        args_str = json.dumps(arguments, ensure_ascii=False)
        t = Text()
        ctx_suffix = f"  {self._fmt_ctx_label()}" if self._context_pct > 0 else ""
        t.append(f"┌─ ⚙️  {name} ", style="bold dark_orange3")
        t.append(f"[{ts}]", style=self.theme.styles.get("timestamp", "cyan"))
        if ctx_suffix:
            t.append(ctx_suffix, style="bright_cyan")
        t.append("\n", style="default")
        t.append(f"{args_str}\n", style="bright_white")
        t.append("└" + "─" * 40, style="dark_orange3")
        self.console.print(t)

    def start_tool_spinner(self, name: str, arguments: dict) -> None:
        pass  # Bordered output provides visual feedback; spinner not needed here

    def stop_tool_spinner(self) -> None:
        pass

    def tool_log(self, name: str, data: str, level: str = "info") -> None:
        """Display real-time log messages from MCP tools (e.g. sandbox output)."""
        if not self.show_logs:
            return
        style = "bright_yellow" if level == "warning" else "bright_red" if level == "error" else "bright_cyan"
        self.console.print(f"  [{name}] {data}", style=style)

    def tool_progress(self, name: str, elapsed_seconds: int) -> None:
        """No-op for text UI; spinner already indicates progress."""
        pass

    def show_tool_done(self, name: str, result: str, success: bool = True) -> None:
        """Print a bordered tool-result block inline (mirrors chat history style)."""
        if not self.show_logs:
            return
        ts = self.format_timestamp()
        display = result if len(result) <= 300 else result[:300] + "…"
        border_style = "bold green" if success else "bold red"
        t = Text()
        t.append(f"┌─ ↩  {name} ", style=border_style)
        t.append(f"[{ts}]\n", style=self.theme.styles.get("timestamp", "cyan"))
        t.append(f"{display}\n", style="bright_white")
        t.append("└" + "─" * 40, style="green" if success else "red")
        self.console.print(t)

    # ── Token streaming ───────────────────────────────────────────

    def stream_start(self) -> None:
        """Prepare for streaming. Resets all stream state; header is deferred lazily."""
        self.stop_status()  # safety: stop spinner if running
        self._stop_thinking_spinner()  # stop rotating lobby messages
        self._streaming_content = ""
        self._stream_header_printed = False
        self._stream_pending = ""
        self._stream_think_started = False
        self._tag_buf = ""
        self._trail_buf = ""
        self._link_buf = ""
        self._url_buf = ""
        self._link_state = 0
        self._stream_start_time = time.monotonic()

    def stream_think_token(self, token: str) -> None:
        """Print a reasoning/thinking token in dim italic, opening a think block if needed."""
        if not self._stream_think_started:
            self._stream_think_started = True
            self.console.print(
                f"┌─ 💭 Thinking [{self.format_timestamp()}]",
                style="bright_white",
            )
        self.console.print(token, end="", style="bright_white")

    def stream_think_end(self) -> None:
        """Close the thinking block if it is open."""
        if self._stream_think_started:
            print()  # newline after last think token
            self.console.print("└" + "─" * 40, style="bright_white")
            self._stream_think_started = False

    def stream_token(self, token: str) -> None:
        """Stream an answer token. Auto-closes any open think block on first call."""
        self._streaming_content += token
        if self._stream_think_started:
            self.stream_think_end()  # reasoning just finished, close think block
        display = self._filter_display_token(token)
        display = self._filter_markdown_links(display)
        self._emit(display)

    def _emit(self, display: str) -> None:
        """Print already-filtered text, printing the block header if needed."""
        if not display:
            return
        self._erase_stream_cursor()
        if not self._stream_header_printed:
            self._stream_pending += display
            if self._stream_pending.strip():
                # First real answer content — print header then flush buffer
                self._stream_header_printed = True
                self.console.print(
                    f"┌─ 🤖 AI ({self.model_name}) [{self.format_timestamp()}]" if self.model_name else f"┌─ 🤖 AI [{self.format_timestamp()}]",
                    style=self.theme.styles.get("assistant", "bold magenta"),
                )
                print(self._stream_pending.lstrip(), end="", flush=True)
                self._stream_pending = ""
                self._show_stream_cursor()
        else:
            if display.strip():
                # Non-whitespace: flush any buffered trailing whitespace then print
                if self._trail_buf:
                    print(self._trail_buf, end="", flush=True)
                    self._trail_buf = ""
                print(display, end="", flush=True)
                self._show_stream_cursor()
            else:
                # Whitespace-only token: buffer it; discard if stream ends before real content
                self._trail_buf += display
                self._show_stream_cursor()

    def _filter_display_token(self, token: str) -> str:
        """Strip <answer>/<answer> wrapper tags, buffering a partial '<' across tokens."""
        buf = self._tag_buf + token
        buf = buf.replace("<answer>", "").replace("</answer>", "")
        if buf.endswith("<"):
            self._tag_buf = "<"
            return buf[:-1]
        self._tag_buf = ""
        return buf

    def _filter_markdown_links(self, text: str) -> str:
        """Filter markdown links from streaming tokens.

        Buffers ``[label](url)`` patterns across token boundaries and emits
        only the label text, suppressing the URL (which may be a huge
        data-URI).  State machine: 0=normal, 1=inside label ``[…``,
        2=consuming URL after ``](``.
        """
        out = []
        for ch in text:
            if self._link_state == 0:
                if ch == '[':
                    self._link_state = 1
                    self._link_buf = ""
                else:
                    out.append(ch)
            elif self._link_state == 1:
                if ch == ']':
                    # Peek-ahead not possible across tokens; buffer the label
                    # and move to a transition state.  We abuse _link_state=2
                    # but first need to see '('.
                    self._link_state = 2
                elif ch == '\n':
                    # Newline inside bracket — not a link, flush as literal
                    out.append('[')
                    out.append(self._link_buf)
                    out.append(ch)
                    self._link_buf = ""
                    self._link_state = 0
                else:
                    self._link_buf += ch
            elif self._link_state == 2:
                if ch == '(':
                    # Confirmed markdown link — consume URL until ')'
                    self._link_state = 3
                    self._url_buf = ""
                else:
                    # Not a markdown link — flush buffered label as literal
                    out.append('[')
                    out.append(self._link_buf)
                    out.append(']')
                    self._link_buf = ""
                    if ch == '[':
                        # Reference-style "[a][b]" — this bracket opens a label
                        self._link_state = 1
                    else:
                        out.append(ch)
                        self._link_state = 0
            elif self._link_state == 3:
                if ch == ')':
                    # End of URL — emit only the label
                    out.append(self._link_buf)
                    self._link_buf = ""
                    self._url_buf = ""
                    self._link_state = 0
                elif ch == '\n':
                    # A link destination never spans a line, so this was never
                    # a link: give back every character it swallowed.  Without
                    # this an unmatched "](" eats the rest of the answer.
                    out.append('[')
                    out.append(self._link_buf)
                    out.append('](')
                    out.append(self._url_buf)
                    out.append(ch)
                    self._link_buf = ""
                    self._url_buf = ""
                    self._link_state = 0
                else:
                    self._url_buf += ch
        return "".join(out)

    def _flush_filters(self) -> str:
        """Return the text the incremental filters are still holding back.

        Both filters buffer across token boundaries on the chance the next
        token completes a wrapper tag or a markdown link.  When the stream
        ends mid-pattern that bet lost: what they hold is ordinary text and
        still has to be shown, or the answer prints short.
        """
        out = ""
        if self._link_state == 1:
            out = '[' + self._link_buf
        elif self._link_state == 2:
            out = '[' + self._link_buf + ']'
        elif self._link_state == 3:
            out = '[' + self._link_buf + '](' + self._url_buf
        self._link_buf = ""
        self._url_buf = ""
        self._link_state = 0
        out += self._tag_buf  # a lone '<' that never became a tag
        self._tag_buf = ""
        return out

    def _decode_rate(self) -> float:
        """Generation rate in tokens/sec, from the live ``self._metrics`` sink.

        Inlined from the upstream ``decode_rate`` helper (see the adaptation
        notice at the top of this file) to avoid a cross-package import; same
        fields, same zero-on-unset behavior. Zero (and so omitted by
        ``format_meta``) when ``set_metrics()`` was never called, which is the
        case for every stream until a caller wires metrics.
        """
        m = self._metrics
        if not m:
            return 0.0
        tokens = m.get("completion_tokens") or 0
        seconds = m.get("decode_s") or 0.0
        if tokens <= 0 or seconds <= 0:
            return 0.0
        return tokens / seconds

    def stream_end(self, elapsed: str = "") -> None:
        """Close the streamed block. Message saving is handled by the caller (has correct elapsed)."""
        self._erase_stream_cursor()
        self.stream_think_end()  # close think block if still open
        # Whatever the tag/link filters are still holding is plain text now
        # that no further token can complete the pattern — print it.
        self._emit(self._flush_filters())
        self._erase_stream_cursor()  # _emit re-shows it; the block is closing
        if not self._stream_header_printed:
            # Only whitespace or tool-call-only response — skip the empty block
            self._streaming_content = ""
            self._stream_pending = ""
            self._trail_buf = ""
            self._tag_buf = ""
            self._link_buf = ""
            self._url_buf = ""
            self._link_state = 0
            return
        # Discard trailing whitespace — just emit a single newline before the footer
        self._trail_buf = ""
        print()  # final newline after the last token
        footer = "└" + "─" * 40
        # tok/s comes from the provider's own token accounting, so it counts
        # the reasoning printed in the think block above and the tool-call
        # arguments never printed at all -- neither of which reaches
        # stream_token(), and both of which the clock was already charging for.
        # elapsed is the whole turn, matching what the web UI reports.
        tok_s = self._decode_rate()
        meta = elapsed or self.format_meta(self._turn_elapsed(), tok_s)
        if meta:
            footer += f"  {meta}"
        if self._context_pct > 0:
            t = Text()
            t.append(footer, style=self.theme.styles.get("assistant", "magenta"))
            t.append(f"  {self._fmt_ctx_label()}", style="bright_cyan")
            self.console.print(t)
        else:
            self.console.print(footer, style=self.theme.styles.get("assistant", "magenta"))
        # Warn when context is getting full (≥75%)
        if self._context_pct >= 90:
            self.console.print(
                f"  ⚠  Context window {self._context_pct:.0f}% full — compaction imminent",
                style="bold red",
            )
        elif self._context_pct >= 75:
            self.console.print(
                f"  ⚠  Context window {self._context_pct:.0f}% full",
                style="bold yellow",
            )
        # Save to history so intermediate AI turns appear in the chat panel.
        # Strip only known model wrapper tags — a generic <[^>]+> pattern would
        # delete LaTeX/math content that happens to contain < and > characters.
        content = re.sub(r"</?(?:answer|think|stop)>", "", self._streaming_content, flags=re.IGNORECASE).strip()
        if content:
            self.add_message("assistant", content)
        self._streaming_content = ""
        self._stream_pending = ""
        self._tag_buf = ""
        self._link_buf = ""
        self._url_buf = ""
        self._link_state = 0
        self._stream_header_printed = False
