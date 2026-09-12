"""Pty-based integration test for the scrollback key dispatch in
`_input_with_history_unix` (Task 01).

This is the one piece of the feature the developer flagged they could not
verify live (raw-mode escape-sequence handling, cooked-terminal restore
around Rich prints, TCSADRAIN type-ahead preservation) because they are a
non-interactive agent with no pty. `pty.openpty()` gives us a real terminal
fd pair, so `termios`/`tty` behave exactly as they would against a real
tty — this closes that gap for real instead of by code reading.

Two things are exercised end to end, driving raw bytes through the actual
read loop (no mocking of the escape-sequence parser):

1. PgUp / Ctrl+U / End move `_scroll_offset` and the trailing `~` of the
   PgUp sequence does not leak into the typed input (the exact class of
   bug the ticket calls out: `\\e[20~`/F9 leaking `~`, chat_ui.py:1414).
2. Type-ahead queued *before* the read loop starts is not dropped by the
   scroll repaint's re-entry into raw mode. All bytes (PgUp sequence +
   "ok\\r") are written to the pty in one burst before the reader thread
   is even started, so by the time `_repaint_with_input` calls
   `termios.tcsetattr(fd, termios.TCSADRAIN, ...)`, "ok\\r" is still
   sitting unread in the kernel input queue. If a future change
   regressed to the `tty.setraw` default of `TCSAFLUSH` (which discards
   pending input), that trailing input would vanish and the submitted
   result would come back empty/truncated instead of "ok".
"""
import os
import pty
import queue
import select
import threading
import time
import tty
from io import StringIO

import pytest
from rich.console import Console

from dsh_tui.chat_ui import ChatUI, Message
from dsh_tui.tests.scroll_helpers import fill as _fill
from dsh_tui.tests.scroll_helpers import make_ui as _make_ui_with_height
from dsh_tui.tests.scroll_helpers import strip_ansi, tall_message

JOIN_TIMEOUT = 5.0


def _make_ui(display_messages: int = 5) -> ChatUI:
    # Constructed directly (not via `scroll_helpers.make_ui`) because this
    # helper deliberately exercises passing `display_messages` into the
    # real `ChatUI` constructor for API-compatibility coverage (Action
    # item 6: the parameter is retained but no longer bounds the window).
    # Task 01 (line-based scrollback) retired `display_messages` as a
    # windowing ceiling — the terminal height is now the only thing that
    # creates scroll room, so this height is pinned small enough (unlike
    # the old height=1000 fixture) that 12 short messages still overflow
    # the content budget. Offset assertions below compute the expected
    # step from `_window_size()`/`_max_scroll_offset()` rather than
    # hardcoding numbers derived from the old message-count cap.
    ui = ChatUI(display_messages=display_messages, show_logs=False)
    ui.console = Console(file=StringIO(), width=100, height=15)
    return ui


@pytest.fixture
def pty_pair():
    master_fd, slave_fd = pty.openpty()
    # The slave starts in canonical (cooked) mode, which line-buffers input
    # until a newline. Pre-arm raw mode here rather than relying on the
    # timing of the reader thread's own `tty.setraw(fd)` call — otherwise
    # bytes written before that call executes race being buffered by the
    # line discipline instead of delivered byte-by-byte. The loop under
    # test still calls `tty.setraw` itself (idempotent on an already-raw
    # fd), so this doesn't remove anything from what's being exercised.
    tty.setraw(slave_fd)
    yield master_fd, slave_fd
    for fd in (master_fd, slave_fd):
        try:
            os.close(fd)
        except OSError:
            pass


