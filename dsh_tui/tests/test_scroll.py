"""Unit tests for ChatUI's vim-like scrollback (Task 01).

Pure, terminal-free coverage of the scroll offset API, the scroll-aware
render slice, the footer text, and the snap-to-bottom behavior on new
content. No pty/tty involved: the raw-byte key dispatch in
`_input_with_history_unix` is exercised only through the thin, testable
methods it calls (`scroll_up`/`scroll_down`/`scroll_to_top`/`scroll_to_bottom`),
never through simulated terminal input.
"""
from io import StringIO

from rich.align import Align
from rich.console import Console
from rich.text import Text

from dsh_tui.chat_ui import ChatUI, Message


def _make_ui(display_messages: int = 5, show_logs: bool = True) -> ChatUI:
    ui = ChatUI(display_messages=display_messages, show_logs=show_logs)
    # Redirect Rich output away from the real terminal for render assertions.
    # Height is pinned explicitly tall so the height-aware windowing gate
    # (see chat_ui.py's _content_budget/_fit_backward/_fit_forward) is
    # non-binding here — this file's tests are the fixed-count regression
    # suite for "the ceiling still applies"; height-gate behavior itself is
    # covered by the dedicated tests below with an explicit smaller height.
    ui.console = Console(file=StringIO(), width=100, height=1000)
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


# ── Height-aware windowing ────────────────────────────────────────────
#
# Unlike `_make_ui` above (pinned tall so the height gate never binds),
# these tests pin an explicit, small console height to exercise the gate
# itself: `_content_budget()`, `_fit_backward()`/`_fit_forward()`, and
# their effect on `_max_scroll_offset()`/`_visible_messages()`.

def _make_ui_with_height(
    height: int, display_messages: int = 15, show_logs: bool = False, width: int = 80,
) -> ChatUI:
    ui = ChatUI(display_messages=display_messages, show_logs=show_logs)
    ui.console = Console(file=StringIO(), width=width, height=height)
    return ui


def test_height_gate_fires_with_few_but_tall_messages():
    """4 messages whose combined height exceeds an 80x24 budget: today's
    fixed-count behavior would show all 4 with offset 0; height-aware
    windowing must instead trim the window and report scroll room.
    """
    ui = _make_ui_with_height(24, display_messages=15, show_logs=False, width=80)
    tall_content = "\n".join(f"line{i}" for i in range(20))
    for i in range(4):
        ui.messages.append(Message(role="assistant", content=f"{tall_content}-{i}", timestamp="t"))

    assert ui._max_scroll_offset() > 0
    assert len(ui._visible_messages()) < 4


def test_frame_fits_viewport_when_every_message_fits_budget():
    """Total rendered rows of the full frame (logs panel if show_logs +
    render_messages() group + disclaimer + blank + 1 prompt row) never
    exceeds console height, as long as every individually displayed
    message fits the budget on its own (the non-degenerate case).
    """
    for show_logs in (False, True):
        ui = _make_ui_with_height(24, display_messages=15, show_logs=show_logs, width=80)
        for i in range(10):
            ui.messages.append(Message(role="assistant", content=f"short message {i}", timestamp="t"))
        if show_logs:
            ui.add_log("something happened")

        msgs_group_rows = len(ui.console.render_lines(ui.render_messages()))
        disclaimer_rows = len(ui.console.render_lines(Align.center(Text(ui.disclaimer))))
        logs_rows = len(ui.console.render_lines(ui.render_logs_panel())) if ui.show_logs else 0
        total = logs_rows + msgs_group_rows + disclaimer_rows + 1 + 1  # +blank +prompt row

        assert total <= ui.console.size.height, (show_logs, total, ui.console.size.height)


def test_visible_messages_never_empty_at_degenerate_budget():
    """A console too short for even one full row of chrome must still show
    exactly one message rather than an empty window.
    """
    ui = _make_ui_with_height(1, display_messages=15, show_logs=False, width=80)
    _fill(ui, 5)

    visible = ui._visible_messages()
    assert len(visible) == 1


def test_visible_messages_never_empty_with_oversized_single_message():
    """A single message taller than the whole viewport still counts as the
    (only) visible message — it overflows rather than disappearing.
    """
    ui = _make_ui_with_height(10, display_messages=15, show_logs=False, width=80)
    huge_content = "\n".join(f"line{i}" for i in range(100))
    ui.messages.append(Message(role="assistant", content=huge_content, timestamp="t"))

    visible = ui._visible_messages()
    assert len(visible) == 1
    assert ui._max_scroll_offset() == 0


