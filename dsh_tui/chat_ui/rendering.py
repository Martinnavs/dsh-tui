"""Painting the chat frame: the messages panel, per-role message blocks,
the logs panel, and the startup banner. Combined with the other ChatUI
mixins in core.py.

Relies on ``ScrollbackMixin`` for the line buffer, ``_window_bounds``,
and ``_max_scroll_offset``.
"""
import re
from itertools import chain

from rich.align import Align
from rich.box import ROUNDED, HEAVY
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.segment import Segments
from rich.text import Text

from .message import Message


class RenderingMixin:
    def render_logs_panel(self) -> Panel:
        """
        Render the execution logs panel.

        Returns:
            Panel: A Rich Panel containing the execution logs
        """
        if not self.execution_logs:
            content = Text("No execution logs yet.", style="dim")
        else:
            content = Text()
            display_logs = list(self.execution_logs)[-self.display_logs:]

            level_styles = {
                "info": "cyan",
                "warning": "yellow",
                "error": "red",
                "debug": "dim white"
            }
            level_icons = {
                "info": "ℹ️ ",
                "warning": "⚠️ ",
                "error": "❌",
                "debug": "🔍"
            }

            for log in display_logs:
                level = log.get("level", "info")
                style = level_styles.get(level, "white")
                icon = level_icons.get(level, "•")
                timestamp = log.get("timestamp", "")
                message = log.get("message", "")

                content.append(f"{icon} ", style=style)
                content.append(f"[{timestamp}] ", style="dim")
                content.append(f"{message}\n", style=style)

        return Panel(
            content,
            title="[bold white]🔧 Execution Logs[/]",
            title_align="left",
            border_style="yellow",
            box=ROUNDED,
            padding=(0, 1),
            subtitle=f"Showing last {min(len(self.execution_logs), self.display_logs)} of {len(self.execution_logs)} logs" if self.execution_logs else None,
            subtitle_align="right"
        )

    def _render_continuation_header(self, msg) -> Text:
        """One-row header shown when the visible window's first line is not
        a message's first line — carries that message's role marker, model
        label (assistant), and its own timestamp, plus a "(continued)" tag.

        Forced to exactly one rendered row (`no_wrap`, ellipsis overflow)
        regardless of terminal width, since it occupies one row of the
        content budget rather than an extra one (see `_window_bounds`).
        """
        role = msg.role if isinstance(msg, Message) else msg["role"]
        msg_time = msg.timestamp if isinstance(msg, Message) else msg["time"]
        msg_name = msg.name if isinstance(msg, Message) else msg.get("name", "")

        t = Text(no_wrap=True, overflow="ellipsis")
        if role == "user":
            t.append("┌─ 👤 You ", style=self.theme.styles.get("user", "bold cyan"))
            t.append(f"[{msg_time}]", style=self.theme.styles.get("timestamp", "cyan"))
        elif role == "tool_call":
            t.append(f"┌─ ⚙️  {msg_name} ", style="bold dark_orange3")
            t.append(f"[{msg_time}]", style=self.theme.styles.get("timestamp", "cyan"))
        elif role == "tool_result":
            t.append(f"┌─ ↩  {msg_name} ", style="bold green")
            t.append(f"[{msg_time}]", style=self.theme.styles.get("timestamp", "cyan"))
        else:
            label = f"┌─ 🤖 AI ({self.model_name}) " if self.model_name else "┌─ 🤖 AI "
            t.append(label, style=self.theme.styles.get("assistant", "bold magenta"))
            t.append(f"[{msg_time}]", style=self.theme.styles.get("timestamp", "dim magenta"))
        t.append(" (continued)", style="dim")
        return t

    def _footer_subtitle(self, start: int, end: int, total: int, header_shown: bool) -> tuple:
        """Footer wording for `render_messages()`'s footer `Rule`.

        1-indexed inclusive line numbers, so the range names what a person
        counting rows on screen would actually see.
        """
        if self.data_path:
            return f"📁 {self.data_path}", "left"

        msg_count = len(self.messages)
        if total == 0:
            start_1, end_1 = 0, 0
        else:
            start_1, end_1 = start + 1, end

        subtitle = f"Lines {start_1}-{end_1} of {total} · {msg_count} msgs"
        if header_shown:
            subtitle += " · continued"
        if total - end > 0:
            subtitle += " · scrolled, End for latest"
        return subtitle, "right"

    def render_messages(self) -> Group:
        """
        Render all messages with error handling.

        Returns:
            Group: A Rich Group containing the rendered messages between horizontal rules
        """
        try:
            if not self.messages:
                return self._render_welcome_panel()

            start, end, header_idx = self._window_bounds()
            total = len(self._line_buffer)
            visible_lines = self._line_buffer[start:end]
            body = Segments(chain.from_iterable(visible_lines))

            subtitle, subtitle_align = self._footer_subtitle(
                start, end, total, header_idx is not None,
            )

            group_items = [
                Rule("[bold white]💬 Chat History[/]", characters="━", style="blue"),
                Text(""),
            ]
            if header_idx is not None:
                msgs = list(self.messages)
                group_items.append(self._render_continuation_header(msgs[header_idx]))
            group_items.append(body)
            group_items.append(
                Rule(subtitle, characters="━", style="blue", align=subtitle_align)
            )
            return Group(*group_items)

        except Exception as e:
            # Fallback rendering on error - ensures UI doesn't crash
            return Group(
                Rule("[bold red]⚠️  Rendering Error[/]", characters="━", style="red"),
                Text(f"Error rendering messages: {e}", style="error"),
            )

    def _render_welcome_panel(self) -> Group:
        """Render welcome message when no messages exist."""
        welcome = Text()
        welcome.append("🤖 ", style="bold")
        welcome.append("Welcome! ", style=self.theme.styles.get("assistant", "bold magenta"))
        welcome.append("Type a message below to start chatting.\n\n", style=self.theme.styles.get("assistant", "bold magenta"))
        welcome.append("Commands: ", style=self.theme.styles.get("user", "bold cyan"))
        welcome.append("'Ctl+c' to close", style=self.theme.styles.get("user", "bold cyan"))

        header = Rule("[bold white]💬 Chat History[/]", characters="━", style="blue")
        footer = Rule(characters="━", style="blue")
        return Group(header, Text(""), Align.center(welcome, vertical="middle"), Text(""), footer)

    def _render_tool_call_message(self, content: Text, name: str, args_str: str, msg_time: str) -> None:
        content.append(f"┌─ ⚙️  {name} ", style="bold dark_orange3")
        content.append(f"[{msg_time}]", style=self.theme.styles.get("timestamp", "cyan"))
        if self._context_pct > 0:
            content.append(f"  {self._fmt_ctx_label()}", style="bright_cyan")
        content.append("\n", style="default")
        content.append(f"{args_str}\n", style="bright_white")
        content.append("└" + "─" * 40 + "\n", style="dark_orange3")

    def _render_tool_result_message(self, content: Text, name: str, result_str: str, msg_time: str) -> None:
        content.append(f"┌─ ↩  {name} ", style="bold green")
        content.append(f"[{msg_time}]\n", style=self.theme.styles.get("timestamp", "cyan"))
        content.append(f"{result_str}\n", style="bright_white")
        content.append("└" + "─" * 40 + "\n", style="green")

    def _render_user_message(self, content: Text, msg_content: str, msg_time: str) -> None:
        """
        Render a user message to the content Text object.

        Args:
            content: The Rich Text object to append to
            msg_content: The message content
            msg_time: The message timestamp
        """
        content.append(f"┌─ 👤 You ", style=self.theme.styles.get("user", "bold cyan"))
        content.append(f"[{msg_time}]\n", style=self.theme.styles.get("timestamp", "cyan"))
        content.append(f"{msg_content}\n", style="white")
        content.append("└" + "─" * 40 + "\n", style=self.theme.styles.get("user", "cyan"))

    @staticmethod
    def _strip_markdown_links(text: str) -> str:
        """Replace markdown links with just their display text.

        Handles regular URLs and data-URIs so the terminal doesn't show
        huge base64 blobs.  ``[label](url)`` → ``label``
        """
        return re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    def _render_assistant_message(self, content: Text, msg_content: str, msg_time: str, msg_elapsed: str) -> None:
        """
        Render an assistant message to the content Text object.

        Args:
            content: The Rich Text object to append to
            msg_content: The message content
            msg_time: The message timestamp
            msg_elapsed: The elapsed time string (optional)
        """
        _ai_label = f"┌─ 🤖 AI ({self.model_name}) " if self.model_name else "┌─ 🤖 AI "
        content.append(_ai_label, style=self.theme.styles.get("assistant", "bold magenta"))
        content.append(f"[{msg_time}]", style=self.theme.styles.get("timestamp", "dim magenta"))
        if msg_elapsed:
            content.append(f" - {msg_elapsed}", style=self.theme.styles.get("warning", "dim magenta"))
        content.append("\n", style=self.theme.styles.get("warning", "dim magenta"))
        msg_content = self._strip_markdown_links(msg_content)
        content.append(f"{msg_content}\n", style="bright_white")
        content.append("└" + "─" * 40 + "\n", style=self.theme.styles.get("assistant", "magenta"))

    def render_thinking_panel(self) -> Panel:
        """Render thinking indicator in input panel"""
        thinking = Text()
        thinking.append("🤖 ", style="bold")
        thinking.append("Assistant is thinking", style="bright_white")
        thinking.append("...", style="bright_white blink")

        return Panel(
            thinking,
            title="[bold white]⌨️  Please Wait[/]",
            title_align="left",
            border_style="magenta",
            box=HEAVY,
            padding=(0, 2),
            height=3
        )

    def render(self, thinking: bool = False):
        """
        Render complete layout including chat messages and optionally execution logs.

        Args:
            thinking: If True, could show thinking indicator (currently unused)

        Returns:
            Panel: The rendered chat UI panel (logs panel is printed separately if enabled)
        """
        messages_panel = self.render_messages()

        if self.show_logs:
            # Print logs panel first, then messages panel
            self.console.print(self.render_logs_panel())

        return messages_panel

    def initialize(self):
        """Initialize the chat UI"""
        self.console.clear()

        # Show header
        header = Text()
        header.append("╔" + "═" * 50 + "╗\n", style=self.theme.styles.get("prompt", "bold blue"))
        header.append("║", style=self.theme.styles.get("prompt", "bold blue"))
        title_text = f"🌟 {self.banner_title} 🌟"
        padding = max(0, 50 - len(title_text))
        left_pad = padding // 2 - 2
        right_pad = padding - left_pad - 2
        header.append(" " * left_pad + title_text + " " * right_pad, style=self.theme.styles.get("prompt", "bold white on blue"))
        header.append("║\n", style=self.theme.styles.get("prompt", "bold blue"))
        header.append("╚" + "═" * 50 + "╝", style=self.theme.styles.get("prompt", "bold blue"))
        self.console.print(Align.center(header))
        self.console.print()
        self.console.print(Align.center(Text(self.disclaimer, style="bright_white")))

    def _print_frame(self) -> None:
        """Clear and redraw the standard frame: panel, disclaimer, blank line.

        The single place that draws the panel outside of a repaint mid-input,
        so the scrolled view and the normal view can never drift apart.
        """
        self.console.clear()
        self.console.print(self.render())
        self.console.print(Align.center(Text(self.disclaimer, style="bright_white")))
        self.console.print()