def _start_drain(master_fd):
    """Continuously read `master_fd` in the background and discard it.

    Without this, everything the reader thread's redirected `sys.stdout`
    writes (the prompt, redraw escapes, the repainted frame) piles up
    unread on the pty's slave-side output queue. Nobody in this test
    process ever reads the master side otherwise, so once that kernel
    buffer fills, the next `write()` in the reader thread blocks forever
    — indistinguishable from a real deadlock in the code under test.
    `sys.stdout` is also process-global, not thread-local, so this masked
    even this test file's own debug output the first time this bug was
    tracked down.
    """
    stop = threading.Event()

    def _drain():
        while not stop.is_set():
            r, _, _ = select.select([master_fd], [], [], 0.05)
            if r:
                try:
                    os.read(master_fd, 4096)
                except OSError:
                    return

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    return stop, t


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _run_input_loop(ui, slave_fd, result_q):
    stdin_file = os.fdopen(os.dup(slave_fd), "rb", buffering=0)
    stdout_file = os.fdopen(os.dup(slave_fd), "w", buffering=1)
    import sys as _sys

    old_stdin, old_stdout = _sys.stdin, _sys.stdout
    _sys.stdin, _sys.stdout = stdin_file, stdout_file
    try:
        result = ui._input_with_history_unix(prompt="> ")
        result_q.put(("ok", result))
    except Exception as e:  # pragma: no cover - failure surfaced via queue
        result_q.put(("error", e))
    finally:
        _sys.stdin, _sys.stdout = old_stdin, old_stdout
        stdin_file.close()
        stdout_file.close()


