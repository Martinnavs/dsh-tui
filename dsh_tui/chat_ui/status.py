"""Run-status text: context-window usage, one-line notices/turn-limit
warnings, and the fact-check (verification) display. Combined with the
other ChatUI mixins in core.py.
"""
import re
from typing import Literal

from rich.text import Text

from .message import Message


class StatusMixin:
    # ── Context usage display ──────────────────────────────────────

    def _fmt_ctx_label(self) -> str:
        """Return a compact ctx label like 'ctx:42%/128k'."""
        label = f"ctx: {self._context_pct:.0f}%"
        if self._context_max_tokens:
            k = f"{self._context_max_tokens // 1000:,}"
            label += f", max toks: {k}k"
        return label

    def set_context_usage(self, pct: float, max_tokens: int = 0) -> None:
        """Update the context window usage percentage (0–100) and max token count."""
        self._context_pct = pct
        if max_tokens:
            self._context_max_tokens = max_tokens

    def show_context_compaction(self, orig_msg_count: int, summary_chars: int) -> None:
        """Print an inline notification when the context window is compacted."""
        self.console.print(
            f"  ⚡ Context compacted  ({orig_msg_count} messages → {summary_chars:,} char summary)",
            style="bold yellow",
        )

    def notice(self, message: str,
               level: Literal["info", "warning", "error", "debug"] = "info") -> None:
        """Print a run event the user has to see to read the answer correctly.

        The log panel these events also go to is off by default, so a reply cut
        off mid-sentence, or one stitched together from two generations, would
        otherwise arrive on screen with nothing to explain it.
        """
        mark, style = {
            "warning": ("⚠", "bold yellow"),
            "error": ("✖", "bold red"),
        }.get(level, ("ℹ", "dim"))
        self.console.print(f"  {mark}  {message}", style=style)

    def show_turn_limit(self, limit: int) -> None:
        """Print an inline warning when the chat loop stops at its turn ceiling.

        Printed rather than logged: the run is about to end mid-task, and the
        block that just streamed is a step, not an answer.  Without this the
        stop is indistinguishable on screen from a task that finished.
        """
        self.console.print(
            f"  ⚠  Stopped at the {limit}-step turn limit — the task did not finish",
            style="bold red",
        )
        self.console.print(
            "     Raise serving.max_chat_iterations, or break the task into smaller pieces.",
            style="dim",
        )

    # ── Fact-check display ─────────────────────────────────────────

    def verification_start(self) -> None:
        """The answer has streamed and is now being checked against its sources."""
        self.console.print("  🔎 Checking the facts…", style="bright_cyan")

    def verification_end(self, answer: str, note: str) -> None:
        """Report the outcome of the check, and correct the answer on screen.

        A terminal cannot unprint the draft, so the correction is printed
        under it and the stored message is swapped for the revised text —
        which keeps the chat panel, the session file, and the block the user
        scrolls back to from disagreeing about what the answer was.
        """
        if not note:
            self.console.print("  ✓ Facts check out", style="green")
            return
        self.console.print(f"  ↻ {note}", style="bold yellow")
        # Normalized the way stream_end normalizes what it stores, so the
        # replacement is byte-identical to what the caller will compare the
        # returned answer against and the message is not appended twice.
        content = re.sub(r"</?(?:answer|think|stop)>", "", answer,
                         flags=re.IGNORECASE).strip()
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].role == "assistant":
                self.messages[i] = Message(
                    role="assistant", content=content,
                    timestamp=self.messages[i].timestamp,
                    elapsed=self.messages[i].elapsed,
                    name=self.messages[i].name,
                )
                break
        self._render_revised_answer(content)

    def verification_correction(self, answer: str, note: str) -> None:
        """A correction that arrived after the answer was already final.

        Not printed when it lands.  The user is at the prompt by then, quite
        possibly mid-word, and this loop reads keystrokes in raw mode — writing
        under the cursor would scramble the line they are typing.  So the
        corrected text replaces the stored message and the note is queued, and
        both appear at the top of the next turn, when ``get_user_input`` clears
        and redraws the panel from ``self.messages`` anyway.

        If the user never asks anything else, they never see it.  That is the
        right trade: they were done with the answer, and a terminal that
        interrupts a finished conversation to revise it is worse than one that
        stays quiet.
        """
        if not note:
            return
        content = re.sub(r"</?(?:answer|think|stop)>", "", answer,
                         flags=re.IGNORECASE).strip()
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].role == "assistant":
                self.messages[i] = Message(
                    role="assistant", content=content,
                    timestamp=self.messages[i].timestamp,
                    elapsed=self.messages[i].elapsed,
                    name=self.messages[i].name,
                )
                break
        self.pending_corrections.append(note)

    def flush_corrections(self) -> None:
        """Show what the background check found, at the top of the next turn."""
        for note in self.pending_corrections:
            self.console.print(f"  ↻ Correction to the previous answer: {note}",
                               style="bold yellow")
        self.pending_corrections.clear()

    def _render_revised_answer(self, answer: str) -> None:
        """Print the corrected answer in the same block style as a streamed one."""
        ts = self.format_timestamp()
        t = Text()
        style = self.theme.styles.get("assistant", "magenta")
        t.append(f"┌─ {self.agent_cursor} (revised) ", style=f"bold {style}")
        t.append(f"[{ts}]", style=self.theme.styles.get("timestamp", "cyan"))
        self.console.print(t)
        self.console.print(self._strip_markdown_links(answer))
        self.console.print("└" + "─" * 40, style=style)
