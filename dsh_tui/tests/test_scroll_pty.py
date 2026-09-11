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
import tty
from io import StringIO

import pytest
from rich.console import Console

from dsh_tui.chat_ui import ChatUI, Message

JOIN_TIMEOUT = 5.0


def _make_ui(display_messages: int = 5) -> ChatUI:
    ui = ChatUI(display_messages=display_messages, show_logs=False)
    ui.console = Console(file=StringIO(), width=100)
    return ui


def _fill(ui: ChatUI, n: int) -> None:
    for i in range(n):
        ui.messages.append(Message(role="assistant", content=f"m{i}", timestamp="t"))


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
    _fill(ui, 12)  # max offset = 7

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

    # PgUp: \e[5~ -> scroll_up(), one page of 5.
    os.write(master_fd, b"\x1b[5~")
    assert _wait_for(lambda: ui._scroll_offset == 5), (
        f"PgUp did not move offset to 5 (got {ui._scroll_offset})"
    )

    # Ctrl+U: another scroll_up(), clamped at max offset 7.
    os.write(master_fd, b"\x15")
    assert _wait_for(lambda: ui._scroll_offset == 7), (
        f"Ctrl+U did not clamp offset to 7 (got {ui._scroll_offset})"
    )

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
    assert ui._scroll_offset == 5


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
    _fill(ui, 12)  # max offset = 7

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
    # Home -> scroll_to_top(): offset 0 -> 7 (changed), triggers the
    # repaint whose _print_frame() call is where the burst above gets
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
    assert ui._scroll_offset == 7, "Ctrl+U during the window must not itself repaint or move the offset here"