def test_scroll_to_top_reaches_oldest_message_under_height_gate():
    """When the height gate trims the window below `display_messages`,
    `scroll_to_top()` must still be able to reach `messages[0]`.
    """
    ui = _make_ui_with_height(24, display_messages=15, show_logs=False, width=80)
    for i in range(10):
        ui.messages.append(Message(role="assistant", content=f"short message {i}", timestamp="t"))

    max_offset = ui._max_scroll_offset()
    assert max_offset > 0  # height gate is actually binding in this scenario

    assert ui.scroll_to_top() is True
    visible = ui._visible_messages()
    assert visible[0] is ui.messages[0]


def test_window_capped_at_display_messages_on_tall_console():
    """Even on a very tall console, the window never exceeds
    `display_messages` — the fit loop is bounded, not unbounded.
    """
    ui = _make_ui_with_height(1000, display_messages=5, show_logs=False, width=80)
    _fill(ui, 20)

    assert len(ui._visible_messages()) == 5


def test_footer_range_reflects_real_window_size_under_height_gate():
    """The footer's `Showing X-Y of Z` must name the actual (height-
    trimmed) window bounds, not a `display_messages`-sized range.
    """
    ui = _make_ui_with_height(24, display_messages=15, show_logs=False, width=80)
    for i in range(10):
        ui.messages.append(Message(role="assistant", content=f"short message {i}", timestamp="t"))
    ui.scroll_up()

    visible = ui._visible_messages()
    text = _render_text(ui)
    end = 10 - ui._scroll_offset
    start = end - len(visible) + 1
    assert f"Showing {start}-{end} of 10 messages" in text


def test_measurement_failure_falls_back_to_fixed_count_without_raising():
    """Any exception inside measurement degrades to the old fixed-count
    behavior instead of propagating — `_max_scroll_offset()` is called
    from inside the raw-mode input loop outside any `try`, so a raise
    there would break typing, not just rendering.
    """
    ui = _make_ui_with_height(24, display_messages=5, show_logs=False, width=80)
    _fill(ui, 12)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    ui.console.render_lines = _boom

    assert ui._max_scroll_offset() == max(0, 12 - 5)
    visible = ui._visible_messages()
    assert [m.content for m in visible] == ["m7", "m8", "m9", "m10", "m11"]


# ── Multi-step scroll walk / gap-free tiling (R2-2, review finding R2-1) ──
#
# R2-1: `scroll_down` used to page by `_window_size()` (a *backward* fit from
# the current window's end — the size of the window being left) instead of a
# *forward* fit from that same end. With variable message heights this let
# successive downward windows drift out of alignment, silently skipping
# messages that then became unreachable at any offset (tech-lead's repro:
# 25 mixed-height messages, indices [2, 7, 10, 20, 23] never displayed).
# None of the tests above — including this ticket's own prior additions —
# ever walked a multi-step scroll sequence; every assertion was a single
# step's resulting offset or an endpoint clamp, which cannot detect a
# tiling gap between two adjacent windows. These walk the full scroll range
# one step at a time and assert every message is shown somewhere along the
# way, using message *identity* (`id()`, not `list.index()` or `==`) since
# `Message` is a dataclass with value equality and content can collide.

