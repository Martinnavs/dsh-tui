"""Unit tests for ChatUI's line-based vim-like scrollback (Task 01).

Pure, terminal-free coverage of the line buffer, the scroll-offset state
machine, the scroll-aware render slice, the continuation header, and the
footer text. No pty/tty involved: the raw-byte key dispatch in
`_input_with_history_unix` is exercised only through the thin, testable
methods it calls (`scroll_up`/`scroll_down`/`scroll_to_top`/`scroll_to_bottom`),
never through simulated terminal input — that side is covered end to end
by `test_scroll_pty.py`.

Message-granular tests from the fixed-count/height-gate era (pre this
ticket) are gone: the visible window is now defined in rendered lines,
not messages, so "N of display_messages messages fit" no longer describes
anything the code does.
"""
from io import StringIO

from rich.align import Align
from rich.console import Console
from rich.text import Text

from dsh_tui.chat_ui import ChatUI, Message
from dsh_tui.tests.scroll_helpers import (
    fill,
    make_ui,
    mixed_height_messages,
    render_text,
    strip_ansi,
    tall_message,
    walk_line_ids,
)


# ── _max_scroll_offset / offsets are line counts (AC 2) ──────────────────

def test_max_scroll_offset_no_messages():
    ui = make_ui()
    assert ui._max_scroll_offset() == 0


def test_max_scroll_offset_zero_when_everything_fits():
    ui = make_ui(height=1000)
    fill(ui, 5)
    assert ui._max_scroll_offset() == 0


def test_max_scroll_offset_equals_total_lines_minus_budget():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)
    ui._ensure_line_buffer()
    total = len(ui._line_buffer)
    budget = ui._content_budget()
    assert ui._max_scroll_offset() == max(0, total - budget)


def test_top_of_scroll_range_shows_buffer_line_zero():
    """AC 2: at max offset, the top visible line is buffer line 0."""
    ui = make_ui(height=24, width=80)
    fill(ui, 12)
    ui.scroll_to_top()
    start, end, _ = ui._window_bounds()
    assert start == 0


def test_scroll_amounts_are_lines_not_messages():
    """A single message spanning many lines is reachable in more than one
    scroll step — a message-granular offset could never do this.
    """
    ui = make_ui(height=10, width=60)
    ui.messages.append(tall_message(60))
    assert ui._max_scroll_offset() > 1
    steps = 0
    while ui.scroll_up():
        steps += 1
        if steps > 200:
            break
    assert steps > 1


# ── scroll_up / scroll_down / scroll_to_top / scroll_to_bottom clamping ──

def test_scroll_up_clamps_at_top_and_reports_change():
    ui = make_ui(height=24, width=80)
    fill(ui, 30)
    max_offset = ui._max_scroll_offset()
    assert max_offset > 0

    assert ui.scroll_up() is True
    assert 0 < ui._scroll_offset <= max_offset
    while ui.scroll_up():
        pass
    assert ui._scroll_offset == max_offset
    assert ui.scroll_up() is False  # already at top: no-op


def test_scroll_down_clamps_at_bottom_and_reports_change():
    ui = make_ui(height=24, width=80)
    fill(ui, 30)
    ui.scroll_to_top()

    while ui.scroll_down():
        pass
    assert ui._scroll_offset == 0
    assert ui.scroll_down() is False  # already at bottom: no-op


def test_scroll_to_top_and_bottom_report_change_only_once():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)

    assert ui.scroll_to_top() is True
    assert ui._scroll_offset == ui._max_scroll_offset()
    assert ui.scroll_to_top() is False  # already at top

    assert ui.scroll_to_bottom() is True
    assert ui._scroll_offset == 0
    assert ui.scroll_to_bottom() is False  # already at bottom


def test_scroll_with_no_messages_is_a_noop():
    ui = make_ui()

    assert ui._max_scroll_offset() == 0
    assert ui.scroll_up() is False
    assert ui.scroll_down() is False
    assert ui.scroll_to_top() is False
    assert ui.scroll_to_bottom() is False
    assert ui._scroll_offset == 0
    assert ui._visible_messages() == []


def test_scroll_when_everything_fits_is_a_noop():
    ui = make_ui(height=1000)
    fill(ui, 3)

    assert ui._max_scroll_offset() == 0
    assert ui.scroll_up() is False
    assert ui.scroll_to_top() is False
    assert ui._scroll_offset == 0


# ── _visible_messages slice correctness ──────────────────────────────

