# 03: MVP driver — event → `ChatUI` mapping

**Repo:** /home/bertie/Documents/projects/dsh-tui/
**Blocked by:** 02 (Port ChatUI whole, de-onit it) — needs `dsh_tui/chat_ui.py`
**Status:** ready-for-agent

## Files
- dsh_tui/driver.py
- dsh_tui/tests/test_replay.py

## Action
An `on_notification` callback mapping dsh `session.event` types to `ChatUI` calls, evolved from `probe/notification_probe.py:on_notification`. **MVP wiring is exactly this and nothing more:**

- `tool/call` → `add_tool_call(name, args)` + `show_tool_start(name, args)` + `start_tool_spinner(name, args)`; buffer `callId → name` (`arguments` arrives as a **JSON string**, parse it).
- `tool/result` → `stop_tool_spinner()` + `add_tool_result(name, text)` + `show_tool_done(name, text, success=not isError)`; resolve the name by matching `toolCallId` back to the buffered `callId`; text from `data.message.content[0].content[0].text`.
- `assistant/message` → extract text; if empty, do nothing (the tool-calling step legitimately emits a 0-char message — `ChatUI.stream_end()`'s own `_stream_header_printed` check at `text.py:1041` already skips empty blocks for free); else `stream_start()` → **one** `stream_token(full_text)` → `stream_end()`.
- turn lifecycle → `start_thinking()` when a turn begins, `stop_thinking()` on first output, so the loop never looks hung while a turn runs.
- `system/message`, `user/message`, `request/header`, `request/context`, `session/title`, `agent/inbox/spliced`, `step/start`, `step/end`, `turn/end` → ignored (no MVP `ChatUI` equivalent).

Every deferred capability gets **no stub, no dead branch, no TODO scaffold** — the driver simply does not call those methods yet.

Per `rules/agents.md` Operating Principle 3, this task owns writing its own unit tests directly — `dsh_tui/tests/test_replay.py` is that test, not a separate test-engineer deliverable.

## Established (verified — don't re-derive)
Exact `tool/call` and `tool/result` payload shapes captured live (`probe/probe-output-2-tool.txt`). `assistant/message` is one complete message, confirmed on two live runs plus the 47-entry event catalog (`known-event-types.ts`, no delta type) plus the "assistant messages embed their exact provider stream" architecture note. **The single-`stream_token` path is real, not a hack:** `stream_token` (`text.py:891`) and `_emit` (`:899`) — the tag/link filters are plain string operations with no per-token assumption, and the header prints on first non-whitespace content regardless of chunk count, so one whole-string call renders identically to N token calls.

## Must Verify
Replaying `probe-output-2-tool.txt`'s exact event sequence offline produces the tool block → answer block sequence with the empty first `assistant/message` correctly suppressed; and a `tool/result` with `isError: true` renders as a failure, not a success.

## Unknowns
None blocking the MVP. Reasoning shape and cancellation semantics are deferred by scope (growth table rows 1 and 6 in PLAN.md), not carried as latent risk in this file.

## Dependencies/license
None new beyond `deepseek-harness-sdk`, already installed and exercised.

## Symbols
`deepseek_harness.models.Notification`; `probe/notification_probe.py:on_notification` (reference implementation).

## Acceptance Criteria
- [ ] `test_replay.py` feeds the captured probe events (from `investigation/probe/probe-output-2-tool.txt`) through the driver against a recording stub and asserts the exact `ChatUI` call sequence
- [ ] The empty-message suppression is exercised and asserted
- [ ] The `isError: true` branch is exercised and asserted (renders as failure, not success)
- [ ] Runs offline, no network, no credentials

## Non-Goals
- Throttled/fake streaming
- `stream_think_token`
- `set_metrics`
- `set_context_usage`
- Any tool-name-specific rendering (`ChatUI` renders generically by name+args, and dsh's tool names differ from onit's — generic rendering is what makes that a non-issue)

## Security-sensitive
no — parses event payloads only; the credential path lives entirely in Task 4 (ticket 04).

## Shared Context
Depends on `dsh_tui/chat_ui.py` from ticket 02 (the `ChatUI` method contract it calls). Test fixture is the already-captured `investigation/probe/probe-output-2-tool.txt` — no separate fixture-builder task needed (per PLAN.md's "Test Boundaries": no shared test infrastructure across tasks).

## Execution Log

### 2026-09-10 — developer
Built `dsh_tui/driver.py`: `make_driver(chat_ui) -> on_notification` closure. Buffers `callId -> name` in a dict for `tool/result` name resolution. `arguments` JSON string parsed with `json.loads`, falling back to `{}` on parse failure. `turn/start` calls `start_thinking()`; the first `tool/call` or `assistant/message` after that calls `stop_thinking()` once (tracked via a `state["thinking"]` flag) before rendering — matches "stop_thinking() on first output". `assistant/message` extracts text by joining `type: "text"` blocks from `data.message.content`; empty text returns with no `ChatUI` calls at all (not even `stream_start`), matching the ticket's "if empty, do nothing". Non-empty text: `stream_start()` → one `stream_token(full_text)` → `stream_end()`. `tool/result` reads `data.message.content[0]` for `toolCallId`/`isError`, and `content[0].content[0].text` for the result text; renders `success=not isError`. All nine ignored event types fall through with no branch (only a trailing comment enumerating them, no code).

Built `dsh_tui/tests/test_replay.py`. `probe-output-2-tool.txt` turned out to be `notification_probe.py`'s console log, not raw JSON — full payloads only for `tool/call`/`tool/result` (the probe prints those un-truncated); every other event type is truncated to a 160-char preview, including `assistant/message`. `_load_probe_events` parses full JSON where the line has it (verified via regex + `json.loads` against the real file: `turn/start`, `tool/call`, `tool/result`, `step/start`, `step/end`, `turn/end` all happen to be short enough to be complete; `agent/inbox/spliced`, `system/message`, `user/message`, `request/header`, `request/context`, `session/title`, `assistant/message` are truncated) and reconstructs a minimal event (`{"type": etype}`) for the truncated ones — safe because the driver ignores every type it doesn't branch on. For `assistant/message` specifically (which the driver *does* act on), the probe's own companion line (`-> assistant/message #N, X chars: '...'`) carries the exact text via Python `repr()`; recovered losslessly with `ast.literal_eval` (verified both messages are ≤80 chars so the repr is never itself truncated — message #1 is 0 chars, #2 is 78).

Also created `dsh_tui/tests/__init__.py` (empty, package scaffolding).

Three tests, all passing:
1. `test_replay_probe_2_tool_call_sequence` — replays all 19 real `session.event`s from the fixture through the driver against a `RecordingChatUI` stub (records `(method, args, kwargs)` tuples via `__getattr__`, no Rich/rendering), asserts the exact ordered call list end to end.
2. `test_empty_first_assistant_message_is_suppressed` — same replay, asserts exactly one `stream_start`/`stream_token`/`stream_end` triplet total (not two), confirming the empty first `assistant/message` produced zero stream calls.
3. `test_tool_result_is_error_renders_as_failure` — the real fixture's only tool call succeeded (`isError: false`), so this synthesizes a `tool/call` + `tool/result` pair (documented in the test docstring as synthetic, built from the same payload shape NOTES.md documents) with `isError: true`, and asserts `show_tool_done` receives `success=False`.

Test run (`uv run --offline --with rich --with pytest python3 -m pytest dsh_tui/tests/test_replay.py -v`): **3 passed in 0.05s**. `--offline` confirms no network dependency; fixture is a static file already on disk; no credentials touched anywhere in `driver.py` or the test.

Status: DONE. All 4 acceptance criteria met — see checklist in the developer's final report.

Notes for reviewer: `stop_thinking()`'s exact trigger point ("on first output") was interpreted as "the first `tool/call` or non-empty-or-empty `assistant/message` event after `turn/start`" — i.e. it fires even for the empty first `assistant/message` (since that message *is* the first "output" of the turn in dsh's event stream, even though it renders nothing). This was a judgment call since the ticket doesn't pin down whether an empty message counts as "output"; flagging in case tech-lead wants it scoped differently. No `tool-name`-specific branching was added (`Non-Goals` honored) — `tool/result`'s `name` comes from the buffered `tool/call`, generic to any tool name.

### Review Feedback — 2026-09-10 (tech-lead, Review mode)

Verified independently: 3/3 tests pass (run by reviewer, not trusted from report);
driver correctly filters `type == "text"` blocks, so the `reasoning` content blocks
Probe 3 finding (c) discovered are excluded from the answer — correct handling of a
shape the plan predated. Empty-message suppression confirmed correct.

**Judgment call (`stop_thinking` on empty first `assistant/message`): CONFIRMED CORRECT.**
Necessary, not merely acceptable: `_start_thinking_spinner` (chat_ui.py:574) runs a
background thread writing `\r...\033[K` to stdout every 80ms, and `show_tool_start`
(chat_ui.py:767) does not stop it. The captured probe has `tool/call` 3ms after the
empty `assistant/message`, so leaving the spinner running would interleave with and
corrupt the bordered tool block. The plan's "if empty, do nothing" governs *rendering*
(no stream_start/token/end), which the driver honors.

- id: R2-2; severity: R2; primary_file/symbol: `dsh_tui/driver.py` — `make_driver` turn
  lifecycle; title: thinking spinner never restarts, leaving the rest of a tool-using
  turn with no progress indicator; impact: acceptance criterion 4 ("a spinner runs while
  a turn is in flight; the loop never looks hung") is only partially delivered on the
  tool path; owner_agent: developer; confidence: high.
  - root_cause: `start_thinking()` is called once on `turn/start` and stopped at the
    first output; nothing restarts it. `start_tool_spinner`/`stop_tool_spinner` are
    literal `pass` no-ops upstream (chat_ui.py:785-789), so after `tool/result` there is
    no animated indicator at all for the remainder of the turn.
  - file_paths: `dsh_tui/driver.py` (turn/start, tool/result branches)
  - evidence: `investigation/probe/probe-output-2-tool.txt` — `tool/result` at
    [12891.603], next `assistant/message` at [12893.407]: 1.8s of dead air on a trivial
    `echo`. Scales with real tools and the 943-1164 char answers Probe 3 measured. In
    onit this window is filled by token streaming; this MVP deliberately deferred
    streaming, which makes the spinner the only thing that can fill it.
  - recommended_fix: restart `start_thinking()` after `tool/result` (and/or on
    `step/start`), updating the `state["thinking"]` bookkeeping.
    `_start_thinking_spinner` already stops any prior spinner (line 577), and
    `stream_start` stops it implicitly (line 877), so the fix composes with the existing
    branches without a second stop path.

- id: R3-2; severity: R3; primary_file/symbol: `dsh_tui/driver.py` — `turn/start` branch;
  title: every answer footer reports `0.00s`; impact: user-visible false elapsed time on
  every turn; owner_agent: developer; confidence: high.
  - root_cause: the driver never calls `ChatUI.turn_start()` (chat_ui.py:836), so
    `_turn_elapsed()` (chat_ui.py:845) falls back to `_stream_start_time`, which the
    driver sets microseconds before `stream_end()` because the whole answer arrives at
    once. onit's own loop calls `chat_ui.turn_start()` at turn submission
    (`../onit/src/onit.py:2257`) precisely to avoid this.
  - file_paths: `dsh_tui/driver.py`
  - evidence: `investigation/task4-live-run-transcript.txt` lines 48 and 168 — both
    completed answers, including the multi-paragraph essay, print
    `└───...───  0.00s`.
  - recommended_fix: one line — `chat_ui.turn_start()` alongside `start_thinking()` in
    the `turn/start` branch. Same branch as R2-2; fix together.

- id: R4-1; severity: R4; primary_file/symbol: `dsh_tui/driver.py` — `tool/result`
  branch; title: unmatched `toolCallId` renders the tool name as `None`; impact: cosmetic
  on an unlikely path; owner_agent: developer; confidence: high.
  - root_cause: `pending_tool_calls.get(tool_call_id)` returns `None` and is passed
    straight into `add_tool_result`/`show_tool_done`, which interpolate it into the block
    header.
  - recommended_fix: default to `"tool"` or the raw id. Non-blocking.

### 2026-09-11 — developer (review fix pass)

Fixed all three open findings in `dsh_tui/driver.py`, together in the `turn/start` and `tool/result` branches per the reviewer's own recommended fixes (no design deviation needed).

**R3-2** (`turn/start` branch, line 41): added `chat_ui.turn_start()` immediately before the existing `chat_ui.start_thinking()` call. One line, exactly as recommended — `_turn_elapsed()` now measures from turn submission instead of falling back to `_stream_start_time`.

**R2-2** (`tool/result` branch, end of block): added `chat_ui.start_thinking()` + `state["thinking"] = True` right after `show_tool_done(...)`, mirroring the bookkeeping the `turn/start` branch already does. Verified by reading `chat_ui.py` (not assumed) that this composes safely with no second stop path needed: `_start_thinking_spinner` (chat_ui.py:577) calls `_stop_thinking_spinner()` on entry, so it's safe to call `start_thinking()` again without an explicit prior stop; `_stop_thinking_spinner` (chat_ui.py:608-620) is itself idempotent (no-ops via `if stop_event`/`if thread` guards when nothing is running), so the pre-existing `_stop_thinking_if_needed()` call at the top of the `assistant/message` branch firing a second time (now that `state["thinking"]` is `True` again post-tool-result) is harmless — confirmed in the updated replay test's call sequence rather than assumed.

**R4-1** (`tool/result` branch, name resolution): changed `pending_tool_calls.get(tool_call_id)` to `pending_tool_calls.get(tool_call_id) or "tool"`, so an unmatched `toolCallId` renders the block header as `tool` instead of the literal string `None`.

**Test updates** (`dsh_tui/tests/test_replay.py`): the R2-2 + R3-2 fix changes the exact `ChatUI` call sequence the main replay test (`test_replay_probe_2_tool_call_sequence`) asserts, so extended (not weakened) that assertion: added `("turn_start", (), {})` as the new first call, and added the new `("start_thinking", (), {})` + `("stop_thinking", (), {})` pair after `show_tool_done` — the second `stop_thinking` is real, newly-observable behavior (the final `assistant/message`'s `_stop_thinking_if_needed()` now has something to stop, since the tool/result branch left `state["thinking"] = True`), not test noise. `test_empty_first_assistant_message_is_suppressed` needed no change (it only filters `stream_*` calls, unaffected by either fix). Added one new test, `test_tool_result_unmatched_call_id_defaults_name_to_tool`, synthesizing a `tool/result` with no prior buffered `tool/call` to directly exercise R4-1 (previously untested behavior) — asserts `show_tool_done` receives `"tool"`, not `None`.

Test run (`uv run --offline --with rich --with pytest python3 -m pytest dsh_tui/tests/ -v`): **4 passed in 0.04s** (3 pre-existing + 1 new). `--offline` confirms no network dependency.

Did not touch `dsh_tui/cli.py`, `scripts/run-dsh-tui.sh`, or `README.md` (ticket 04's scope, per instruction).

Status: DONE. All three findings (R2-2, R3-2, R4-1) fixed and verified; full test suite green.

### Review Feedback — 2026-09-11 (tech-lead, Review mode — re-review of fix pass)

Re-derived independently from the code; developer self-report not taken on faith.
Test suite re-run by the reviewer with the project venv
(`investigation/probe/.venv/bin/python -m pytest dsh_tui/tests/ -v`, Python 3.14.7,
pytest 9.1.1): **4 passed in 0.03s**. Note: `dsh_tui/` is still untracked, so there is
no diffable git baseline for these files — review was done against current file state
plus the prior review's recorded findings.

- **R2-2 — CONFIRMED FIXED.** `dsh_tui/driver.py:75-76` restarts the spinner
  (`chat_ui.start_thinking()` + `state["thinking"] = True`) immediately after
  `show_tool_done(...)`, closing the dead-air window measured at
  `probe-output-2-tool.txt` [12891.603] → [12893.407]. Composition verified against
  `chat_ui.py`, not assumed:
  - No double-stop bug. `_stop_thinking_spinner` (`chat_ui.py:608-620`) guards on
    `if stop_event:` / `if thread:` and nulls both, so the second stop is a no-op beyond
    an escape-code line erase. `_start_thinking_spinner` (`chat_ui.py:574-577`) stops any
    prior spinner on entry, so a restart never leaks a thread.
  - Multi-tool turns compose: the next `tool/call` hits `_stop_thinking_if_needed()`
    (`driver.py:56`) before rendering, so the spinner is never live while the bordered
    tool block prints.
  - Final `assistant/message` path composes: `_stop_thinking_if_needed()`
    (`driver.py:87`) stops it, then `stream_start()` (`chat_ui.py:876-877`) stops it
    again idempotently.
  - Output-corruption check: `show_tool_done` ends on `self.console.print(t)`
    (`chat_ui.py:814`), i.e. cursor is on a fresh line when the spinner starts, so the
    closing `└───` border cannot be overwritten.
  - Residual (R5, non-blocking): if a turn ends after `tool/result` with no trailing
    `assistant/message` (`turn/end` is ignored by design), the spinner keeps running
    until `get_user_input()`'s entry `stop_thinking()` (`chat_ui.py:1571`) — a
    sub-second window between `session.run()` returning and the next prompt, not a stuck
    spinner. `state["thinking"]` is left stale-`True` in that case, but its only consumer
    is `_stop_thinking_if_needed()`, whose stop is idempotent, and the next `turn/start`
    resets it. No action required.

- **R3-2 — CONFIRMED FIXED.** `dsh_tui/driver.py:41` calls `chat_ui.turn_start()` before
  `chat_ui.start_thinking()` in the `turn/start` branch, i.e. on the first event of the
  turn and therefore well before `stream_end()` reads it. Verified the read path:
  `turn_start` sets `_turn_start_time` (`chat_ui.py:843`); `_turn_elapsed`
  (`chat_ui.py:845-848`) now takes the `_turn_start_time` arm instead of falling back to
  `_stream_start_time`; consumed by the answer footer at `chat_ui.py:1094`. Re-set on
  every turn, so no cross-turn accumulation. The `0.00s` footers at
  `investigation/task4-live-run-transcript.txt:48,168` are addressed at their root cause.

- **R4-1 — CONFIRMED FIXED.** `dsh_tui/driver.py:67` —
  `pending_tool_calls.get(tool_call_id) or "tool"`. Directly covered by the new
  `test_tool_result_unmatched_call_id_defaults_name_to_tool`
  (`dsh_tui/tests/test_replay.py:174-211`), asserting `show_tool_done` receives `"tool"`.
  Note the `or` (rather than `.get(id, "tool")`) also coerces an empty-string tool name to
  `"tool"`, which is the desired behavior here, not a defect.

- **Test-assertion integrity:** the main replay test was extended, not weakened —
  `test_replay.py:82` adds `("turn_start", (), {})` as the first expected call and
  `:95-96` adds the new `start_thinking`/`stop_thinking` pair after `show_tool_done`,
  with the full ordered sequence still asserted end to end.

**No R1/R2 open on this ticket. Approved.**
