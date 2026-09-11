"""Painting the chat frame: the messages panel, per-role message blocks,
the logs panel, and the startup banner. Combined with the other ChatUI
mixins in core.py.

Relies on ``ScrollbackMixin`` for ``_visible_messages``/``_max_scroll_offset``.
"""
import re

from rich.align import Align
from rich.box import ROUNDED, HEAVY
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text


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

    def render_messages(self) -> Group:
        """
        Render all messages with error handling.

        Returns:
            Group: A Rich Group containing the rendered messages between horizontal rules
        """
        try:
            if not self.messages:
                return self._render_welcome_panel()

            content = Text()
            display_msgs = self._visible_messages()

            for msg in display_msgs:
                self._render_message_into(content, msg)

            if self.data_path:
                subtitle = f"📁 {self.data_path}"
                subtitle_align = "left"
            else:
                total = len(self.messages)
                offset = min(self._scroll_offset, self._max_scroll_offset())
                if offset == 0:
                    subtitle = f"Showing last {len(display_msgs)} of {total} messages"
                else:
                    end = total - offset
                    start = end - len(display_msgs) + 1
                    subtitle = (
                        f"Showing {start}-{end} of {total} messages "
                        f"— scrolled, End to jump to latest"
                    )
                subtitle_align = "right"

            header = Rule("[bold white]💬 Chat History[/]", characters="━", style="blue")
            footer = Rule(subtitle, characters="━", style="blue", align=subtitle_align)
            return Group(header, Text(""), content, footer)

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