def test_visible_messages_at_offset_zero_includes_newest():
    ui = make_ui(height=1000)
    msgs = fill(ui, 12)

    visible = ui._visible_messages()
    assert visible[-1] is msgs[-1]
    assert visible == list(ui.messages)


def test_visible_messages_at_max_offset_starts_at_oldest():
    ui = make_ui(height=24, width=80)
    msgs = fill(ui, 12)
    ui.scroll_to_top()

    visible = ui._visible_messages()
    assert visible[0] is msgs[0]


def test_visible_messages_never_empty_at_degenerate_budget():
    """A console too short for even one full row of chrome must still show
    exactly one line's worth of content, not an empty window.
    """
    ui = make_ui(height=1, width=80)
    fill(ui, 5)

    start, end, header = ui._window_bounds()
    assert end - start == 1
    assert header is None  # budget <= 1: never show a header
    assert len(ui._visible_messages()) >= 1


def test_visible_messages_never_empty_with_oversized_single_message():
    ui = make_ui(height=10, width=80)
    ui.messages.append(tall_message(100))

    visible = ui._visible_messages()
    assert len(visible) == 1
    assert ui._max_scroll_offset() > 0


# ── Snap-to-bottom on new content ────────────────────────────────────

def test_add_message_resets_scroll_offset():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.add_message("user", "hi")
    assert ui._scroll_offset == 0


def test_add_tool_call_resets_scroll_offset():
    ui = make_ui(height=24, width=80, show_logs=True)
    fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.add_tool_call("bash", {"command": "echo hi"})
    assert ui._scroll_offset == 0


def test_add_tool_result_resets_scroll_offset():
    ui = make_ui(height=24, width=80, show_logs=True)
    fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.add_tool_result("bash", "hi")
    assert ui._scroll_offset == 0


def test_clear_messages_resets_scroll_offset():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)
    ui.scroll_to_top()
    assert ui._scroll_offset != 0

    ui.clear_messages()
    assert ui._scroll_offset == 0


# ── show_logs=False zero-line tool_call/tool_result ──────────────────

def test_show_logs_false_tool_call_contributes_zero_lines():
    ui = make_ui(height=1000, show_logs=False)
    ui.messages.append(Message(role="tool_call", content="{}", timestamp="t", name="bash"))
    ui.messages.append(Message(role="assistant", content="hi", timestamp="t"))

    ui._ensure_line_buffer()
    assert len(ui._line_buffer) == 3  # only the assistant message's 3 lines
    assert ui._visible_messages() == [ui.messages[-1]]


def test_fit_loop_terminates_with_all_zero_line_messages():
    """A run of `tool_call`/`tool_result` messages with `show_logs=False`
    renders as zero rows each — the line buffer must end up empty rather
    than hanging or raising.
    """
    ui = make_ui(height=24, width=80, show_logs=False)
    for i in range(20):
        role = "tool_call" if i % 2 == 0 else "tool_result"
        ui.messages.append(Message(role=role, content="{}", timestamp="t", name="bash"))

    ui._ensure_line_buffer()
    assert ui._line_buffer == []
    assert ui._max_scroll_offset() == 0
    assert ui._visible_messages() == []


# ── Continuation header (AC 3) ────────────────────────────────────────

def _header_row_count(ui, msg, width):
    ui.console = Console(file=StringIO(), width=width, height=1000)
    header = ui._render_continuation_header(msg)
    return len(ui.console.render_lines(header, pad=False))


def test_continuation_header_is_exactly_one_row():
    ui = make_ui()
    cases = [
        Message(role="user", content="x", timestamp="11:04 AM 12 Sep"),
        Message(role="tool_call", content="{}", timestamp="11:04 AM 12 Sep", name="bash"),
        Message(role="tool_result", content="ok", timestamp="11:04 AM 12 Sep", name="bash"),
        Message(role="assistant", content="x", timestamp="11:04 AM 12 Sep"),
    ]
    for width in (60, 80):
        for msg in cases:
            assert _header_row_count(ui, msg, width) == 1, (msg.role, width)

        ui.model_name = "gpt-5"
        assert _header_row_count(
            ui, Message(role="assistant", content="x", timestamp="t"), width
        ) == 1
        ui.model_name = ""


def test_continuation_header_shown_when_window_starts_mid_message():
    ui = make_ui(height=10, width=60)
    tm = tall_message(60)
    ui.messages.append(tm)
    ui.scroll_up()

    start, end, header_idx = ui._window_bounds()
    assert not ui._is_first_line(start)
    assert header_idx == 0
    text = render_text(ui)
    assert "(continued)" in strip_ansi(text)