def _mixed_height_messages(n: int) -> list:
    """Heterogeneous per-message heights, matching tech-lead's exact R2-1
    repro shape: one-liner, 400-char single line, 14-bullet list, and
    120-word body, cycled. Uniform-height messages would not expose the
    misalignment — every window would tile correctly by coincidence.
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


def _walk_ids(ui: ChatUI, step) -> set:
    """Repeatedly call `step` (`ui.scroll_down` or `ui.scroll_up`), collecting
    the identity of every message visible at every offset along the way,
    including the starting offset.
    """
    seen = {id(m) for m in ui._visible_messages()}
    while step():
        seen.update(id(m) for m in ui._visible_messages())
    return seen


def test_scroll_down_walk_from_top_covers_every_message_with_mixed_heights():
    """Regression test for review finding R2-1. Walks `scroll_down()` one
    step at a time from `scroll_to_top()` to the bottom and asserts the
    union of every visible window covers all 25 mixed-height messages —
    this would have failed against the pre-fix `scroll_down` (which paged
    by `_window_size()`), reproducing tech-lead's exact missing indices.
    """
    ui = _make_ui_with_height(30, display_messages=15, show_logs=True, width=80)
    messages = _mixed_height_messages(25)
    ui.messages.extend(messages)

    ui.scroll_to_top()
    seen = _walk_ids(ui, ui.scroll_down)

    missing = [i for i, m in enumerate(messages) if id(m) not in seen]
    assert not missing, f"messages at indices {missing} were never shown during the downward walk"


def test_scroll_up_walk_from_bottom_covers_every_message_with_mixed_heights():
    """Mirror of the above walking `scroll_up()` from the bottom. R2-1 was
    specific to `scroll_down`'s forward paging (tech-lead's own mirror walk
    with `scroll_up` showed zero gaps even pre-fix), but this closes the
    same class of gap for the `scroll_up` direction by test rather than by
    assuming the asymmetry stays fixed.
    """
    ui = _make_ui_with_height(30, display_messages=15, show_logs=True, width=80)
    messages = _mixed_height_messages(25)
    ui.messages.extend(messages)

    ui.scroll_to_bottom()
    seen = _walk_ids(ui, ui.scroll_up)

    missing = [i for i, m in enumerate(messages) if id(m) not in seen]
    assert not missing, f"messages at indices {missing} were never shown during the upward walk"


def test_fit_loop_terminates_at_display_messages_ceiling_with_zero_height_messages():
    """A run of `tool_call`/`tool_result` messages with `show_logs=False`
    renders as zero rows each (`_render_message_into` gates their output on
    `show_logs`), so every candidate total in the fit loop only grows by 1
    per message regardless of budget. Without the `count < self.display_messages`
    loop bound this would walk arbitrarily far into the message list instead
    of stopping at the ceiling — this is the degenerate case flagged in
    ticket 01's Must Verify that its own new tests didn't directly exercise.
    """
    ui = _make_ui_with_height(24, display_messages=5, show_logs=False, width=80)
    for i in range(20):
        role = "tool_call" if i % 2 == 0 else "tool_result"
        ui.messages.append(Message(role=role, content="{}", timestamp="t", name="bash"))

    visible = ui._visible_messages()
    assert len(visible) == 5  # capped at display_messages, not all 20
    assert ui._max_scroll_offset() == 15


def test_content_budget_measures_disclaimer_at_narrow_width_where_it_wraps():
    """Resolves ticket 01's flagged Unknown: `Align.center(Text(disclaimer))`
    does exceed 1 row at some in-use widths (measured: 2 rows at width 60,
    1 row at width 80+) — this is exactly why `_content_budget()` measures
    it rather than hardcoding a row count. Confirms the measured value is
    what actually gets reserved, at a width where the extra row matters.
    """
    ui = _make_ui_with_height(24, display_messages=15, show_logs=False, width=60)
    disclaimer_rows = len(
        ui.console.render_lines(Align.center(Text(ui.disclaimer, style="bright_white")))
    )
    assert disclaimer_rows == 2, "fixture must exercise the wrapped case, not the common 1-row case"

    reserved = 3 + disclaimer_rows + 1 + 1  # header+blank+footer, disclaimer, blank, prompt row
    assert ui._content_budget() == max(1, ui.console.size.height - reserved)


def test_no_regression_when_everything_fits_under_finite_height():
    """Mirror of `test_height_gate_fires_with_few_but_tall_messages` with
    short instead of tall content: on a finite (not artificially huge)
    console, when everything actually fits, the height gate must not fire
    spuriously — offset stays 0, all messages visible, footer unchanged.
    Nothing in the pre-existing suite exercises "everything fits" on a
    finite height fixture; the other regression tests all use the
    height=1000 fixture, which cannot distinguish "gate correctly inactive"
    from "gate impossible to trigger."
    """
    ui = _make_ui_with_height(24, display_messages=15, show_logs=False, width=80)
    for i in range(4):
        ui.messages.append(Message(role="assistant", content=f"short {i}", timestamp="t"))

    assert ui._max_scroll_offset() == 0
    assert len(ui._visible_messages()) == 4

    text = _render_text(ui)
    assert "Showing last 4 of 4 messages" in text
    assert "scrolled" not in text


def test_show_logs_shrinks_budget_relative_to_show_logs_false():
    """The logs panel is measured and subtracted from the content budget
    only when `show_logs` is True, so an otherwise-identical transcript
    shows fewer messages with logs on than with logs off.
    """
    ui_no_logs = _make_ui_with_height(24, display_messages=15, show_logs=False, width=80)
    ui_with_logs = _make_ui_with_height(24, display_messages=15, show_logs=True, width=80)
    for ui in (ui_no_logs, ui_with_logs):
        for i in range(10):
            ui.messages.append(Message(role="assistant", content=f"short message {i}", timestamp="t"))
    ui_with_logs.add_log("something happened")

    assert len(ui_with_logs._visible_messages()) < len(ui_no_logs._visible_messages())