def test_pgup_ctrlu_end_scroll_without_leaking_tilde(pty_pair):
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    max_offset = ui._max_scroll_offset()
    assert max_offset > 0, "fixture must actually create scroll room"
    step = ui._window_size()

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    def _wait_for(predicate, timeout=2.0):
        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    import time as _time

    _time.sleep(0.2)  # let the loop's own initial tty.setraw(fd) run first
    # (TCSAFLUSH by default — see test_typeahead_survives... docstring
    # below for why bytes written before that call would be discarded)

    # PgUp: \e[5~ -> scroll_up(), one page.
    os.write(master_fd, b"\x1b[5~")
    assert _wait_for(lambda: ui._scroll_offset == min(max_offset, step)), (
        f"PgUp did not move offset to {min(max_offset, step)} (got {ui._scroll_offset})"
    )

    # Ctrl+U: keep paging up until clamped at max offset.
    guard = 0
    while ui._scroll_offset < max_offset and guard < 50:
        before = ui._scroll_offset
        os.write(master_fd, b"\x15")
        assert _wait_for(lambda: ui._scroll_offset != before), (
            "Ctrl+U made no progress toward max offset"
        )
        guard += 1
    assert ui._scroll_offset == max_offset

    # End: \e[4~ -> scroll_to_bottom().
    os.write(master_fd, b"\x1b[4~")
    assert _wait_for(lambda: ui._scroll_offset == 0), (
        f"End did not reset offset to 0 (got {ui._scroll_offset})"
    )

    # Now type "ok" and submit; the trailing '~' from PgUp/End must not
    # have leaked into the input buffer.
    os.write(master_fd, b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", f"stray bytes leaked into input: {payload!r}"


def test_typeahead_survives_scroll_repaint_tcsadrain(pty_pair):
    """Bytes queued before the scroll repaint's mid-stream `tcsetattr` must
    survive it (TCSADRAIN, not the TCSAFLUSH default) — i.e. no dropped
    type-ahead across a repaint.

    Note this cannot be a burst written before the thread even starts:
    `_input_with_history_unix`'s *own* initial `tty.setraw(fd)` call (its
    very first statement, before the read loop) uses the stdlib default of
    `TCSAFLUSH`, which discards any input queued before that call runs —
    confirmed by reproducing it directly (see ticket 03's Execution Log).
    That is unrelated to the risk under test here (it fires once, before
    any of this feature's code runs) so the burst is written just after
    the thread starts instead, giving the initial `setraw` a moment to run
    before bytes land in the queue that the *scroll repaint's* `tcsetattr`
    must not discard.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    max_offset = ui._max_scroll_offset()
    assert max_offset > 0, "fixture must actually create scroll room"
    expected_offset = min(max_offset, ui._window_size())

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    import time

    time.sleep(0.2)  # let the loop's own initial tty.setraw(fd) run first
    # Burst written together so "ok\r" is still unread in the kernel queue
    # when the PgUp branch's _repaint_with_input calls tcsetattr mid-stream.
    os.write(master_fd, b"\x1b[5~" + b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", (
        f"type-ahead queued before the repaint was dropped or corrupted: {payload!r}"
    )
    assert ui._scroll_offset == expected_offset


def test_bytes_arriving_inside_repaint_termios_window_survive(pty_pair):
    """Regression test for tech-lead review finding R2-1: an earlier version
    of `_repaint_with_input` restored the *full* cooked termios settings
    (not just OPOST/ONLCR) for the print window, which also re-enabled
    ECHO/ICANON/ISIG for those ~3-5ms. A keystroke arriving in that window
    got line-discipline-processed as cooked input instead of passed through
    raw — a repeated Ctrl+U in particular was interpreted as VKILL and
    silently destroyed whatever was sitting unread in the canonical buffer.
    The fix ORs only OPOST|ONLCR onto the already-raw attributes, so ECHO/
    ICANON/ISIG stay off for the whole repaint.

    A real ~3-5ms wall-clock window is too short to land a write inside
    reliably from a test process without either flakiness or a synthetic
    slowdown that wouldn't reflect real timing. Instead of guessing at
    timing, this hooks the exact seam that *defines* the window: `_print_
    frame()` is the only thing that runs between the two `tcsetattr` calls
    in `_repaint_with_input`, so injecting the burst from inside a wrapped
    `_print_frame` guarantees the bytes are already sitting unread in the
    kernel queue while the cooked-output attributes are active — the
    injection is synchronous with the window by construction, not by luck.

    The injected burst is "xy" + Ctrl+U + "z" + Enter. If ECHO/ICANON were
    back on during the window (the bug), the kernel's own line discipline
    would consume "xy" into its canonical edit buffer and then erase it on
    Ctrl+U (VKILL) before this process ever reads any of it — only "z"
    would reach the read loop. With the fix, ECHO/ICANON/ISIG stay off, so
    all four bytes pass through as raw input to the application: "x"/"y"
    insert as text, `\\x15` is consumed by *this app's own* Ctrl+U-scroll
    binding (a no-op here since the view is already scrolled to the top,
    so no nested repaint fires), and "z" inserts as text — nothing lost.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    max_offset = ui._max_scroll_offset()
    assert max_offset > 0, "fixture must actually create scroll room"

    injected = {"done": False}
    original_print_frame = ui._print_frame

    def _print_frame_with_injection():
        if not injected["done"]:
            injected["done"] = True
            os.write(master_fd, b"xy\x15z\r")
        return original_print_frame()

    ui._print_frame = _print_frame_with_injection

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    import time

    time.sleep(0.2)  # let the loop's own initial tty.setraw(fd) run first

    os.write(master_fd, b"ab")
    # Home -> scroll_to_top(): offset 0 -> max_offset (changed), triggers
    # the repaint whose _print_frame() call is where the burst above gets
    # injected mid-window. Using Home (not PgUp) means the Ctrl+U inside
    # the injected burst lands with the view already at max offset, so
    # `scroll_up()` there is a no-op (no nested repaint to reason about).
    os.write(master_fd, b"\x1b[1~")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "abxyz", (
        f"bytes arriving inside the repaint's termios window were lost or "
        f"corrupted (VKILL-style truncation would show as 'abz' or similar "
        f"missing 'xy'): {payload!r}"
    )
    assert ui._scroll_offset == max_offset, "Ctrl+U during the window must not itself repaint or move the offset here"


def test_scroll_mode_esc_toggle_jk_gg_G_and_arrows_scroll_not_history(pty_pair):
    """A standalone Esc toggles vim-style scroll mode. While active: j/k and
    Up/Down arrows scroll (the arrow case is the actual bug report this
    feature exists for — a terminal reporting a mouse-wheel scroll as plain
    `\\e[A`/`\\e[B` is indistinguishable from a real arrow keypress, so
    without an explicit mode it silently recalled input history instead of
    scrolling); gg/G jump to top/bottom. None of j/k/g/G leak into the typed
    buffer, and the Up-arrow scrolling (rather than recalling
    "previous message" from history) is the direct proof the mode is active.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    max_offset = ui._max_scroll_offset()
    assert max_offset > 0, "fixture must actually create scroll room"
    window_size = ui._window_size()
    ui.input_history.append("previous message")

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)  # let the loop's own initial tty.setraw(fd) run first

    # Standalone Esc — must be its own write so the 50ms disambiguation
    # window elapses before the next byte, otherwise it reads as the start
    # of a CSI sequence instead of a bare Esc.
    os.write(master_fd, b"\x1b")
    time.sleep(0.15)

    os.write(master_fd, b"k")  # scroll_up(): 0 -> window_size
    assert _wait_for(lambda: ui._scroll_offset == min(max_offset, window_size)), (
        f"'k' did not scroll up while in scroll mode (got {ui._scroll_offset})"
    )
    after_k = ui._scroll_offset
    expected_after_up = min(max_offset, after_k + ui._window_size())

    os.write(master_fd, b"\x1b[A")  # Up arrow: scroll_up(), further up
    assert _wait_for(lambda: ui._scroll_offset == expected_after_up), (
        f"Up arrow did not scroll in scroll mode — got {ui._scroll_offset}; "
        f"if it recalled history instead this would still read {after_k}"
    )

    os.write(master_fd, b"j")  # scroll_down(): move back toward bottom
    assert _wait_for(lambda: ui._scroll_offset < max_offset), (
        f"'j' did not scroll down while in scroll mode (got {ui._scroll_offset})"
    )

    os.write(master_fd, b"gg")  # scroll_to_top(): -> max_offset
    assert _wait_for(lambda: ui._scroll_offset == max_offset), (
        f"'gg' did not jump to top (got {ui._scroll_offset})"
    )

    os.write(master_fd, b"G")  # scroll_to_bottom(): -> 0
    assert _wait_for(lambda: ui._scroll_offset == 0), (
        f"'G' did not jump to bottom (got {ui._scroll_offset})"
    )

    os.write(master_fd, b"\x1b")  # Esc again — exit scroll mode
    time.sleep(0.15)
    os.write(master_fd, b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", (
        f"j/k/g/G leaked into the input buffer, or history was recalled "
        f"into it: {payload!r}"
    )


def test_scroll_mode_exit_via_enter_and_i_preserve_typed_buffer(pty_pair):
    """Enter and 'i' both exit scroll mode back to typing (in addition to
    Esc), and neither submits the input nor disturbs the in-progress buffer
    typed before entering scroll mode.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)
    max_offset = ui._max_scroll_offset()
    assert max_offset > 0, "fixture must actually create scroll room"
    expected_after_k = min(max_offset, ui._window_size())

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)

    os.write(master_fd, b"ab")

    os.write(master_fd, b"\x1b")  # enter scroll mode
    time.sleep(0.15)
    os.write(master_fd, b"k")  # scroll_up(): 0 -> expected_after_k
    assert _wait_for(lambda: ui._scroll_offset == expected_after_k)

    os.write(master_fd, b"\r")  # Enter — exit scroll mode, must not submit
    time.sleep(0.15)
    assert result_q.empty(), "Enter inside scroll mode submitted instead of exiting the mode"
    assert thread.is_alive(), "input loop returned early on Enter inside scroll mode"

    os.write(master_fd, b"cd")  # still typing after the Enter-exit

    os.write(master_fd, b"\x1b")  # enter scroll mode again
    time.sleep(0.15)
    os.write(master_fd, b"i")  # exit via 'i' this time
    time.sleep(0.15)

    os.write(master_fd, b"\r")  # real submit

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "abcd", (
        f"typed buffer was not preserved across scroll-mode entry/exit: {payload!r}"
    )


