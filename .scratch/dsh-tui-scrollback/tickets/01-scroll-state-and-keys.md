# 01: Scroll state, scroll-aware render, and key dispatch

**Repo:** /home/bertie/Documents/projects/dsh-tui
**Blocked by:** None (can start immediately, in parallel with Task 02)
**Status:** ready-for-agent

## Files
- dsh_tui/chat_ui.py
- dsh_tui/tests/test_scroll.py (new)

## Action

1. **State:** add `self._scroll_offset = 0` beside `self.display_messages` in `__init__` (`chat_ui.py:119`). Offset counts messages scrolled back from the newest; `0` = bottom = today's behavior.
2. **Pure, testable scroll API** on `ChatUI`: `_max_scroll_offset()` → `max(0, len(self.messages) - self.display_messages)`; `scroll_up(pages=1)`, `scroll_down(pages=1)`, `scroll_to_top()`, `scroll_to_bottom()`, each clamping and **returning `bool` — whether the offset actually changed** (the input loop uses this to skip the repaint at the ends, which is acceptance criterion 2); `_visible_messages() -> list` returning the offset slice.
3. **Render:** `render_messages` (`:382`) uses `_visible_messages()` instead of the hardcoded `list(self.messages)[-self.display_messages:]` (`:395`). Make the `else` subtitle branch (`:421`) scroll-aware per acceptance criterion 3. Leave the `self.data_path` branch (`:417`) untouched — verified dead in this repo (`data_path` is only ever assigned `""` at `:159`).
4. **Snap-to-bottom:** a one-line `self._scroll_offset = 0` (via a small private helper) in `add_message`, `add_tool_call`, `add_tool_result`, and `clear_messages`.
5. **Repaint helper:** extract the frame sequence `get_user_input` already performs (`:1574-1576`, `:1581-1586`) — `console.clear()` → `console.print(self.render())` → centered disclaimer → blank line — into one private `_print_frame()`, and call it from *both* `get_user_input` and the new scroll repaint, so the scrolled view cannot drift from the normal view. Then `_repaint_with_input(fd, old_settings, prompt, current_input, cursor_pos)`: restore cooked termios → `_print_frame()` → `tty.setraw(fd, termios.TCSADRAIN)` → `_redraw_line(prompt, current_input, prev_lines=1)` (`prev_lines=1` because the screen was just cleared) → restore the caret to `cursor_pos`. Return the new `num_display_lines`.
6. **Cursor restore rule (make it deterministic, not best-effort):** if the text after the caret contains no `'\n'`, emit `\033[{n}D` with `n = len(current_input) - cursor_pos` — the same arithmetic `_handle_printable` (`:1309-1311`) already uses. If it does contain a newline (multi-line paste buffer), leave the caret at the end of the buffer and set `cursor_pos = len(current_input)`, documented in the docstring. Do not attempt multi-line caret math in v1.
7. **Key dispatch in `_input_with_history_unix`:** PgUp `\e[5~` / PgDn `\e[6~` as a new `elif next2 in ('5', '6')` branch that consumes the trailing `~` exactly the way `_handle_delete_key` does (`:1250-1254`) before acting; Home/End accepting both `\e[1~`/`\e[4~` (consume `~`) and `\e[H`/`\e[F` (no trailing byte), falling through to `_drain_escape_sequence` on anything else (e.g. modified `\e[1;5H`). Ctrl+U `\x15` and Ctrl+F `\x06` as top-level `elif char ==` branches placed before the printable test. Each calls its scroll method and repaints only if it returned `True`. Add a short code comment recording *why* Ctrl+B and Ctrl+D are not used (tmux prefix; existing EOF binding) so a future reader does not "fix" it.
8. **Own unit tests** (`dsh_tui/tests/test_scroll.py`, per `rules/agents.md` Operating Principle 3): offset clamping at both ends and the `bool` return; `_visible_messages()` slice correctness at offset 0 / mid / max; offset 0 producing today's exact slice; each of `add_message`/`add_tool_call`/`add_tool_result` resetting the offset; footer text asserted for the bottom, scrolled, and no-overflow cases (render into a `Console(file=StringIO(), width=100)` assigned onto the instance, or `console.capture()`); `display_messages` larger than `len(messages)` (offset always 0). No pty needed for any of these — keep the raw-byte dispatch thin enough that the logic under test is pure.