def test_continuation_header_absent_when_window_starts_at_message_boundary():
    ui = make_ui(height=1000)
    fill(ui, 12)

    start, end, header_idx = ui._window_bounds()
    assert start == 0
    assert header_idx is None
    assert "(continued)" not in render_text(ui)


def test_continuation_header_occupies_one_row_of_the_budget_not_an_extra_one():
    """AC 3: the header eats into the budget rather than growing the frame."""
    ui = make_ui(height=10, width=60)
    ui.messages.append(tall_message(60))
    ui.scroll_up()

    start, end, header_idx = ui._window_bounds()
    budget = ui._content_budget()
    content_rows = end - start
    header_rows = 1 if header_idx is not None else 0
    assert content_rows + header_rows == budget


def test_continuation_header_dropped_when_shift_lands_on_boundary():
    """If shifting `start` forward by one row to make room for a header
    lands exactly on a message's first line, the header is dropped rather
    than re-derived (single-step rule, Action item 3).
    """
    ui = make_ui(height=1000)
    # Two messages, each exactly 5 lines (header+content+footer already
    # included by _render_user_message), budget crafted so the naive
    # tentative_start lands one row before a message boundary.
    msgs = [Message(role="user", content="one line", timestamp="t") for _ in range(3)]
    ui.messages.extend(msgs)
    ui._ensure_line_buffer()
    per_msg = len(ui._line_buffer) // 3
    # Force a budget that makes tentative_start land exactly one row
    # before the second message's first line.
    ui.console = Console(width=80, height=1000)
    target_end = len(ui._line_buffer)
    boundary = per_msg  # first line of the 2nd message
    budget = target_end - (boundary - 1)
    ui._content_budget = lambda: budget
    start, end, header_idx = ui._window_bounds()
    assert start == boundary
    assert header_idx is None


# ── Footer text (AC 4) ────────────────────────────────────────────────

def test_footer_no_scroll_room():
    ui = make_ui(height=1000)
    fill(ui, 5)

    text = strip_ansi(render_text(ui))
    total = len(ui._line_buffer)
    assert f"Lines 1-{total} of {total} · 5 msgs" in text
    assert "scrolled" not in text
    assert "continued" not in text


def test_footer_at_bottom_with_scroll_room():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)
    assert ui._max_scroll_offset() > 0

    start, end, header_idx = ui._window_bounds()
    total = len(ui._line_buffer)
    text = strip_ansi(render_text(ui))
    assert f"Lines {start + 1}-{end} of {total} · 12 msgs" in text
    assert "scrolled" not in text


def test_footer_when_scrolled():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)
    ui.scroll_up()
    assert ui._scroll_offset > 0

    start, end, header_idx = ui._window_bounds()
    total = len(ui._line_buffer)
    text = strip_ansi(render_text(ui))
    assert f"Lines {start + 1}-{end} of {total} · 12 msgs" in text
    assert "scrolled" in text
    assert "End" in text  # names the key that returns to latest
    if header_idx is not None:
        assert "continued" in text


def test_footer_continued_marker_matches_header_presence():
    ui = make_ui(height=10, width=60)
    ui.messages.append(tall_message(60))
    ui.scroll_up()

    start, end, header_idx = ui._window_bounds()
    text = strip_ansi(render_text(ui))
    assert header_idx is not None
    assert "· continued" in text


def test_footer_data_path_branch_unchanged():
    ui = make_ui(height=1000)
    fill(ui, 5)
    ui.data_path = "/tmp/run-1"

    text = strip_ansi(render_text(ui))
    assert "📁 /tmp/run-1" in text


def test_footer_fits_within_80_columns_worst_case():
    """AC 4: the worst realistic string fits an 80-column Rule."""
    ui = make_ui(height=1000)
    fill(ui, 999)
    subtitle, _ = ui._footer_subtitle(0, 99999, 99999, True)
    subtitle += " · scrolled, End for latest"
    assert len(subtitle) <= 80, subtitle


# ── AC 1: reachability of a message taller than the budget ────────────

def test_tall_message_fully_reachable_scrolling_up_from_bottom():
    ui = make_ui(height=10, width=60)
    ui.messages.append(tall_message(200))

    seen = walk_line_ids(ui, ui.scroll_up)
    all_ids = {id(line) for line in ui._line_buffer}
    assert seen == all_ids


def test_tall_message_fully_reachable_scrolling_down_from_top():
    ui = make_ui(height=10, width=60)
    ui.messages.append(tall_message(200))
    ui.scroll_to_top()

    seen = walk_line_ids(ui, ui.scroll_down)
    all_ids = {id(line) for line in ui._line_buffer}
    assert seen == all_ids