def test_scroll_mode_with_too_few_messages_toggles_but_does_not_scroll(pty_pair):
    """Regression coverage for the live bug report: with the real default
    `display_messages=15` and fewer messages than that, `_max_scroll_offset()`
    is 0, so j/k/gg/G are legitimate no-ops (nothing to scroll to) — this is
    not a bug, but tech-lead review flagged that scroll mode gave zero visual
    feedback in this case, indistinguishable from a broken feature. This
    locks in that the mode still toggles (and exits) correctly, and that the
    offset genuinely never moves, when there is nothing to scroll — and a
    console tall enough that the height gate does not bind.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui()  # default display_messages=15
    _fill(ui, 10)  # fewer than display_messages -> max offset 0

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)

    os.write(master_fd, b"\x1b")  # enter scroll mode
    time.sleep(0.15)
    for key in (b"k", b"j", b"gg", b"G", b"\x1b[A", b"\x1b[B"):
        os.write(master_fd, key)
        time.sleep(0.05)
    assert ui._scroll_offset == 0, (
        f"offset moved with nothing to scroll (got {ui._scroll_offset})"
    )

    os.write(master_fd, b"\x1b")  # exit scroll mode
    time.sleep(0.15)
    os.write(master_fd, b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", f"scroll-mode keys leaked into input: {payload!r}"


def test_scroll_mode_guards_backspace_delete_and_left_right(pty_pair):
    """Backspace, Delete (`\\e[3~`), and Left/Right arrows are no-ops while
    in scroll mode (can't edit or move the cursor in an invisible buffer
    while scrolling) — none of them should alter `current_input`.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)

    os.write(master_fd, b"abc")

    os.write(master_fd, b"\x1b")  # enter scroll mode
    time.sleep(0.15)

    os.write(master_fd, b"\x7f")  # Backspace — must be a no-op
    time.sleep(0.05)
    os.write(master_fd, b"\x1b[3~")  # Delete — must be a no-op
    time.sleep(0.05)
    os.write(master_fd, b"\x1b[D")  # Left arrow — must be a no-op
    time.sleep(0.05)
    os.write(master_fd, b"\x1b[C")  # Right arrow — must be a no-op
    time.sleep(0.05)

    os.write(master_fd, b"i")  # exit scroll mode
    time.sleep(0.15)
    os.write(master_fd, b"Z\r")  # confirm cursor/buffer still sane

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "abcZ", (
        f"backspace/delete/left/right leaked through scroll-mode guards: {payload!r}"
    )


