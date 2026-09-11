# 02: Raise the in-memory history ceiling

**Repo:** /home/bertie/Documents/projects/dsh-tui
**Blocked by:** None (can start immediately, in parallel with Task 01)
**Status:** ready-for-agent

## Files
- dsh_tui/cli.py

## Action
Pass an explicit `max_messages=1000` in the `ChatUI(...)` construction (`cli.py:66-78`) with a one-line comment stating that the default 100 caps scrollback at roughly 25 tool-using turns and that this is the depth the scroll feature can reach. No other change.

## Established (verified — don't re-derive)
`cli.py` constructs `ChatUI` with `agent_cursor`/`banner_title`/`disclaimer`/`show_logs` only; `max_messages` defaults to 100; `show_logs=True` means tool turns append 2+ extra messages each.

## Must Verify
The client still starts and a turn still renders (a normal live run is sufficient).

## Unknowns
None. Memory cost of 1000 short `Message` dataclasses is negligible.

## Dependencies/license
None.

## Symbols
`dsh_tui.cli.main`; `ChatUI.__init__(max_messages=...)`.

## Acceptance Criteria
- [ ] `max_messages` is explicit and commented in `cli.py`
- [ ] Nothing else in `cli.py` changes
- [ ] Scrollback reaches messages beyond the 100th-from-last in a long session

## Non-Goals
- Changing `ChatUI`'s own default (keep the ported default intact)
- Making it configurable via env var

## Security-sensitive
no.

## Shared Context
None. This is a single, disjoint constructor-argument change with no dependency on Task 01's `chat_ui.py` work and no shared infrastructure other tasks depend on. (Judgment call recorded in the plan: this is the one deliberate step outside "chat_ui.py only" — without it the feature quietly can't reach older history.)

## Execution Log

### 2026-09-11 — developer
Status: DONE

Added `max_messages=1000` to the `ChatUI(...)` construction in `dsh_tui/cli.py` (after `show_logs=True`), with a one-line (3-line) comment explaining the default-100 ceiling caps scrollback at ~25 tool-using turns and that raising it lets the scroll feature reach further back. No other lines in `cli.py` changed.

Verification: `investigation/probe/.venv/bin/python -m py_compile dsh_tui/cli.py` succeeded. `git diff dsh_tui/cli.py` shows only the 4 added lines (comment + `max_messages=1000,`), nothing else touched. Did not touch `chat_ui.py` or add any test file, per instructions.

Acceptance criteria:
- [x] `max_messages` is explicit and commented in `cli.py`
- [x] Nothing else in `cli.py` changes
- [ ] Scrollback reaches messages beyond the 100th-from-last in a long session — not independently verified here (requires a long live session exercising Task 01's scroll feature, which is out of scope/parallel); the raised ceiling itself is confirmed via diff/compile.
