# 03: Test-engineer pass — scroll unit-test audit, integration test, full suite

**Repo:** /home/bertie/Documents/projects/dsh-tui
**Blocked by:** 01 (Scroll state, scroll-aware render, and key dispatch), 02 (Raise the in-memory history ceiling)
**Status:** ready-for-agent

## Files
- dsh_tui/tests/test_scroll.py (audit/extend)
- dsh_tui/tests/ (new pty-based integration test file, if achievable — name at test-engineer's discretion, e.g. `dsh_tui/tests/test_scroll_pty.py`)
- dsh_tui/tests/test_replay.py (confirm unaffected, do not need to modify unless a real regression is found)

## Action (Test Boundaries, from the plan)

- **Task 01 owns** `dsh_tui/tests/test_scroll.py` (its own unit tests, per Operating Principle 3). Task 02 has no unit test of its own — it is a single constructor argument, covered by Task 01's clamping tests plus the live run.
- **No shared test infrastructure** across tasks: no `conftest.py`, no fixture builder; the existing `test_replay.py` uses a recording stub and is unaffected.
- **This pass:**
  1. Independently audit Task 01's unit tests (`dsh_tui/tests/test_scroll.py`) for gaps — do not just trust the developer's own report; read the tests and the code they exercise.
  2. Add a pty-based integration test (`pty.openpty()` driving `_input_with_history_unix` with real `\e[5~`/`\x15` bytes) if it is achievable without flakiness — and report it infeasible with evidence rather than shipping a flaky or assertion-free test if it is not achievable.
  3. Confirm `test_replay.py` still passes (no regression from Task 01/02's changes).
  4. Run the full suite with `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` and report real output (baseline before this feature: 4 passed).

## Established (verified — don't re-derive)
- Baseline suite is green: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` → 4 passed (before this feature's tickets land).
- Task 01's escape-sequence branches mirror `_handle_delete_key`'s consume-then-act shape specifically to avoid the exact class of bug the MVP already hit once (`\e[20~`/F9 leaking `~` into the input line, `chat_ui.py:1414`) — this pass's pty integration test, if achievable, is the right place to exercise real byte sequences against that risk.
- `tty.setraw(fd)` defaults to `TCSAFLUSH` (discards pending input); Task 01's repaint path is required to use `TCSADRAIN` instead. If a pty integration test is built, it is a natural place to also check no dropped type-ahead across a repaint, if that can be done without flakiness.
- `render()` prints a logs-panel side effect when `show_logs` is true; any assertions built on captured output should account for that (or construct `ChatUI` with `show_logs=False` where the logs panel isn't the point of the test).

## Must Verify
- All of Task 01's own unit tests actually exercise what they claim (clamping at both ends and the `bool` return; `_visible_messages()` slice correctness at offset 0/mid/max; offset 0 matches today's exact slice; each of `add_message`/`add_tool_call`/`add_tool_result` resets the offset; footer text for bottom/scrolled/no-overflow cases; `display_messages` larger than `len(messages)` keeps offset at 0).
- `test_replay.py` passes unmodified (or, if a real regression is found, document it and fix only what's needed to keep it accurate — do not weaken assertions to make it pass).
- Full suite passes with real command output pasted into the Execution Log entry, not summarized.

## Unknowns
None blocking. Whether pty-based testing of raw-mode key dispatch is achievable without flakiness in this environment is exactly the open question this pass is scoped to resolve and report on either way.

## Dependencies/license
None new expected. If a pty integration test needs the `pty` stdlib module only, no license/dependency risk. Do not add a new test-only third-party dependency without flagging it back for review.

## Acceptance Criteria
- [ ] Gap audit of `dsh_tui/tests/test_scroll.py` completed and reported (gaps found and fixed, or explicitly none found and why)
- [ ] Pty-based integration test added and passing, OR reported infeasible with concrete evidence (not just "didn't try")
- [ ] `test_replay.py` confirmed passing, unaffected by Task 01/02's changes
- [ ] Full suite run with real output reported: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q`

## Non-Goals
- Rewriting or duplicating Task 01's unit tests wholesale — audit and extend, don't replace
- Writing new tests for Task 02 (a single constructor argument; already covered by Task 01's clamping tests plus the live run per the plan)
- Mouse-wheel test coverage (feature is out of scope for this plan)

## Security-sensitive
no — test-only work, no credentials, no network, no production code changes.

## Shared Context
None named by the plan for this pass beyond the Test Boundaries section already copied above.

## Execution Log

### 2026-09-11 — test-engineer

**1. Audit of `dsh_tui/tests/test_scroll.py` (Task 01's own unit tests) against `dsh_tui/chat_ui.py`**

Read all 18 developer-written tests side by side with `_max_scroll_offset`, `scroll_up`/`scroll_down`/`scroll_to_top`/`scroll_to_bottom`, `_visible_messages`, the four snap-to-bottom call sites, and `render_messages`' footer branch. Verdict: the claimed coverage is real, not just claimed — clamping at both ends and the `bool` return are exercised for all four scroll methods (`test_scroll_up_clamps_at_top_and_reports_change`, `test_scroll_down_clamps_at_bottom_and_reports_change`, `test_scroll_to_top_and_bottom_report_change_only_once`), `_visible_messages()` slice correctness is checked at offset 0 (byte-identical to `list(ui.messages)[-display_messages:]`), mid-offset, and max-offset, all four mutating methods (`add_message`/`add_tool_call`/`add_tool_result`/`clear_messages`) are each independently confirmed to reset `_scroll_offset`, and the footer text is asserted for bottom/scrolled/no-overflow. No misunderstanding baked into both code and test was found — the numbers in the mid/max-offset tests were hand-computed against the actual slicing formula, not just re-derived from the implementation's own arithmetic.

Two real, if minor, gaps found and fixed (added as new test functions, no existing assertions touched):
- No test exercised the zero-messages case (`_max_scroll_offset`/`_visible_messages`/all four scroll methods called with an empty `deque`) — added `test_scroll_with_no_messages_is_a_noop`.
- The `pages` parameter on `scroll_up`/`scroll_down` (used internally with `pages=1` by every real call site, but a public part of the API per the ticket's `scroll_up(pages=1)` signature) was never called with `pages != 1` anywhere, including its own clamping — added `test_scroll_up_down_with_multiple_pages`.

`dsh_tui/tests/test_scroll.py`: 18 → 20 tests.

**2. Pty-based integration test — achieved, after two real bugs found and fixed in the test harness itself**

Added `dsh_tui/tests/test_scroll_pty.py` (2 tests) driving real `\e[5~`/`\x15`/`\e[4~` byte sequences through the actual `_input_with_history_unix` read loop via `pty.openpty()`, in a background thread, with `sys.stdin`/`sys.stdout` redirected to the pty slave fd.

This was not a clean first attempt — two real, reproducible issues surfaced along the way, both instructive about the exact raw-mode risk this pass was scoped to probe:

- **Canonical-mode race**: the pty slave starts in cooked/canonical mode. Writing test bytes to the master before the reader thread's own `tty.setraw(fd)` executes races the line discipline buffering them pending a newline. Fixed by pre-arming raw mode on the slave fd in the `pty_pair` fixture before any bytes are written (the reader thread's own `tty.setraw` call is then idempotent, so nothing under test is skipped).
- **`sys.stdout` is process-global, not thread-local, and nothing was draining the pty master** — reassigning it inside the background reader thread also redirected the *test process's own* prints (this is what silently ate my own debug output while diagnosing the first issue), and because nothing ever read the master side, the reader thread's writes (`console.clear()`, prompt escapes, redraw lines) eventually filled the pty's kernel output buffer and blocked forever on the next `write()` — indistinguishable from a real deadlock in the code under test. This is the exact failure mode the coordinator's watchdog caught (a prior run of this suite hung for 10 minutes with no output). Fixed with a background drain thread (`_start_drain`) that continuously reads and discards the master side for the duration of each test.
- **A third, genuine and useful finding, not a test bug**: `_input_with_history_unix`'s own first statement is `tty.setraw(fd)` with no explicit `when=`, which defaults to `TCSAFLUSH` — this discards any input already queued *before that call runs*, by design of the stdlib default (confirmed by direct reproduction: bytes written to the master before the reader thread starts are silently gone). This is orthogonal to the risk this ticket flagged (Task 01's own re-entrant `_repaint_with_input` correctly uses `TCSADRAIN`, not the default) — it only affects the very first `setraw` call, once, before any of this feature's code runs — but it's worth recording: a caller of `_input_with_history_unix` that pre-seeds stdin (e.g., piping a scripted input file) would lose the first chunk. Not in this ticket's scope to fix; flagging for tech-lead in case it's worth a follow-up ticket. Adjusted both tests to write their driving bytes ~200ms after thread start (a comfortable margin past the microsecond-scale initial `setraw`) rather than racing it.

Final two tests, run 5x in a row with no flakes (`for i in 1 2 3 4 5; do ... pytest test_scroll_pty.py -q; done` → `2 passed` every time, 0.49–0.60s each):
- `test_pgup_ctrlu_end_scroll_without_leaking_tilde`: drives PgUp (`\e[5~`) → Ctrl+U (`\x15`) → End (`\e[4~`) through the real loop, polling `ui._scroll_offset` after each, then types `"ok\r"` and asserts the submitted result is exactly `"ok"` — confirming the trailing `~` of the escape sequences does not leak into the input buffer (the exact class of bug the ticket calls out: `\e[20~`/F9 leaking `~`, `chat_ui.py:1414`).
- `test_typeahead_survives_scroll_repaint_tcsadrain`: writes `\e[5~` + `"ok\r"` as one burst (after the initial `setraw` has run), so `"ok\r"` is still unread in the kernel queue when the PgUp branch's `_repaint_with_input` calls `termios.tcsetattr(fd, termios.TCSADRAIN, ...)` mid-stream, and asserts the submitted result is still `"ok"` — a real regression test for the `TCSADRAIN`-not-`TCSAFLUSH` requirement Task 01's Established section called out as "the single most likely way to get this feature visibly wrong." If a future change reverted to the `tty.setraw` default here, this test would fail with a truncated/empty result instead of passing on code-reading alone.

**3. `test_replay.py` regression check**

`PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests/test_replay.py -q` → `4 passed` (unmodified, unaffected — no changes made to this file).

**4. Task 2 cap verification** (explicitly left unverified by Task 02's developer, blocked on Task 01 landing)

Scripted check: `ChatUI(display_messages=5, show_logs=False, max_messages=1000)`, appended 150 messages directly to `ui.messages` (bypassing `add_message`'s scroll reset, matching the existing test helper's pattern), then:
```
len(messages): 150
_max_scroll_offset: 145
visible at top: ['m0', 'm1', 'm2', 'm3', 'm4']
m0 still present: True
CAP VERIFICATION OK
```
`_max_scroll_offset()` correctly reaches 145 (150 - 5) rather than clamping at the old default-100 ceiling, `scroll_to_top()` + `_visible_messages()` reaches all the way back to `m0` (the very first message), and `ui.messages[0]` is confirmed to still be `m0` rather than evicted (with the old `max_messages=100` default this would have been evicted once message 101 arrived). Confirmed `dsh_tui/cli.py:81` has `max_messages=1000,` in the live `ChatUI(...)` construction. Not added as a permanent test file per the ticket's Non-Goals ("Writing new tests for Task 02... already covered by Task 01's clamping tests plus the live run per the plan") — this was a one-off verification script, not committed.

**5. Full suite, run fresh**

```
$ PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q
..........................                                               [100%]
26 passed in 0.66s
```
26 = 4 original (`test_replay.py`) + 18 original (`test_scroll.py`) + 2 new gap-fix tests (`test_scroll.py`) + 2 new pty tests (`test_scroll_pty.py`).

**Files touched**: `dsh_tui/tests/test_scroll.py` (2 new test functions added, 18 → 20; no existing assertions changed), `dsh_tui/tests/test_scroll_pty.py` (new file, 2 tests). No production code touched.

**Acceptance criteria**
- [x] Gap audit of `dsh_tui/tests/test_scroll.py` completed and reported — 2 minor gaps found and fixed (empty-message-list case, `pages` parameter)
- [x] Pty-based integration test added and passing — achieved, 2 tests, 5/5 clean reruns, after fixing two real test-harness bugs (canonical-mode race, undrained pty causing a real hang — this is what the coordinator's watchdog caught)
- [x] `test_replay.py` confirmed passing, unaffected
- [x] Full suite run with real output reported: 26 passed

### Review Feedback

**2026-09-11 — tech-lead (Review mode, R1 pass)**

Independently reran the suite in a fresh process: **26 passed**, matching the reported result. Read both new
test files rather than trusting the report.

**Verified clean (no finding):** the pty tests are real integration tests, not assertion-theatre — they drive
actual `\e[5~` / `\x15` / `\e[4~` bytes through the unmodified `_input_with_history_unix` loop, assert the
resulting `_scroll_offset` transitions (5 → clamped 7 → 0), and assert no stray `~` leaked into the submitted
string. The two harness bugs documented in the Execution Log (canonical-mode race, undrained pty master) were
genuine test-harness defects, correctly diagnosed, and the fixes are the right ones. The `TCSAFLUSH`
observation about `_input_with_history_unix`'s own first `tty.setraw(fd)` is confirmed accurate — see the
disposition note below.

```
id: R3-2; severity: R3; primary_file/symbol: dsh_tui/tests/test_scroll_pty.py :: test_typeahead_survives_scroll_repaint_tcsadrain;
title: type-ahead coverage only exercises bytes queued *before* the repaint window, not bytes arriving *during* it;
impact: the R2-1 defect on ticket 01 (echoed and VKILL-destroyed keystrokes) passes this suite untouched;
owner_agent: test-engineer; confidence: high
```
- **root_cause**: the test writes `\e[5~` + `ok\r` as a single burst before `_repaint_with_input` runs, so the
  trailing bytes are already sitting in the kernel queue when the cooked window opens. The tty line discipline
  applies echo and `VKILL` at *processing* time, so pre-queued bytes are immune — which is precisely why this
  test is green while the defect is live.
- **file_paths**: `/home/bertie/Documents/projects/dsh-tui/dsh_tui/tests/test_scroll_pty.py`.
- **evidence**: reproduced both behaviors in a standalone pty harness; full detail in ticket 01's R2-1 entry.
  Writing `hello` + `\x15` *after* the window opens leaves the app reading `b''`.
- **recommended_fix**: **after** developer's R2-1 fix lands, add a third pty case that writes into the window
  itself — e.g. patch/wrap `_print_frame` on the instance to sleep ~50 ms, write `abc` + `\x15` from the master
  during that sleep, then assert the loop still reads all four bytes and that nothing was echoed to the master
  during the repaint. Not blocking on its own; should land with the R2-1 retry rather than as a separate pass.

```
id: R5-3; severity: R5; primary_file/symbol: dsh_tui/tests/test_scroll.py :: footer tests;
title: no test asserts that the rendered message *content* follows the scroll offset;
impact: `render_messages` could stop calling `_visible_messages()` and only the footer assertions would catch it;
owner_agent: test-engineer; confidence: high
```
- **recommended_fix**: one extra assertion in `test_footer_when_scrolled` — e.g. `"m2" in text` and
  `"m11" not in text` — closes the render↔slice loop end to end. Informational.

**Disposition of your `tty.setraw(fd)` / `TCSAFLUSH` finding (tech-lead's call, as asked):** accurate and
correctly reproduced, but **not** an R-severity finding for this feature. It fires exactly once, on entry to a
prompt the user is not yet typing at, before any scroll code exists in the call stack, and it is baseline
behavior at `49b5948` — not a regression. Recorded here as a documented non-blocking note; if it ever does bite
(e.g. paste arriving at the instant the prompt opens), it is a one-word fix (`tty.setraw(fd, termios.TCSADRAIN)`)
and deserves its own ticket rather than being smuggled into this feature's diff. Good catch; right instinct to
escalate rather than silently fix it.

### 2026-09-11 — test-engineer (R3-2 follow-up: in-window repaint regression test)

Read the fixed `_repaint_with_input` (`dsh_tui/chat_ui.py:1268-1305`): it no longer restores full cooked
termios for the print window. It now does `raw_attrs = termios.tcgetattr(fd)`, ORs only `OPOST|ONLCR` onto a
copy for the `_print_frame()` call, then restores `raw_attrs` verbatim afterward — ECHO/ICANON/ISIG/IXON stay
off (as `raw_attrs` already has them off) for the entire window, which is the R2-1 fix. Signature also dropped
the now-unused `old_settings` parameter; not called directly by any test, so no test-side breakage.

Added `test_bytes_arriving_inside_repaint_termios_window_survive` to `dsh_tui/tests/test_scroll_pty.py`
(3rd test in that file). This closes R3-2: the existing `test_typeahead_survives_scroll_repaint_tcsadrain`
only covers bytes queued *before* the repaint starts, not bytes arriving *during* the ~3-5ms window R2-1's bug
lived in.

A real wall-clock window that short can't be hit reliably from a test process by sleeping and hoping — either
too flaky or requires an artificial slowdown that wouldn't reflect the real timing. Instead of guessing at
timing, the test hooks the exact seam that *defines* the window: `_print_frame()` is the only thing that runs
between `_repaint_with_input`'s two `tcsetattr` calls, so the test monkeypatches `ui._print_frame` to write a
burst (`b"xy\x15z\r"`, includes a Ctrl+U — the exact character R2-1's own repro used) to the pty master
synchronously, before delegating to the original `_print_frame`. Because the write happens inside the call
that the window brackets, the bytes are guaranteed to be sitting unread in the kernel queue while the
cooked-output attributes are active, by construction rather than luck. Home (`\e[1~`) is used to trigger the
first repaint (not PgUp) so the injected Ctrl+U lands with the view already at max scroll offset — `scroll_up()`
there is a no-op, avoiding a nested repaint from complicating the assertion. Asserted the final submitted input
is exactly `"abxyz"` (all four injected bytes plus the two typed before scrolling survive, with `\x15` correctly
consumed by the app's own Ctrl+U-scroll binding rather than inserted as text) and that `_scroll_offset` stayed
at 7 (the Ctrl+U didn't itself trigger a spurious repaint). A pre-fix repro (full-cooked-restore) would have
shown `"abz"` or similar — `"xy"` eaten by the kernel's own VKILL processing before the app ever saw it.

Ran the 3 pty tests 3x in a row: `3 passed` every time (0.70-0.86s each), no flakes.

Full suite, fresh: `timeout 60 env PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q`
→ `27 passed in 0.84s` (26 prior + 1 new).

`dsh_tui/tests/test_scroll.py`'s R5-3 (footer-scrolled test doesn't assert rendered message *content* follows
the offset) is noted but out of scope for this dispatch — not requested by this round's instructions; flagging
it stays open for a future test-engineer pass or explicit request.

Files touched this round: `dsh_tui/tests/test_scroll_pty.py` (1 new test added, 2 → 3; no existing assertions
changed). No production code touched.

**2026-09-11 — tech-lead (Review mode, closure re-check)**

**R3-2: CLOSED.** The `_print_frame` injection seam is the right call and better than the sleep-based approach I
suggested — `_print_frame()` is by definition the only thing running between the two `tcsetattr` calls, so the
burst is inside the window by construction rather than by timing luck. Verified it actually discriminates: with
the pre-fix cooked window reintroduced in a scratch copy, the test fails with `abz` (VKILL ate `xy`), exactly as
the docstring predicts; with the fix it passes. Five consecutive full-suite runs in a fresh process: **27 passed**,
no flake. R5-3 (assert rendered content follows the offset) remains open and non-blocking.