def test_scroll_mode_paste_start_exits_mode_instead_of_dropping_content(pty_pair):
    """Regression test for tech-lead finding F1: a bracketed paste starting
    while in scroll mode used to have its content silently mangled — the
    paste's own embedded newline was read as the "exit scroll mode" Enter
    (via the `if scroll_mode: ... elif in_paste:` ordering), so everything
    before the first `\\n` in the pasted text was dropped and only the tail
    got typed normally. The fix: detecting a paste start while in scroll
    mode now exits the mode immediately, so the paste proceeds through the
    normal (non-scroll-mode) path unmangled.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)

    os.write(master_fd, b"\x1b")  # enter scroll mode
    time.sleep(0.15)

    # Bracketed paste of "alpha\nbeta" arriving mid-scroll-mode.
    os.write(master_fd, b"\x1b[200~alpha\rbeta\x1b[201~")
    time.sleep(0.1)
    os.write(master_fd, b"\r")  # submit

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "alpha\nbeta", (
        f"paste starting in scroll mode was mangled instead of exiting the "
        f"mode cleanly: {payload!r}"
    )


def _start_capturing_drain(master_fd):
    """Like `_start_drain`, but accumulates read bytes instead of discarding
    them, so a test can inspect what was actually printed (e.g. which
    scroll-mode prompt string appeared) rather than only internal state.
    """
    stop = threading.Event()
    buf = bytearray()
    lock = threading.Lock()

    def _drain():
        while not stop.is_set():
            r, _, _ = select.select([master_fd], [], [], 0.05)
            if r:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    return
                with lock:
                    buf.extend(data)

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    def _snapshot() -> bytes:
        with lock:
            return bytes(buf)

    return stop, t, _snapshot


def test_scroll_mode_shows_real_prompt_with_few_but_tall_messages(pty_pair):
    """End-to-end regression for the originally reported bug: with few
    messages that are individually tall enough to overflow the console
    height, `_max_scroll_offset()` is > 0 (the height gate, not the
    `display_messages` count, is what's binding here) — so Esc must show
    the real `_SCROLL_MODE_PROMPT`, not `_SCROLL_MODE_NOTHING_PROMPT`. This
    is exercised through the actual raw-mode key-dispatch loop and its real
    printed output, not through direct calls to
    `_max_scroll_offset()`/`_visible_messages()` the way `test_scroll.py`'s
    `test_height_gate_fires_with_few_but_tall_messages` does.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui_with_height(height=24, show_logs=True, width=80)
    tall_content = "\n".join(f"line{i}" for i in range(20))
    for i in range(4):
        ui.messages.append(Message(role="assistant", content=f"{tall_content}-{i}", timestamp="t"))
    ui.add_log("something happened")

    assert ui._max_scroll_offset() > 0, "fixture must actually bind the height gate"
    window_size = ui._window_size()
    max_offset = ui._max_scroll_offset()

    stop, drain_thread, snapshot = _start_capturing_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)

    os.write(master_fd, b"\x1b")  # standalone Esc -> toggle scroll mode
    time.sleep(0.15)

    def _prompt_shown():
        return b"-- SCROLL --" in snapshot()

    assert _wait_for(_prompt_shown), "scroll mode prompt never appeared in output"
    output = snapshot()
    assert ui._SCROLL_MODE_PROMPT.encode() in output, (
        f"expected the real scroll prompt, not the 'nothing to scroll' one; "
        f"captured output: {output!r}"
    )
    assert ui._SCROLL_MODE_NOTHING_PROMPT.encode() not in output

    os.write(master_fd, b"k")  # scroll_up(): 0 -> window_size
    assert _wait_for(lambda: ui._scroll_offset == min(max_offset, window_size)), (
        f"'k' did not move the offset (got {ui._scroll_offset}, expected "
        f"{min(max_offset, window_size)})"
    )
    after_k = ui._scroll_offset

    os.write(master_fd, b"j")  # scroll_down(): back toward bottom
    assert _wait_for(lambda: ui._scroll_offset == max(0, after_k - window_size)), (
        f"'j' did not move the offset back (got {ui._scroll_offset})"
    )

    os.write(master_fd, b"gg")  # scroll_to_top()
    assert _wait_for(lambda: ui._scroll_offset == max_offset), (
        f"'gg' did not reach max offset {max_offset} (got {ui._scroll_offset})"
    )
    visible = ui._visible_messages()
    assert visible[0] is ui.messages[0], "top not actually reachable via gg under the height gate"

    os.write(master_fd, b"G")  # scroll_to_bottom()
    assert _wait_for(lambda: ui._scroll_offset == 0), (
        f"'G' did not return to bottom (got {ui._scroll_offset})"
    )

    os.write(master_fd, b"\x1b")  # exit scroll mode
    time.sleep(0.15)
    os.write(master_fd, b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", f"scroll-mode keys leaked into input: {payload!r}"


def test_scroll_mode_lone_g_does_not_swallow_the_exit_key(pty_pair):
    """Regression test for tech-lead finding F2: a lone 'g' (not followed by
    a second 'g') used to discard whatever byte it peeked at to check for
    the double-press — including the Esc/Enter/i exit keys, which left
    scroll mode looking "stuck" until the exit key was pressed twice. The
    fix queues the non-'g' peeked byte as `pending_char` so it's processed
    normally on the next loop iteration instead of being dropped.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui(display_messages=5)
    _fill(ui, 12)

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)

    os.write(master_fd, b"\x1b")  # enter scroll mode
    time.sleep(0.15)

    # 'g' immediately followed by 'i', written together so 'i' is already
    # sitting in the kernel queue when the 'g' handler's peek `select()`
    # checks — this is what makes the peek's `os.read` consume 'i' instead
    # of timing out, which is the exact condition the bug needs: the fix is
    # that a non-'g' peeked byte must be queued (`pending_char`), not
    # dropped, so 'i' still exits scroll mode on the next loop iteration.
    os.write(master_fd, b"gi")
    time.sleep(0.15)
    os.write(master_fd, b"ok\r")  # if still in scroll mode, "o"/"k" would be

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", (
        f"lone 'g' left scroll mode stuck, swallowing the 'i' exit key: {payload!r}"
    )


def test_tall_message_fully_reachable_via_real_key_dispatch(pty_pair):
    """Ticket 02's pty-level statement of AC 1 from ticket 01: a message
    taller than the console must be fully reachable *through the real key
    dispatch loop* (PgUp/PgDn), not merely through direct calls to
    `scroll_up()`/`scroll_down()` the way `test_scroll.py`'s
    `test_tall_message_fully_reachable_scrolling_up_from_bottom` proves at
    the unit level. This exercises the same escape-sequence parsing and
    repaint path production actually uses.
    """
    master_fd, slave_fd = pty_pair
    ui = _make_ui_with_height(height=10, width=60, show_logs=False)
    ui.messages.append(tall_message(200))
    ui._ensure_line_buffer()
    all_ids = {id(line) for line in ui._line_buffer}

    drain_stop, drain_thread = _start_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)  # let the loop's own initial tty.setraw(fd) run first

    def _visible_ids():
        start, end, _ = ui._window_bounds()
        return {id(line) for line in ui._line_buffer[start:end]}

    seen = set(_visible_ids())
    guard = 0
    while ui._scroll_offset < ui._max_scroll_offset() and guard < 500:
        before = ui._scroll_offset
        os.write(master_fd, b"\x1b[5~")  # PgUp -> scroll_up()
        assert _wait_for(lambda: ui._scroll_offset != before), (
            "PgUp made no progress toward max offset"
        )
        seen.update(_visible_ids())
        guard += 1
    assert ui._scroll_offset == ui._max_scroll_offset(), "did not reach the top"
    assert guard < 500, "reachability walk did not terminate"

    guard = 0
    while ui._scroll_offset > 0 and guard < 500:
        before = ui._scroll_offset
        os.write(master_fd, b"\x1b[6~")  # PgDn -> scroll_down()
        assert _wait_for(lambda: ui._scroll_offset != before), (
            "PgDn made no progress toward the bottom"
        )
        seen.update(_visible_ids())
        guard += 1
    assert ui._scroll_offset == 0, "did not return to the bottom"

    assert seen == all_ids, (
        f"{len(all_ids - seen)} lines never visible through real key dispatch"
    )

    os.write(master_fd, b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        drain_stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", f"stray bytes leaked into input: {payload!r}"


def test_continuation_header_visible_in_printed_frame_when_paged_mid_message(pty_pair):
    """Pty-level check of AC 3: when real PgUp key dispatch lands the window
    mid-message, the continuation header actually appears in the frame the
    application prints to the terminal — not just in the internal
    `_window_bounds()` state that `test_scroll.py`'s
    `test_continuation_header_shown_when_window_starts_mid_message` checks
    at the unit level.
    """
    master_fd, slave_fd = pty_pair
    # No explicit `file=` on the console (unlike the other fixtures in this
    # module): Rich's `Console.file` property re-reads `sys.stdout` on every
    # print when `_file` is `None`, so leaving it unset is what makes the
    # continuation header actually land in the bytes read back from
    # `master_fd` instead of an internal `StringIO` nothing else observes.
    ui = ChatUI(show_logs=False)
    ui.console = Console(width=60, height=10)
    ui.messages.append(tall_message(200))

    stop, drain_thread, snapshot = _start_capturing_drain(master_fd)
    result_q: "queue.Queue" = queue.Queue()
    thread = threading.Thread(
        target=_run_input_loop, args=(ui, slave_fd, result_q), daemon=True
    )
    thread.start()

    time.sleep(0.2)  # let the loop's own initial tty.setraw(fd) run first

    def _header_shown_internally():
        _, _, header_idx = ui._window_bounds()
        return header_idx is not None

    # A single huge message spanning the whole buffer already starts
    # "mid-message" even at offset 0 (only the very first line is a
    # boundary), so checking `_header_shown_internally()` alone would pass
    # trivially without any key ever being dispatched — and without
    # `_print_frame()` (only called from inside a scroll-key branch) ever
    # running against the real pty. An actual PgUp keypress, and waiting
    # for the offset to move, is what proves this is exercised through the
    # real key-dispatch/repaint path rather than the fixture's initial state.
    before_offset = ui._scroll_offset
    os.write(master_fd, b"\x1b[5~")  # PgUp -> scroll_up()
    assert _wait_for(lambda: ui._scroll_offset != before_offset), (
        "PgUp did not move the offset"
    )
    assert _header_shown_internally(), "fixture did not land mid-message"

    def _header_printed():
        return b"(continued)" in snapshot()

    assert _wait_for(_header_printed), (
        "continuation header never appeared in the printed pty frame"
    )
    printed = strip_ansi(snapshot().decode(errors="replace"))
    assert "(continued)" in printed

    os.write(master_fd, b"\x1b[4~")  # End -> scroll_to_bottom()
    assert _wait_for(lambda: ui._scroll_offset == 0)
    os.write(master_fd, b"ok\r")

    try:
        kind, payload = result_q.get(timeout=JOIN_TIMEOUT)
    finally:
        stop.set()
        drain_thread.join(timeout=1)
    thread.join(timeout=JOIN_TIMEOUT)
    assert not thread.is_alive(), "input loop thread did not finish"
    assert kind == "ok", f"input loop raised: {payload}"
    assert payload == "ok", f"stray bytes leaked into input: {payload!r}"
