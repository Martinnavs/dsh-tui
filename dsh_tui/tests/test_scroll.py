"""Unit tests for ChatUI's vim-like scrollback (Task 01).

Pure, terminal-free coverage of the scroll offset API, the scroll-aware
render slice, the footer text, and the snap-to-bottom behavior on new
content. No pty/tty involved: the raw-byte key dispatch in
`_input_with_history_unix` is exercised only through the thin, testable
methods it calls (`scroll_up`/`scroll_down`/`scroll_to_top`/`scroll_to_bottom`),
never through simulated terminal input.
"""
from io import StringIO

from rich.console import Console

from dsh_tui.chat_ui import ChatUI, Message


def _make_ui(display_messages: int = 5, show_logs: bool = True) -> ChatUI:
    ui = ChatUI(display_messages=display_messages, show_logs=show_logs)
    # Redirect Rich output away from the real terminal for render assertions.
    ui.console = Console(file=StringIO(), width=100)
    return ui


def _fill(ui: ChatUI, n: int, role: str = "assistant") -> None:
    """Append n bare messages directly, bypassing add_message's scroll reset."""
    for i in range(n):
        ui.messages.append(Message(role=role, content=f"m{i}", timestamp="t"))


def _render_text(ui: ChatUI) -> str:
    with ui.console.capture() as capture:
        ui.console.print(ui.render_messages())
    return capture.get()


# ── _max_scroll_offset ──────────────────────────────────────────────

def test_max_scroll_offset_no_messages():
    ui = _make_ui(display_messages=5)
    assert ui._max_scroll_offset() == 0


def test_max_scroll_offset_fewer_than_display():
    ui = _make_ui(display_messages=5)
    _fill(ui, 3)
    assert ui._max_scroll_offset() == 0


def test_max_scroll_offset_more_than_display():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    assert ui._max_scroll_offset() == 7


# ── scroll_up / scroll_down / scroll_to_top / scroll_to_bottom clamping ──

def test_scroll_up_clamps_at_top_and_reports_change():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)  # max offset = 7

    assert ui.scroll_up() is True
    assert ui._scroll_offset == 5
    assert ui.scroll_up() is True
    assert ui._scroll_offset == 7  # clamped, not 10
    assert ui.scroll_up() is False  # already at top: no-op
    assert ui._scroll_offset == 7


def test_scroll_down_clamps_at_bottom_and_reports_change():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    ui.scroll_to_top()  # offset = 7

    assert ui.scroll_down() is True
    assert ui._scroll_offset == 2
    assert ui.scroll_down() is True
    assert ui._scroll_offset == 0  # clamped, not negative
    assert ui.scroll_down() is False  # already at bottom: no-op
    assert ui._scroll_offset == 0


def test_scroll_to_top_and_bottom_report_change_only_once():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)

    assert ui.scroll_to_top() is True
    assert ui._scroll_offset == 7
    assert ui.scroll_to_top() is False  # already at top

    assert ui.scroll_to_bottom() is True
    assert ui._scroll_offset == 0
    assert ui.scroll_to_bottom() is False  # already at bottom


def test_scroll_with_no_messages_is_a_noop():
    ui = _make_ui(display_messages=5)

    assert ui._max_scroll_offset() == 0
    assert ui.scroll_up() is False
    assert ui.scroll_down() is False
    assert ui.scroll_to_top() is False
    assert ui.scroll_to_bottom() is False
    assert ui._scroll_offset == 0
    assert ui._visible_messages() == []


def test_scroll_up_down_with_multiple_pages():
    ui = _make_ui(display_messages=5)
    _fill(ui, 22)  # max offset = 17

    assert ui.scroll_up(pages=2) is True
    assert ui._scroll_offset == 10
    assert ui.scroll_up(pages=2) is True
    assert ui._scroll_offset == 17  # clamped, not 20
    assert ui.scroll_down(pages=2) is True
    assert ui._scroll_offset == 7
    assert ui.scroll_down(pages=10) is True
    assert ui._scroll_offset == 0  # clamped, not negative


def test_scroll_when_display_messages_exceeds_message_count():
    ui = _make_ui(display_messages=50)
    _fill(ui, 3)

    assert ui._max_scroll_offset() == 0
    assert ui.scroll_up() is False
    assert ui.scroll_to_top() is False
    assert ui._scroll_offset == 0


# ── _visible_messages slice correctness ──────────────────────────────

def test_visible_messages_at_offset_zero_matches_todays_slice():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)

    visible = ui._visible_messages()
    expected = list(ui.messages)[-ui.display_messages:]
    assert visible == expected
    assert [m.content for m in visible] == ["m7", "m8", "m9", "m10", "m11"]


def test_visible_messages_at_mid_offset():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    ui.scroll_up()  # offset = 5

    visible = ui._visible_messages()
    assert [m.content for m in visible] == ["m2", "m3", "m4", "m5", "m6"]


def test_visible_messages_at_max_offset():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    ui.scroll_to_top()  # offset = 7 (max)

    visible = ui._visible_messages()
    assert [m.content for m in visible] == ["m0", "m1", "m2", "m3", "m4"]


def test_visible_messages_display_larger_than_message_count():
    ui = _make_ui(display_messages=50)
    _fill(ui, 3)

    visible = ui._visible_messages()
    assert [m.content for m in visible] == ["m0", "m1", "m2"]


# ── Snap-to-bottom on new content ────────────────────────────────────

def test_add_message_resets_scroll_offset():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.add_message("user", "hi")
    assert ui._scroll_offset == 0


def test_add_tool_call_resets_scroll_offset():
    ui = _make_ui(display_messages=5, show_logs=True)
    _fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.add_tool_call("bash", {"command": "echo hi"})
    assert ui._scroll_offset == 0


def test_add_tool_result_resets_scroll_offset():
    ui = _make_ui(display_messages=5, show_logs=True)
    _fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.add_tool_result("bash", "hi")
    assert ui._scroll_offset == 0


def test_clear_messages_resets_scroll_offset():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.clear_messages()
    assert ui._scroll_offset == 0


# ── Footer text ───────────────────────────────────────────────────

def test_footer_at_bottom():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)

    text = _render_text(ui)
    assert "Showing last 5 of 12 messages" in text


def test_footer_when_scrolled():
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    ui.scroll_up()  # offset = 5 -> visible m2..m6, 1-indexed 3-7

    text = _render_text(ui)
    assert "Showing 3-7 of 12 messages" in text
    assert "scrolled" in text
    assert "End" in text  # names the key that returns to latest


def test_footer_with_no_overflow():
    ui = _make_ui(display_messages=5)
    _fill(ui, 3)

    text = _render_text(ui)
    assert "Showing last 3 of 3 messages" in text
    assert "scrolled" not in text