## Established (verified — don't re-derive)

- `self.messages` is `deque(maxlen=max_messages)` with `max_messages=100` (`:118`, `:88`) — **not** unbounded. `cli.py` does not override it, and it runs with `show_logs=True`, so each tool-using turn appends `tool_call` + `tool_result` messages too; the real scrollback ceiling today is ~25 tool-heavy turns. That is what Task 02 addresses.
- `render_messages` hardcodes the tail slice at `:395`; footer subtitle at `:421`; `data_path` branch dead (`:159`).
- PgUp/PgDn/Home/End currently land in the catch-all `else` → `_drain_escape_sequence` (`:1431-1435`), i.e. they are silently swallowed today — adding them regresses nothing. Sequences confirmed against the live terminfo entry for `TERM=tmux-256color`: `kpp=\E[5~`, `knp=\E[6~`, `khome=\E[1~`, `kend=\E[4~`.
- Ctrl+B `\x02` and Ctrl+F `\x06` are unbound in the loop and silently dropped (below `' '`, so no branch matches). Ctrl+D `\x04` raises `EOFError` (`:1370`). tmux global `prefix C-b`, `prefix2 None`, `mouse off`; `tmux list-keys -T root` has no PageUp/PPage binding, so bare PgUp/PgDn do reach the app.
- **`tty.setraw` clears `OPOST`** (read from the stdlib `tty` source: `cfmakeraw` does `mode[OFLAG] &= ~OPOST`). A bare `\n` emitted by Rich while the loop holds raw mode therefore does **not** carriage-return — output stair-steps diagonally. This is why the repaint must restore cooked termios around the Rich prints; it is the single most likely way to get this feature visibly wrong.
- **`tty.setraw(fd)` defaults to `when=TCSAFLUSH`**, which *discards pending input*. Re-entering raw mode after each repaint with the default would silently drop type-ahead — use `TCSADRAIN` (the value the existing `finally` already uses at `:1451`).
- `_redraw_line` (`:1173`) already redraws prompt + multi-line buffer and is raw-safe (explicit `\r\n`); `_handle_arrow_keys` is its existing caller. Reuse it, do not write a second one.
- `render()` (`:1139-1145`) prints the logs panel as a side effect when `show_logs` is true (it is, from `cli.py`) and returns the messages panel — so any repaint must call `render()`, not `render_messages()`, to keep the frame identical.
- Baseline suite is green: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` → 4 passed.
- The MVP-era "no rendering/behavior changes" constraint was Task 2's non-goal in `PLAN.md` for the *initial port only*; no live reason found for it to bind follow-on feature work. The Apache-2.0 adaptation notice at the top of `chat_ui.py` (§4(b), items 1-2) is a different obligation and **does** bind: add a third numbered item recording this change.

## Must Verify

- Run the real client in tmux and confirm: PgUp/PgDn/Ctrl+U/Ctrl+F/Home/End scroll without stair-stepped output, the in-progress input line and caret survive a repaint, and keys typed *during* a repaint are not lost (the `TCSAFLUSH` trap).
- Confirm a scroll key at the top/bottom limit produces no repaint at all.

## Unknowns
Whether some other terminal the user may use emits `\e[5;2~`-style modified PgUp — handled by design (unrecognized tail drains as today), not worth probing.

## Dependencies/license
None new. `termios`/`tty`/`select` are stdlib and already imported; no new package, so no transitive or license risk. Apache-2.0 notice update as above.

## Symbols
`ChatUI.__init__`, `render_messages`, `_render_welcome_panel`, `render`, `get_user_input`, `_input_with_history_unix`, `_redraw_line`, `_handle_arrow_keys`, `_handle_delete_key`, `_drain_escape_sequence`, `_handle_printable`, `add_message`, `add_tool_call`, `add_tool_result`, `clear_messages`.

## Acceptance Criteria
- [ ] At the `➤` prompt, PgUp/Ctrl+U moves the visible window one screen back through `self.messages`; PgDn/Ctrl+F moves forward; End jumps to newest; Home jumps to oldest
- [ ] Scrolling is clamped: no offset past the oldest message, never below zero; pressing a scroll key at either end is a no-op that does **not** repaint (no flicker)
- [ ] The chat panel footer states the visible range, the total, and — when scrolled — that you are scrolled and which key returns to latest
- [ ] A new message (user, assistant, tool call, tool result) resets the view to the bottom
- [ ] Typing does not reset the view; the in-progress input line and cursor position survive a scroll repaint intact
- [ ] With no scrolling performed, rendering output is byte-identical to today's
- [ ] `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` passes (baseline today: 4 passed)
- [ ] `_input_with_history_windows` untouched
- [ ] No new module-level dependency
- [ ] Apache-2.0 adaptation notice updated with a third numbered item recording this change

## Non-Goals
- Line/height-aware scrolling (v1 windowing stays message-count based; nothing in the render path consults `self.console.size`)
- Mouse-wheel support (deferred; document as a growth-table candidate, not developer's job)
- Scrolling during an in-flight turn (the prompt is the only scrollable state)
- A modal/copy-mode
- Persisting scroll position across turns
- Windows (`_input_with_history_windows`) parity
- Any change to `driver.py`, `scripts/`, or `settings.yaml.template`
- No `console.size` use anywhere, no refactor of the escape-sequence parser beyond the new branches

## Security-sensitive
no — terminal state and in-memory message slicing only; no I/O beyond stdin/stdout, no credential, network, or filesystem path touched.

## Shared Context
None — no other task depends on shared infrastructure this task produces (Task 02 is a disjoint one-line change to `cli.py` with no code dependency on this ticket).

## Execution Log

### 2026-09-11 — developer

Implemented as specified in `dsh_tui/chat_ui.py`:
- `__init__`: added `self._scroll_offset = 0` beside `self.display_messages`.
- New scroll API: `_max_scroll_offset`, `_reset_scroll` (private snap helper), `scroll_up`, `scroll_down`, `scroll_to_top`, `scroll_to_bottom` (all return `bool`), `_visible_messages`.
- `render_messages`: now calls `_visible_messages()` instead of the hardcoded tail slice; the `else` subtitle branch is scroll-aware — unchanged string at offset 0 (`"Showing last N of M messages"`), a `"Showing S-E of M messages — scrolled, End to jump to latest"` string when scrolled. `self.data_path` branch left untouched.
- Snap-to-bottom: added `self._reset_scroll()` calls at the end of `add_message`, `clear_messages`, `add_tool_call`, `add_tool_result`.
- Added `_print_frame()` (extracted from `get_user_input`'s repeated clear/render/disclaimer/blank-line sequence) and wired both occurrences in `get_user_input` through it (first occurrence now a single call; second keeps its own `flush_corrections()` placement, since that ordering differs from the first and isn't part of the shared sequence).
- Added `_repaint_with_input(fd, old_settings, prompt, current_input, cursor_pos) -> tuple[int, int]`: restores cooked termios (`TCSADRAIN`) → `_print_frame()` → `tty.setraw(fd, termios.TCSADRAIN)` → `_redraw_line(prompt, current_input, prev_lines=1)` → caret restore. Returns `(num_display_lines, cursor_pos)` rather than just the line count the ticket's prose mentioned — the multi-line-paste branch has to hand the caller a new `cursor_pos` (`len(current_input)`) since Python has no out-parameters and the caller's `cursor_pos` is a plain local int; every call site immediately does `num_display_lines, cursor_pos = self._repaint_with_input(...)`. Documented this in the docstring. Judgment call — flagged for tech-lead review in case a different shape was intended, but I couldn't find a way to satisfy "leave the caret at the end of the buffer and set cursor_pos" otherwise.
- Key dispatch in `_input_with_history_unix`: added top-level `elif char == '\x15'` (Ctrl+U → `scroll_up`) and `elif char == '\x06'` (Ctrl+F → `scroll_down`) before the escape-sequence branch, each repainting only if the scroll call returned `True`. Added a comment on the Ctrl+F branch recording why Ctrl+B/Ctrl+D aren't used (tmux prefix key; existing EOF binding). Added `elif next2 in ('5', '6')` (PgUp/PgDn, consumes trailing `~` the same way `_handle_delete_key` does, falls back to `_drain_escape_sequence` on a non-`~` tail e.g. modified `\e[5;2~`), `elif next2 in ('1', '4')` (Home/End with trailing `~`, same fallback), and `elif next2 in ('H', 'F')` (Home/End with no trailing byte) — all before the existing catch-all `else` → `_drain_escape_sequence`.
- Apache-2.0 adaptation notice: added item 3 recording the scrollback feature.
- `_input_with_history_windows` not touched.

Tests: `dsh_tui/tests/test_scroll.py` (new, 18 tests) covering: `_max_scroll_offset` at 0/under/over display_messages; `scroll_up`/`scroll_down`/`scroll_to_top`/`scroll_to_bottom` clamping and `bool` return at both ends; `_visible_messages` slice correctness at offset 0 (matches today's exact slice)/mid/max, and when `display_messages` exceeds message count; `_reset_scroll` firing from `add_message`/`add_tool_call`/`add_tool_result`/`clear_messages`; footer text at bottom, scrolled, and no-overflow. All render tests use `Console(file=StringIO(), width=100)` assigned onto the instance. No pty used anywhere.

Test run: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` → `22 passed` (baseline 4 + 18 new).

Must-verify: could not run the real client in a live tmux session (non-interactive agent, no pty/terminal available in this environment) — verified by code reading instead, per the assignment's fallback instruction:
- Cooked-terminal restore wraps every Rich print during a repaint: confirmed — `_repaint_with_input` calls `termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)` *before* `_print_frame()` (which does all the `console.clear()`/`console.print()` calls) and only calls `tty.setraw` again after `_print_frame()` returns.
- `TCSADRAIN` used everywhere raw mode is re-entered: confirmed — the only re-entry point after the initial `tty.setraw(fd)` at the top of `_input_with_history_unix` is inside `_repaint_with_input`, which uses `tty.setraw(fd, termios.TCSADRAIN)` explicitly (not the `TCSAFLUSH` default).
- A scroll key at either clamp limit does not repaint: confirmed by code inspection — every one of the seven new call sites (`\x15`, `\x06`, `next2 in ('5','6')`, `next2 in ('1','4')`, `next2 in ('H','F')`) guards the `_repaint_with_input` call with `if changed:` / `if self.scroll_up():` etc., and `changed` is exactly the `bool` `scroll_up`/`scroll_down`/`scroll_to_top`/`scroll_to_bottom` return, which is `False` whenever the offset didn't move (unit-tested directly).

No other files touched. `cli.py` untouched (Task 02's file).

### Review Feedback

**2026-09-11 — tech-lead (Review mode, R1 pass)**

Reviewed `git diff 49b5948` for `dsh_tui/chat_ui.py` plus untracked `dsh_tui/tests/test_scroll.py`.
Independently reran the suite (fresh process): **26 passed**.

**Verified clean (no finding):**
- Acceptance criteria 1, 2, 3, 4, 6, 7 all met. Criterion 5 partially — see R2-1.
- Offset math correct at every boundary I checked by hand: `total=120, display=15, offset=15` → footer
  `91-105 of 120`, slice `msgs[90:105]` — consistent, no off-by-one. `offset==0` returns the byte-identical
  `msgs[-display:]` and the byte-identical original footer f-string, so the not-scrolled path is a true no-op.
- Snap-to-bottom covers **every** append site: `add_message`, `add_tool_call`, `add_tool_result`,
  `clear_messages` are the only four places anything is appended to `self.messages` (re-grepped post-diff),
  and `stream_end` reaches `self.messages` via `add_message("assistant", …)` — so assistant answers both land
  in scrollback and snap the view. `update_last_message`/`verification_correction` mutate in place, correctly
  no reset.
- `TCSADRAIN` honored on raw re-entry (`tty.setraw(fd, termios.TCSADRAIN)`), not the `TCSAFLUSH` default.
- Clamp-limit no-repaint: all five new call sites gate on the `bool` return. Confirmed.
- Escape-parser additions consume exactly the right byte count; `\e[15~`-style F-keys still fall through to
  `_drain_escape_sequence` (which breaks on `~`), so the `\e[20~`/F9 class of leak is not reintroduced.
- Apache-2.0 §4(b) adaptation notice item 3 added. `_input_with_history_windows`, `driver.py`, `scripts/`
  untouched. No new dependency (stdlib `termios`/`tty` already imported).

**Deviation reviewed and ACCEPTED:** `_repaint_with_input` returning `(num_display_lines, cursor_pos)` instead
of just the line count. The reasoning in the Execution Log is correct — the multi-line-paste caret rule the plan
specified ("leave the caret at end of buffer and set `cursor_pos = len(current_input)`") cannot reach the
caller's local `int` any other way. All five call sites destructure both values. No change requested.

---

```
id: R2-1; severity: R2; primary_file/symbol: dsh_tui/chat_ui.py :: _repaint_with_input;
title: restoring full cooked termios during the repaint lets the line discipline echo — and destroy — keystrokes typed while the frame redraws;
impact: keys arriving inside the repaint window are echoed as literal garbage into the frame being drawn, and an auto-repeated Ctrl+U is interpreted by the line discipline as VKILL, silently erasing input already queued but not yet read;
owner_agent: developer; confidence: high (reproduced directly, twice)
```

### 2026-09-11 — developer (R2-1 fix)

Fixed `_repaint_with_input` in `dsh_tui/chat_ui.py`. It no longer restores the full cooked `old_settings` to get `OPOST`/line-break rendering back for the Rich print. Instead:
- reads the currently-active raw termios attrs via `termios.tcgetattr(fd)` (raw mode is already in effect at every call site — set once at the top of `_input_with_history_unix`, never left in between),
- builds a copy with `OPOST | ONLCR` OR'd onto the output-flags field (index 1) only,
- applies that copy with `TCSADRAIN`, calls `_print_frame()`, then restores the exact raw attrs it read at the start (also `TCSADRAIN`).

`ECHO`/`ICANON`/`ISIG`/`IXON` are never touched, so they stay off for the whole repaint window — no echo, no line-discipline interpretation of control chars (VKILL etc.), input-only correctness is unaffected, and `\n` still renders as `\r\n` because `OPOST`/`ONLCR` are the only bits that changed.

Also dropped the now-unused `old_settings` parameter from `_repaint_with_input`'s signature (it no longer needs the caller's saved cooked settings for anything) and updated all five call sites accordingly. `old_settings` itself is untouched at its original two uses (captured at raw-mode entry, restored in the `finally` block on function exit) — this fix only changes what the mid-loop repaint does.

**Verification:**
1. Adapted tech-lead's own `vkill_probe.py` repro (found already in the shared scratchpad) into a fixed-logic variant (`vkill_probe_fixed.py`, run once, then deleted — tech-lead's original probes were left alone) applying the same OPOST|ONLCR-only patch to a `pty.openpty()` pair with `hello` + `\x15` (Ctrl+U) written during the cooked window, followed by a `FRAME\n` print:
   - Before (tech-lead's original probe, full-cooked restore): `app reads after window: b''` — confirms the bug: VKILL wiped the queued `hello`.
   - After (this fix's OPOST|ONLCR-only approach): `app reads after window: b'hello\x15'` and `terminal showed: b'FRAME\r\n'` — input survives byte-for-byte (including the literal `\x15`, since ICANON/ISIG stay off so it's ordinary data, not a control action) and the frame still renders with a correct `\r\n`, not stair-stepped.
2. Also ran a `pty.fork()`-based end-to-end repro driving real bytes through `ChatUI._input_with_history_unix` itself (`hello` + 5×(`\x15` + literal char) + `\r`): returned `'helloXXXXX'` — every byte survived, no echo artifacts, no truncation.

Full suite: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` → **26 passed** (unchanged from tech-lead's own pre-fix count — this was a pure implementation fix inside `_repaint_with_input`/its call sites, no test file touched, no behavior visible to the existing unit/pty test suite changed).

No other files touched.

- **root_cause**: `_repaint_with_input` restores the *entire* pre-raw termios state
  (`termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)`) in order to get back `OPOST`/`ONLCR` so Rich's bare
  `\n` carriage-returns. But `old_settings` also carries `ECHO`, `ICANON`, `ISIG` and `IXON`. For the few
  milliseconds `_print_frame()` runs, the terminal is therefore a full cooked line editor again. Only `OPOST` was
  actually needed.
- **file_paths**: `/home/bertie/Documents/projects/dsh-tui/dsh_tui/chat_ui.py` (`_repaint_with_input`, the
  `tcsetattr(fd, TCSADRAIN, old_settings)` line and its paired `tty.setraw`).
- **evidence** (pty reproductions, run against this exact sequence, not inferred):
  1. Keys arriving *during* the cooked window are echoed:
     `setraw → tcsetattr(old) → write \e[5~abc to master → write "FRAME\n" → setraw` →
     the terminal side saw `b'^[[5~abcFRAME\r\n'`. The `^[[5~abc` is echo smeared into the frame being painted.
     (Keys queued *before* the window are safe — echo is applied at processing time — which is exactly why the
     existing `test_typeahead_survives_scroll_repaint_tcsadrain` passes and does **not** cover this.)
  2. Worse, input is destroyed, not just echoed: same sequence, writing `hello` then `\x15` into the window →
     the app read `b''` afterwards, and the terminal showed `hello` followed by five `\x08 \x08` erases. `\x15`
     is `VKILL` in canonical mode, so the queued `hello` was killed by the line discipline before the loop could
     read it. Ctrl+U is one of this feature's own scroll keys, so key auto-repeat reaches this on ordinary
     "hold the key to scroll back fast" usage. `_print_frame()` measures median 2.7 ms / max 5.4 ms against a
     StringIO console (real tty writes are slower), against a ~33 ms key-repeat period — so the window is hit
     regularly over a held scroll, not theoretically.
- **recommended_fix**: do not restore `old_settings` mid-loop. Flip only output post-processing back on for the
  duration of the print and leave `ECHO`/`ICANON`/`ISIG`/`IXON` off. Verified working in the same pty harness
  (output came back as `LINE1\r\nLINE2\r\n`, no echo, queued input intact):
  ```python
  raw_settings = termios.tcgetattr(fd)
  post = termios.tcgetattr(fd)
  post[1] |= termios.OPOST | termios.ONLCR      # index 1 == OFLAG
  termios.tcsetattr(fd, termios.TCSANOW, post)
  self._print_frame()
  termios.tcsetattr(fd, termios.TCSADRAIN, raw_settings)
  ```
  `old_settings` then becomes unused by this method — dropping the parameter (and updating the five call sites)
  or keeping it for symmetry is the developer's call; if kept, say in the docstring why it is unused. The
  docstring's existing OPOST explanation should be updated to say *only* OPOST is restored, and why.

---

```
id: R3-1; severity: R3; primary_file/symbol: dsh_tui/chat_ui.py :: get_user_input;
title: the post-submit repaint still hand-rolls the frame sequence _print_frame() now owns;
impact: the drift guard the helper was introduced for is only half-closed — a future change to the frame updates one of the two copies; owner_agent: developer; confidence: high
```
- **root_cause**: `_print_frame()` was correctly extracted and used for the pre-input frame, but the second
  repaint inside the `try` (after `add_message("user", …)`) still does `console.clear()` → `console.print(render())`
  → disclaimer → `flush_corrections()` → blank line inline, because `flush_corrections()` has to be interleaved.
- **file_paths**: `/home/bertie/Documents/projects/dsh-tui/dsh_tui/chat_ui.py` (`get_user_input`).
- **evidence**: the two sequences are identical except for the `flush_corrections()` call sitting between the
  disclaimer and the trailing blank line.
- **recommended_fix**: non-blocking; either give `_print_frame` an optional callback/`trailing_blank` parameter,
  or leave as is with a one-line comment stating that the interleaved `flush_corrections()` is why it is not
  folded in. Do **not** reorder `flush_corrections()` to make it fit — its placement is deliberate.

---

```
id: R5-1; severity: R5; primary_file/symbol: dsh_tui/chat_ui.py :: render_messages (footer);
title: the footer names the scroll keys only once you are already scrolled; impact: discoverability rests
entirely on README; owner_agent: developer; confidence: high
```
- **recommended_fix**: when `_max_scroll_offset() > 0` and the offset is 0, append a short hint
  (e.g. `— PgUp to scroll back`) to the existing "Showing last N of M" footer. Informational only; the plan's
  acceptance criterion 3 only required the scrolled-state wording, which is met. `document-writer` covers the
  README half.

---

```
id: R5-2; severity: R5; primary_file/symbol: dsh_tui/chat_ui.py :: _input_with_history_unix (escape parser);
title: pre-existing — \eO-prefixed function keys (F1-F4 in application mode) leak their final byte as typed text;
impact: pressing F1 in some terminals types "P" into the prompt; owner_agent: developer; confidence: high
```
- **root_cause**: the parser branches only on `next1 == '['` with no `else`, so for `\eOP` the `O` is dropped and
  `P` is read on the next iteration as a printable character. Pre-existing (baseline `49b5948`), **not**
  introduced by this diff — raised only because this review touched that parser. Out of scope for this ticket;
  worth a follow-up line or a growth-table note, not a retry.

**Verdict: CHANGES REQUESTED — R2-1 blocks.** R3-1/R5-1/R5-2 are non-blocking and are not retry work.
Per this repo's surface-and-wait policy, no fix has been made; awaiting human approval before any retry.

**2026-09-11 — tech-lead (Review mode, closure re-check after R2-1 fix)**

**R2-1: CLOSED.** Verified three independent ways, not from the fix report:

1. *Code read of what actually landed.* `_repaint_with_input` no longer touches `old_settings` at all
   (grep confirms `old_settings` now appears only at its definition and in the loop's own `finally` restore, and
   none of the five call sites passes it). It reads the live raw attrs, copies with `list(...)` (so `raw_attrs`
   is not aliased — only field 1, an int, is mutated), ORs `OPOST | ONLCR` onto the OFLAG only, applies and
   restores with `TCSADRAIN` both ways. Note a bonus property: there is now no `tty.setraw` re-entry in this path
   at all, so the `TCSAFLUSH` hazard is structurally gone rather than merely parameterised away.
2. *Mutation test.* Reintroduced the exact defect in a throwaway copy under scratch (ORing
   `ICRNL|IXON` / `ECHO|ICANON|ISIG` back on for the window; repo working tree untouched) and ran the pty file
   against it: `test_bytes_arriving_inside_repaint_termios_window_survive` **fails** with `abz` — the predicted
   VKILL truncation, `xy` eaten by the canonical buffer — and passes against the real fix. The other two pty
   tests pass either way, which is precisely the coverage gap R3-2 was filed for. The new test is a genuine
   regression test, not a passing-by-construction one.
3. *Live pty exercise of the fixed function.* Drove the real `_repaint_with_input` against `pty.openpty()` with
   a real Rich console writing to the pty: queued `hello\x15` was still readable intact afterwards
   (`b'hello\x15'`), no echo of it leaked into the painted frame, and post-repaint attrs are back to raw
   (`ECHO=False ICANON=False OPOST=False`). Separately measured the frame's own line endings during the window:
   **16 LFs emitted, 16 CRLF, 0 bare LF** — so the OPOST/ONLCR half genuinely works and the stair-stepping this
   whole mechanism exists to avoid does not occur.

Suite rerun by me in a fresh process, five consecutive runs: **27 passed** every time, no flake.

```
id: R4-1; severity: R4; primary_file/symbol: dsh_tui/chat_ui.py :: _repaint_with_input (docstring);
title: the rewritten docstring dropped the return contract and the multi-line-paste caret rule;
impact: the reviewed deviation is now undocumented in the place a caller would look; owner_agent: developer;
confidence: high
```
- **root_cause**: the docstring was rewritten to explain the termios fix (a good explanation — keep it) but the
  previous `Returns: (num_display_lines, cursor_pos) — the caller must overwrite its own cursor_pos` paragraph
  and the "multi-line paste buffer is not supported in v1, caret is left at end of buffer" paragraph were
  dropped with it. The code still does both things.
- **recommended_fix**: restore those two paragraphs alongside the new termios explanation. Non-blocking, no
  retry required — fold into the documentation pass or any later touch of this function.

**Verdict: APPROVED.** No open R1/R2. R3-1, R4-1, R5-1, R5-2 remain non-blocking and are explicitly not retry
work. Ready for `document-writer`.