def test_mixed_height_messages_fully_reachable_scrolling_down_from_top():
    """Regression shape for review finding R2-1 (see the message-granular
    predecessor of this test): mixed per-message heights are what exposes
    a forward/backward tiling mismatch; uniform heights tile correctly by
    coincidence and would not catch a bug here.
    """
    ui = make_ui(height=30, width=80, show_logs=True)
    msgs = mixed_height_messages(25)
    ui.messages.extend(msgs)

    ui.scroll_to_top()
    seen = walk_line_ids(ui, ui.scroll_down)
    all_ids = {id(line) for line in ui._line_buffer}
    missing = all_ids - seen
    assert not missing


def test_mixed_height_messages_fully_reachable_scrolling_up_from_bottom():
    ui = make_ui(height=30, width=80, show_logs=True)
    msgs = mixed_height_messages(25)
    ui.messages.extend(msgs)

    ui.scroll_to_bottom()
    seen = walk_line_ids(ui, ui.scroll_up)
    all_ids = {id(line) for line in ui._line_buffer}
    missing = all_ids - seen
    assert not missing


# ── AC 7: frame fits the viewport exactly ──────────────────────────────

def _frame_total_rows(ui):
    msgs_group_rows = len(ui.console.render_lines(ui.render_messages()))
    disclaimer_rows = len(ui.console.render_lines(Align.center(Text(ui.disclaimer))))
    logs_rows = len(ui.console.render_lines(ui.render_logs_panel())) if ui.show_logs else 0
    return logs_rows + msgs_group_rows + disclaimer_rows + 1 + 1  # +blank +prompt row


def test_frame_never_exceeds_console_height_sweep():
    """Sweep of heights/widths/show_logs/content shapes (AC 7). The one
    combination excluded is where the frame's fixed chrome (headers, the
    disclaimer, the logs panel, the prompt row) alone already exceeds the
    console height before any message content is added at all —
    `_content_budget()`'s `max(1, ...)` floor (reused unchanged from
    before this ticket, see Non-Goals) cannot make that case fit, and it
    already failed by a much larger margin on the pre-ticket fixed-count
    windowing (measured: 209-214 rows into a 10-row console).
    """
    for height in (10, 20, 24, 30, 40):
        for width in (60, 80, 100):
            for show_logs in (False, True):
                ui = make_ui(height=height, width=width, show_logs=show_logs)
                for i in range(10):
                    ui.messages.append(
                        Message(role="assistant", content=f"short message {i}", timestamp="t")
                    )
                ui.messages.append(tall_message(200))
                if show_logs:
                    ui.add_log("something happened")

                # Chrome-only reserved rows, uninfluenced by the budget floor:
                disclaimer_rows = len(
                    ui.console.render_lines(Align.center(Text(ui.disclaimer, style="bright_white")))
                )
                logs_rows = (
                    len(ui.console.render_lines(ui.render_logs_panel())) if show_logs else 0
                )
                reserved = 3 + disclaimer_rows + 1 + 1 + logs_rows
                if reserved >= height:
                    continue  # degenerate pre-existing floor case, see docstring

                total = _frame_total_rows(ui)
                assert total <= height, (height, width, show_logs, total)


def test_frame_fits_when_every_message_individually_fits_budget():
    for show_logs in (False, True):
        ui = make_ui(height=24, width=80, show_logs=show_logs)
        for i in range(10):
            ui.messages.append(Message(role="assistant", content=f"short message {i}", timestamp="t"))
        if show_logs:
            ui.add_log("something happened")

        total = _frame_total_rows(ui)
        assert total <= ui.console.size.height, (show_logs, total, ui.console.size.height)


# ── AC 8: measurement failure never raises ─────────────────────────────

def test_measurement_failure_degrades_without_raising():
    ui = make_ui(height=24, width=80)
    fill(ui, 12)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    ui.console.render_lines = _boom

    assert ui._max_scroll_offset() == 0
    assert ui._visible_messages() == []
    assert ui.scroll_up() is False
    assert ui.scroll_down() is False
    assert ui.scroll_to_top() is False
    assert ui.scroll_to_bottom() is False
    # render_messages() must not raise either — it has its own except branch.
    ui.render_messages()


# ── AC 9: byte-identical rendering vs the concatenated-Text baseline ──

