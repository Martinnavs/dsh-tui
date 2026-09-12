"""Shared test builders for the line-based scrollback suites (Task 01/02).

Both `test_scroll.py` and `test_scroll_pty.py` depend on this module, so
ticket 01 (which lands the line-based scrollback itself) owns it rather
than each test file redefining its own near-duplicate `_make_ui`/`_fill`.
"""
import re
from io import StringIO

from rich.console import Console

from dsh_tui.chat_ui import ChatUI, Message

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def make_ui(
    height: int = 1000,
    width: int = 100,
    show_logs: bool = False,
    max_messages: int = 100,
) -> ChatUI:
    """A `ChatUI` with output redirected away from the real terminal.

    `height` defaults tall enough that the line-based windowing gate is
    non-binding; pass an explicit smaller height to exercise the gate.
    """
    ui = ChatUI(show_logs=show_logs, max_messages=max_messages)
    ui.console = Console(file=StringIO(), width=width, height=height)
    return ui


def fill(ui: ChatUI, n: int, role: str = "assistant") -> list:
    """Append `n` bare messages directly, bypassing `add_message`'s scroll
    reset. Returns the appended `Message` objects for identity-based
    assertions.
    """
    msgs = [Message(role=role, content=f"m{i}", timestamp="t") for i in range(n)]
    ui.messages.extend(msgs)
    return msgs


def tall_message(line_count: int, role: str = "assistant", tag: str = "tall") -> Message:
    """A single message that renders to (at least) `line_count` lines —
    for exercising a message taller than `_content_budget()`.
    """
    content = "\n".join(f"line{i}-{tag}" for i in range(line_count))
    return Message(role=role, content=content, timestamp="t")


def mixed_height_messages(n: int) -> list:
    """Heterogeneous per-message heights: one-liner, 400-char single line,
    14-bullet list, and 120-word body, cycled. Uniform-height messages
    would not expose tiling/reachability bugs — every window would tile
    correctly by coincidence.
    """
    bodies = [
        lambda i: f"short message {i}",
        lambda i: "x" * 400 + f"-{i}",
        lambda i: "\n".join(f"- bullet {j}" for j in range(14)) + f" ({i})",
        lambda i: " ".join(f"word{j}" for j in range(120)) + f" ({i})",
    ]
    return [
        Message(role="assistant", content=bodies[i % len(bodies)](i), timestamp="t")
        for i in range(n)
    ]


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def render_text(ui: ChatUI) -> str:
    """Render `ui.render_messages()` to plain text with ANSI stripped.

    Rich's highlighter styles numbers inside `Rule` titles/subtitles, so a
    plain substring assertion against the raw ANSI capture can fail even
    when the wording is correct — see ticket 01's Established section.
    """
    with ui.console.capture() as capture:
        ui.console.print(ui.render_messages())
    return strip_ansi(capture.get())


def walk_line_ids(ui: ChatUI, step) -> set:
    """Repeatedly call `step` (`ui.scroll_down` or `ui.scroll_up`),
    collecting the identity (`id()`) of every line object visible at every
    offset along the way, including the starting offset.

    Line identity (not equality) matters: line objects are lists of
    `Segment`s that can compare equal across different messages, the way
    `Message` (a dataclass) already can.
    """
    start, end, _ = ui._window_bounds()
    seen = {id(line) for line in ui._line_buffer[start:end]}
    while step():
        start, end, _ = ui._window_bounds()
        seen.update(id(line) for line in ui._line_buffer[start:end])
    return seen
