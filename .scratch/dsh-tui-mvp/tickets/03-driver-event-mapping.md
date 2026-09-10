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