def test_rendering_byte_identical_to_concatenated_text_baseline():
    ui = make_ui(height=1000, width=60)
    msgs = mixed_height_messages(6)
    ui.messages.extend(msgs)

    baseline = Text()
    for m in ui.messages:
        ui._render_message_into(baseline, m)
    baseline_lines = ui.console.render_lines(baseline, pad=False, new_lines=True)[:-1]

    start, end, header_idx = ui._window_bounds()
    assert header_idx is None
    actual_lines = ui._line_buffer[start:end]

    assert len(actual_lines) == len(baseline_lines)
    assert actual_lines == baseline_lines


# ── AC 10: performance / caching ────────────────────────────────────────

def test_scroll_does_not_trigger_full_rebuild():
    """A warm scroll repaint must not re-render every message from scratch."""
    ui = make_ui(height=1000, width=100, max_messages=1000)
    ui.messages.extend(mixed_height_messages(1000))
    ui._ensure_line_buffer()  # cold build, seeds the cache

    render_calls = {"count": 0}
    original = ui._render_message_into

    def _counting(content, msg):
        render_calls["count"] += 1
        return original(content, msg)

    ui._render_message_into = _counting
    ui.scroll_up()
    ui._ensure_line_buffer()

    assert render_calls["count"] == 0


def test_set_context_usage_does_not_force_full_rebuild_without_tool_calls():
    ui = make_ui(height=1000, width=100, max_messages=1000)
    ui.messages.extend(mixed_height_messages(1000))
    ui._ensure_line_buffer()

    render_calls = {"count": 0}
    original = ui._render_message_into

    def _counting(content, msg):
        render_calls["count"] += 1
        return original(content, msg)

    ui._render_message_into = _counting
    ui.set_context_usage(42.0, 128_000)
    ui._ensure_line_buffer()

    assert render_calls["count"] == 0


def test_set_context_usage_invalidates_only_tool_call_entries():
    ui = make_ui(height=1000, width=100, show_logs=True, max_messages=1000)
    ui.messages.append(Message(role="tool_call", content="{}", timestamp="t", name="bash"))
    ui.messages.extend(mixed_height_messages(5))
    ui._ensure_line_buffer()

    render_calls = {"count": 0}
    original = ui._render_message_into

    def _counting(content, msg):
        render_calls["count"] += 1
        return original(content, msg)

    ui._render_message_into = _counting
    ui.set_context_usage(50.0, 100_000)
    ui._ensure_line_buffer()

    assert render_calls["count"] == 1  # only the tool_call entry re-rendered


# ── Cache correctness (Must Verify) ─────────────────────────────────────

def test_cache_updates_after_update_last_message_on_message_object():
    ui = make_ui(height=1000)
    ui.add_message("assistant", "hello")
    ui._ensure_line_buffer()
    before = list(ui._line_buffer)

    ui.update_last_message(" world")
    ui._ensure_line_buffer()
    after = list(ui._line_buffer)

    assert before != after
    assert any(
        any(seg.text == "hello world" for seg in line if hasattr(seg, "text"))
        for line in after
    )


def test_cache_updates_after_update_last_message_on_legacy_dict():
    ui = make_ui(height=1000)
    ui.messages.append({"role": "assistant", "content": "hi", "time": "t", "elapsed": ""})
    ui._ensure_line_buffer()
    before = list(ui._line_buffer)

    ui.update_last_message(" there")
    ui._ensure_line_buffer()
    after = list(ui._line_buffer)

    assert before != after
    assert any(
        any(seg.text == "hi there" for seg in line if hasattr(seg, "text"))
        for line in after
    )


def test_set_theme_drops_stale_line_cache():
    ui = make_ui(height=1000)
    ui.add_message("user", "hello")
    ui._ensure_line_buffer()
    before_styles = [
        seg.style for line in ui._line_buffer for seg in line if seg.style is not None
    ]

    ui.set_theme("dark")
    ui._ensure_line_buffer()
    after_styles = [
        seg.style for line in ui._line_buffer for seg in line if seg.style is not None
    ]

    assert before_styles != after_styles


# ── _content_budget unchanged (measures the disclaimer at a wrapping width) ──

def test_content_budget_measures_disclaimer_at_narrow_width_where_it_wraps():
    ui = make_ui(height=24, width=60, show_logs=False)
    disclaimer_rows = len(
        ui.console.render_lines(Align.center(Text(ui.disclaimer, style="bright_white")))
    )
    assert disclaimer_rows == 2, "fixture must exercise the wrapped case, not the common 1-row case"

    reserved = 3 + disclaimer_rows + 1 + 1
    assert ui._content_budget() == max(1, ui.console.size.height - reserved)
