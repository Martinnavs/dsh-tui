# 04: Entry point, config, growth documentation

**Repo:** /home/bertie/Documents/projects/dsh-tui/
**Blocked by:** 01 (Pre-flight probe — finding (a): does a second `.run()` on the held `Session` continue the conversation) AND 03 (MVP driver — event → ChatUI mapping). Do **not** start this task until ticket 01 reports finding (a), and do not proceed with the build if finding (a) is negative — stop and escalate instead (per ticket 01's Unknowns section and PLAN.md Risks).
**Status:** ready-for-agent

## Files
- dsh_tui/cli.py
- dsh_tui/__main__.py
- dsh_tui/settings.yaml.template
- scripts/run-dsh-tui.sh
- README.md

## Action
Generalize `probe/settings.yaml.template` so provider id, model, and base URL are real template variables instead of hardcoded `onit`/`Qwen/Qwen3.8-27B`, keeping the `compat.supportsDeveloperRole: false` / `compat.maxTokensField: max_tokens` block this backend needs. `scripts/run-dsh-tui.sh` exports secrets from `~/.secrets/onit/{host_address,api_key}.txt` following the established pattern in `/home/bertie/Documents/projects/another-harness/scripts/run-onit.sh`; Python reads `os.environ` only, never the files. Main loop: `harness.start_session(id)` **once per process**, then `ChatUI.get_user_input()` → `session.run(text, on_notification=driver)` → repeat until `"\bye"`. Write `README.md` containing usage, the **known limitations** (no live streaming — the answer lands complete; Ctrl+C mid-turn exits the client and may leave the run orphaned server-side; single session, no resume across restarts), and **the growth table from PLAN.md ("Growth table — capability-driven, not calendar-driven"), verbatim**, so a future session enables a row instead of re-deriving it.

## Established (verified — don't re-derive)
This `settings.yaml` schema, provider wiring, and `compat` block already work end to end against the live endpoint — both probes succeeded through them. `ChatUI.get_user_input` returns `"\bye"` on **both** `EOFError` and `KeyboardInterrupt` (`text.py:1562-1565`), so acceptance criterion 5 (Ctrl+C/EOF exits cleanly) is inherited for free with no signal handling to write. `Session` is reusable across `.run()` calls by construction (`api.py:135-184`).

## Must Verify
Ticket 01's finding (a) — that a second `.run()` on the held `Session` continues the conversation. Do not start this task until ticket 01 reports on it; if the finding is negative, stop and escalate rather than building on the assumption.

## Unknowns
Mid-turn Ctrl+C behavior — resolved by *scoping* (documented limitation + growth row 6), not by pretending it is handled. It must be stated in the README, not left silent.

## Dependencies/license
None new.

## Symbols
`deepseek_harness.api.DeepSeekHarnessConfig`, `DeepSeekHarness.start_session` (`api.py:120`), `Session.run` (`api.py:139`).

## Acceptance Criteria
- [ ] MVP acceptance criteria 1–6 (from PLAN.md) all demonstrated in one live run:
  1. `scripts/run-dsh-tui.sh` opens an interactive terminal chat against dsh's `sdk` profile pointed at the onit/Qwen vLLM endpoint
  2. A two-turn conversation demonstrably carries context (turn 2 answers about turn 1) within one process run — shown in real transcript output, not asserted
  3. Tool calls and tool results render inline as onit renders them — the agent never edits a file or runs `bash` invisibly
  4. A spinner runs while a turn is in flight; the loop never looks hung
  5. Ctrl+C / EOF at the prompt exits cleanly
  6. Apache-2.0 attribution preserved on the ported file (`dsh_tui/chat_ui.py`), with changes recorded per §4b

## Non-Goals
- An `onit setup`-equivalent wizard
- Keychain storage
- `--data-path`/sandbox flags
- Cross-restart resume
- Multi-provider selection

## Security-sensitive
yes — the API key reaches the process via environment. It must never be logged, printed, echoed into `settings.yaml` in a world-readable location, or included in any error message. Mirrors the discipline already precedented in `run-onit.sh` and both probe scripts.

## Shared Context
Blocked on ticket 01's finding (a) — read ticket 01's Execution Log for the verdict before starting. Depends on ticket 03's `dsh_tui/driver.py` for the `on_notification` callback wired into the main loop. The growth table to copy verbatim into README.md is in `/home/bertie/Documents/projects/dsh-tui/PLAN.md` under "Growth table — capability-driven, not calendar-driven". Secret-handling pattern precedent: `/home/bertie/Documents/projects/another-harness/scripts/run-onit.sh`.

## Execution Log

### 2026-09-10 — developer

**Status: DONE.** Both blockers cleared before starting: ticket 01 finding
(a) is POSITIVE (`investigation/NOTES.md` "Probe 3" (a): held `Session`,
second `.run()` answered "Teal — you told me in your first message.");
ticket 03's `dsh_tui/driver.py` exists with a passing offline replay test.

**Files created/changed:**
- `dsh_tui/settings.yaml.template` — generalized `investigation/probe/settings.yaml.template`: `__DSH_TUI_PROVIDER__` / `__DSH_TUI_MODEL__` / `__DSH_TUI_BASE_URL__` replace the hardcoded `onit` / `Qwen/Qwen3.8-27B` / `__ONIT_BASE_URL__`. `compat.supportsDeveloperRole: false` / `compat.maxTokensField: max_tokens` kept verbatim.
- `scripts/run-dsh-tui.sh` — new. Sources `~/.secrets/onit/{host_address,api_key}.txt` into its own process env only (never printed, never written to a file in the clear); renders `dsh_tui/settings.yaml.template` into `.dsh-home/settings.yaml` via `sed`; exports `ONIT_API_KEY`, `DSH_TUI_PROVIDER`, `DSH_TUI_MODEL`, `DSH_TUI_DSH_HOME`; execs `investigation/probe/.venv/bin/python -m dsh_tui` with `PYTHONPATH` set to the repo root. Reuses the probe venv (already has `deepseek-harness-sdk` + dsh runtime proven live); installs `rich` into it on first run if missing (idempotent, `pip install -q rich` — done once during this task, confirmed present).
- `dsh_tui/cli.py` — new. `main()`: reads `DSH_TUI_PROVIDER`/`DSH_TUI_MODEL`/`DSH_TUI_DSH_HOME`/`ONIT_API_KEY`-presence-check from `os.environ` only (never reads the secrets files); builds `DeepSeekHarnessConfig(provider=, model=, profile="sdk", dsh_home=)`; constructs `ChatUI(agent_cursor="dsh", banner_title="dsh Chat Interface", disclaimer=..., show_logs=True)` — `show_logs=True` is required, not cosmetic: `add_tool_call`/`show_tool_start`/`add_tool_result`/`show_tool_done` all early-return when `show_logs` is `False`, which would silently violate acceptance criterion 3. Loop: `harness.start_session(session_id)` once, outside the `while`; each iteration is `chat_ui.get_user_input()` → break on `"\bye"` → `session.run(text, on_notification=driver)`, with a broad `except Exception` around `session.run` that reports via `chat_ui.notice(..., level="error")` rather than crashing the loop or printing a raw traceback.
- `dsh_tui/__main__.py` — new. `python -m dsh_tui` → `dsh_tui.cli.main()`.
- `README.md` — rewritten: usage (`scripts/run-dsh-tui.sh`, prerequisites, provider/model override), known limitations (streaming, mid-turn Ctrl+C, no cross-restart resume — as specified, not silently omitted), the growth table copied verbatim from `PLAN.md` (all 10 rows, including the two ⚠ rows), and a short attribution section pointing at `chat_ui.py`'s own notice.
- `.gitignore` — added `.dsh-home/`, `__pycache__/`, `*.pyc`, `.pytest_cache/` (the new runtime DSH_HOME this task introduces, plus bytecode noise already accumulating under `dsh_tui/`).
- `investigation/task4-live-run-transcript.txt` — new. Full `tmux capture-pane` transcript from the live verification run below (grepped clean of the API key and the endpoint IP before being written to the repo).

**Live verification (via `scripts/run-dsh-tui.sh` against the real onit/Qwen vLLM endpoint, driven through `tmux` since the raw-terminal input loop needs a real tty):**
1. Banner rendered, disclaimer shown, `➤` prompt appeared — PASS (criterion 1).
2. Turn 1 "My favourite colour is teal." → answered. Turn 2 "What is my favourite colour? Answer in one short sentence." → answered "Your favourite colour is teal." within the same process/session — PASS (criterion 2), full text in `investigation/task4-live-run-transcript.txt`.
3. "Run this exact bash command and show me its output: echo hello-from-dsh-tui-live" → rendered a bordered `┌─ ⚙️ bash ... └───` call block with the JSON arguments, then a bordered `┌─ ↩ bash ... └───` result block with `hello-from-dsh-tui-live`, then the assistant's answer — PASS (criterion 3), confirms `show_logs=True` was in fact necessary (without it these blocks are silently skipped).
4. A long-form prompt ("Write a detailed 5-paragraph essay about the history of teal...") polled every 400ms during the turn showed rotating spinner frames (`⠼ On it...` → `⠏ On it...` → ... → `⠋ Analyzing your request...`) — PASS (criterion 4).
5. Ctrl+C sent at the `➤` prompt (between turns, not mid-turn) → process exited with code `0`, no traceback, no dangling prompt — PASS (criterion 5).
6. Criterion 6 (Apache-2.0 attribution) was established by Task 2 and untouched by this task; `dsh_tui/chat_ui.py`'s header/notice unchanged.

**Ran `dsh_tui/tests/test_replay.py`** (Task 3's suite, unaffected by this task's files) to confirm nothing broke: `3 passed in 0.05s`.

**Notes for reviewer:**
- The probe venv (`investigation/probe/.venv`) is now also the runtime venv for the shipped app, not just the probe's own venv — this was a judgment call to stay within this ticket's file list (no new venv-management file was in scope) while reusing infrastructure already proven against the live endpoint. Flagging in case tech-lead wants a dedicated `dsh_tui`-owned venv as a follow-up; not done here as it would exceed the ticket's Files list.
- `chat_ui.py`'s `show_logs=False` default would have silently satisfied every acceptance criterion except #3 (tool calls just wouldn't render) — worth double-checking in review that `show_logs=True` in `cli.py` is intentional and not reverted.
- Non-goals respected: no config wizard, no keychain, no `--data-path`/sandbox flags, no cross-restart resume, no multi-provider selection UI (provider/model are env-var overrides only, per the growth table's own row 10 framing).

### Review Feedback — 2026-09-10 (tech-lead, Review mode)

Security discipline verified independently by the reviewer: no Python file reads
`~/.secrets`, `host_address`, `api_key.txt`, `expanduser`, or `Path.home` (grep across
all `*.py` returns nothing); only the three shell launchers source the raw files;
`cli.py` reads `os.environ` for presence only and never prints the value. A literal-value
scan of the whole working tree for both the API key and the endpoint address found them
in **no** non-ignored file — including `investigation/task4-live-run-transcript.txt` and
`investigation/NOTES.md`. `.dsh-home/`, `investigation/probe/.venv/`,
`investigation/probe/dsh-home/` are all gitignored and confirmed via `git check-ignore`.

README verified: growth table is **byte-identical** to PLAN.md's (diff of extracted table
rows is empty); known-limitations section carries all three required items.
Attribution header preserved verbatim with an adaptation notice naming both changes.
Live transcript demonstrates criteria 1, 2 (teal recalled across turns), 3, 5 (exit 0),
6; criterion 4 is partially met — see R2-2 on ticket 03.

**Judgment call (`show_logs=True`): CONFIRMED NECESSARY, with a side effect (R3-1).**
`add_tool_call` (271), `add_tool_result` (282), `show_tool_start` (769) and
`show_tool_done` (804) all early-return when `show_logs` is False, so acceptance
criterion 3 is unreachable without it. Not a scope deviation — a plan gap: PLAN.md put
`ChatUI(show_logs=True)` in the growth table while criterion 3 required it in v1. The
developer's inline comment and the README note both record it correctly.

**Judgment call (reusing `investigation/probe/.venv`): ACCEPTED for the MVP**, with R3-3
below on the missing creation path.

- id: R2-1; severity: R2; primary_file/symbol: `dsh_tui/cli.py` — `main` turn-loop
  `except` branch; title: a failed turn is silently swallowed — the user never sees the
  error; impact: any provider/auth/network failure looks like the prompt doing nothing;
  owner_agent: developer; confidence: high.
  - root_cause: `chat_ui.notice(f"Turn failed: {exc}", level="error")` prints to the
    console, then the loop immediately calls `get_user_input()`, whose first actions are
    `stop_status()`/`stop_thinking()` (which write `\r\033[2K` over the current line) and
    then `self.console.clear()` (chat_ui.py:1571-1574). The notice is erased before it
    can be read, and because `notice` does not touch `self.messages`, it is absent from
    the redrawn chat history too — so the failure leaves no trace on screen.
  - file_paths: `dsh_tui/cli.py:88-91`
  - evidence: `chat_ui.py:1569-1576` (`get_user_input` clears the console on entry);
    `chat_ui.py:653-665` (`notice` only prints, never appends to `self.messages`).
  - recommended_fix: follow the ported source's own precedent and record the failure in
    history so it survives the redraw — `chat_ui.add_message("system", f"Turn failed:
    {exc}")` (onit does exactly this for provider errors at
    `../onit/src/onit.py:2298` and `:2335`), optionally keeping `notice()` as well.
    Stopping the thinking spinner in the same `except` is worth doing for the same
    reason.

- id: R3-1; severity: R3; primary_file/symbol: `dsh_tui/cli.py` — `ChatUI(...)`
  construction; title: `show_logs=True` also ships the execution-logs panel, an explicit
  plan non-goal; impact: a permanently empty "🔧 Execution Logs / No execution logs yet."
  panel is printed above the chat on every redraw; owner_agent: developer; confidence:
  high.
  - root_cause: `show_logs` is overloaded upstream — it gates both inline tool rendering
    (needed for criterion 3) and `render()`'s logs panel (chat_ui.py:1141-1143). The
    driver never calls `add_log`, so the panel is always empty.
  - file_paths: `dsh_tui/cli.py:77`, `dsh_tui/chat_ui.py:1141`
  - evidence: `investigation/task4-live-run-transcript.txt` — the empty panel appears at
    every screen redraw.
  - recommended_fix: **needs a human scope decision, not an automatic fix.** Separating
    the two behaviors means a third change to `ChatUI`, which Task 2's non-goals forbid
    ("any rendering/behavior change beyond those two fixes"). Either accept the empty
    panel for the MVP (it is honest about what is wired) or explicitly authorize a narrow
    third change — a `show_tool_blocks` flag defaulting to `show_logs`. Non-blocking
    either way.

- id: R3-3; severity: R3; primary_file/symbol: `scripts/run-dsh-tui.sh` — `VENV_PY`;
  title: no venv creation path exists anywhere in the repo; impact: the documented usage
  is unreproducible on any machine but this one; owner_agent: developer; confidence:
  high.
  - root_cause: the runtime venv is `investigation/probe/.venv`, which is gitignored and
    created by no script. The wrapper's own error message points the reader at
    `investigation/probe/run_probe.sh`, which also only *uses* `.venv` (line 17) and
    never creates it. There is no `pyproject.toml`, `requirements.txt`, or `setup.py`.
  - file_paths: `scripts/run-dsh-tui.sh:22,27`, `investigation/probe/run_probe.sh:17`,
    `README.md:27-31`
  - evidence: repo-wide grep for venv creation / `pip install` finds only the idempotent
    `pip install -q rich` in `scripts/run-dsh-tui.sh:29`; no packaging file exists.
  - recommended_fix: cheapest honest fix is a `requirements.txt` (`deepseek-harness-sdk`,
    `rich`) plus a create-if-missing branch in the wrapper, or at minimum correct the
    dead-end error message and state in the README how the venv was originally built.
    Reusing the probe venv itself is fine for an MVP; the absent creation path is the
    actual gap.

### 2026-09-11 — developer (review fixes: R2-1, R3-1, R3-3)

**Status: DONE.** Fixed R2-1 and R3-3; recorded the human decision on R3-1 (no code
change). Read `dsh_tui/cli.py` and `dsh_tui/chat_ui.py`'s `notice`, `add_message`,
`stop_thinking`, `get_user_input` before editing, per the assignment. Did not touch
`dsh_tui/driver.py` or its tests (owned by the parallel ticket 03 task).

**R2-1 — fixed.** `dsh_tui/cli.py:88-99`, the `except Exception as exc` branch around
`session.run`, now does two things before/alongside the existing `chat_ui.notice(...)`:
1. `chat_ui.stop_thinking()` first. `driver.py`'s `state["thinking"]` is private to its
   closure, so an exception raised between a `turn/start` event (spinner started) and the
   next event that would normally stop it (e.g. a network failure mid-turn) can leave the
   spinner thread running when control returns to `cli.py`. Confirmed
   `chat_ui._stop_thinking_spinner()` (chat_ui.py:608-620) is safe to call when nothing is
   spinning — both `self._thinking_stop_event` and `self._thinking_thread` are `None` in
   that case, so the `if stop_event:`/`if thread:` guards make it a no-op beyond the
   harmless cursor-reset escape codes — so this call is correct whether or not the spinner
   was actually running.
2. `chat_ui.add_message("system", f"Turn failed: {exc}")`, kept alongside the existing
   `chat_ui.notice(..., level="error")` rather than replacing it. Confirmed the root cause
   independently: `get_user_input()` (chat_ui.py:1569-1576) calls `stop_status()` /
   `stop_thinking()` then `self.console.clear()` as its very first actions, which erases
   `notice()`'s printed line before the user's eye could land on it; `notice()`
   (chat_ui.py:653-665) only calls `self.console.print(...)` and never touches
   `self.messages`, so nothing survived the next `render()` either. `add_message` (
   chat_ui.py:202-232) appends a `Message` to `self.messages`, which `render()` (and thus
   every subsequent redraw, including the one at the top of the next `get_user_input()`
   call) draws from — so the failure now has a permanent line in the visible chat history.
   `../onit/src/onit.py` does not exist on this machine to diff against directly; applied
   the pattern described in the finding instead. Verified `python3 -m py_compile
   dsh_tui/cli.py` (via `investigation/probe/.venv/bin/python`) passes.

**R3-1 — human decision, no code change.** Accept the empty "Execution Logs" panel
as-is for the MVP. `show_logs=True` stays required for tool-call rendering (acceptance
criterion 3); the driver's never calling `add_log` (so the logs panel always renders
empty) is accepted rather than fixed, since a real fix needs a third `ChatUI` change
(e.g. a `show_tool_blocks` flag) beyond this ticket's authorized scope. Revisit as a
growth-table item if/when `add_log` is wired up from the driver's ignored-event branch
(see README.md's growth table row "You want a debugging view of every event").

**R3-3 — fixed.** Added `/home/bertie/Documents/projects/dsh-tui/requirements.txt`
pinning `deepseek-harness-sdk>=0.1.5rc1` and `rich>=15.0.0` (the exact versions already
proven live in `investigation/probe/.venv`, confirmed via `pip list` before editing).
Added a create-if-missing branch to `scripts/run-dsh-tui.sh` (lines ~24-40): computes
`VENV_DIR`/`VENV_PY` as before, but now `if [[ ! -x "$VENV_PY" ]]` runs `python3 -m venv
"$VENV_DIR"`, upgrades `pip`, then installs `-r requirements.txt`, before falling through
to the existing (now redundant-safe) `rich` presence check and the rest of the script
unchanged. Replaced the old dead-end error message (which pointed at
`investigation/probe/run_probe.sh`, itself only a *user* of `.venv`, never a creator)
with a `venv creation ... failed` message that only fires if creation itself errored.
Updated README.md's requirements bullet to state the venv is created automatically on
first run from `requirements.txt` if missing, and is reused as-is (with only a `rich`
top-up) if it already exists — no longer describing it as a pre-existing manual
artifact.

Verification:
- `python3 -m py_compile dsh_tui/cli.py` via `investigation/probe/.venv/bin/python` —
  passed (confirms the R2-1 edit didn't break the module).
- `bash -n scripts/run-dsh-tui.sh` — passed (syntax check on the edited script).
- Did **not** delete or touch the real `investigation/probe/.venv` to test creation —
  per instructions it's precious, already-proven, working infrastructure. Instead:
  extracted the exact create-if-missing block (verbatim body, parameterized only by
  `VENV_DIR`) into a throwaway script run against a scratch directory
  (`/tmp/.../scratchpad/test-venv-2`, outside the repo). It printed the expected "no venv
  at ... creating it from requirements.txt" message, ran `python3 -m venv`, `pip install
  --upgrade pip`, `pip install -r requirements.txt`, and both `deepseek_harness` and
  `rich` imported successfully afterward — same package versions (`deepseek-harness-sdk
  0.1.5rc1`, `deepseek-harness-runtime-bin 0.1.5rc1`, `rich 15.0.0`) as the real
  `investigation/probe/.venv`, confirming `requirements.txt` reproduces it. Also ran a
  fully independent manual repro (`python3 -m venv` + `pip install -r requirements.txt`
  into a second scratch dir) with the same result, before writing the exercise script, as
  a first sanity check that PyPI installs of `deepseek-harness-sdk`/`rich` succeed in this
  environment at all. Confirmed afterward via `ls -ld` and `pip list` that the real
  `investigation/probe/.venv` was untouched and still has its original packages. Deleted
  both scratch venvs after verification; nothing was written inside the repo.

**Files changed:**
- `dsh_tui/cli.py` — R2-1 fix (stop_thinking() + add_message() in the turn-failure
  `except` branch).
- `requirements.txt` — new, R3-3 fix.
- `scripts/run-dsh-tui.sh` — R3-3 fix (create-if-missing venv branch, updated error
  message).
- `README.md` — R3-3 fix (requirements bullet describes self-bootstrapping venv).

**Notes for reviewer:**
- `requirements.txt` uses `>=` pins rather than exact `==` pins, deliberately: exact pins
  would be more reproducible but the ticket only asked for "listing `deepseek-harness-sdk`
  and `rich`", and `>=` at the proven-good version leaves room for patch upgrades without
  a repo edit. Flagging in case tech-lead wants `==` instead for stricter reproducibility.
- Did not add a `pyproject.toml`/`setup.py` — the ticket's recommended fix named
  `requirements.txt` specifically, and `scripts/run-dsh-tui.sh`'s `pip install -r
  requirements.txt` doesn't need a package build system for this repo (dsh_tui is run via
  `PYTHONPATH`, not installed).

### Review Feedback — 2026-09-11 (tech-lead, Review mode — re-review of fix pass)

Re-derived independently from the code and by running things; developer self-report not
taken on faith. Test suite re-run by the reviewer with the project venv
(`investigation/probe/.venv/bin/python -m pytest dsh_tui/tests/ -v`): **4 passed in
0.03s**. `investigation/probe/.venv` was treated as read-only throughout and was not
deleted, recreated, or modified. Note: `dsh_tui/`, `scripts/` and `requirements.txt` are
still untracked, so there is no diffable git baseline for them — review was against
current file state plus the prior review's recorded findings.

- **R2-1 — CONFIRMED FIXED.** `dsh_tui/cli.py:90-107`. The `except Exception` branch now
  calls `chat_ui.stop_thinking()` (`:98`), then `chat_ui.notice(...)` (`:106`), then
  `chat_ui.add_message("system", f"Turn failed: {exc}")` (`:107`).
  - Survives the redraw — verified through the render path, not assumed: `add_message`
    (`chat_ui.py:202-232`) appends a `Message` to `self.messages`; `get_user_input`
    (`chat_ui.py:1569-1576`) clears the console and then prints `self.render()`;
    `render` (`chat_ui.py:1129-1145`) returns `render_messages()`, which iterates
    `self.messages` (`chat_ui.py:397`). Role `"system"` is not filtered — it falls to the
    `else` arm at `chat_ui.py:414-415` and renders via `_render_assistant_message`. The
    failure therefore has a permanent, visible line in chat history.
  - `stop_thinking()` idempotency — CONFIRMED SAFE, no double-stop.
    `_stop_thinking_spinner` (`chat_ui.py:608-620`) reads `_thinking_stop_event` /
    `_thinking_thread` into locals and guards each with `if`, then nulls both, so calling
    it with nothing running is a no-op beyond a `\r\033[2K` line erase. Two independent
    stop paths can therefore both fire safely.
  - `chat_ui` vs. driver `state["thinking"]` mismatch — real but benign (R5,
    non-blocking): `cli.py:98` stops the real spinner without being able to reset the
    driver closure's `state["thinking"]` (`driver.py:24`), so it can be left stale-`True`
    after a failed turn. Its only consumer is `_stop_thinking_if_needed()`
    (`driver.py:26-29`), whose `stop_thinking()` is idempotent per the above, and the next
    `turn/start` (`driver.py:40-43`) unconditionally re-syncs it. No corrective action
    required; the inline comment at `cli.py:91-97` documents the reasoning accurately.
  - R5 (informational, non-blocking): because role `"system"` renders through
    `_render_assistant_message`, the failure line is styled like an assistant reply rather
    than an error. Honest and visible, which is what R2-1 demanded; distinct error styling
    would need a `ChatUI` change that ticket 02's non-goals forbid.

- **R3-1 — CONFIRMED RECORDED CORRECTLY.** The human decision (accept the permanently
  empty "🔧 Execution Logs" panel for the MVP; `show_logs=True` stays required for
  acceptance criterion 3) is logged in this ticket's `2026-09-11` entry, with no code
  change, and points at a real revisit path — README.md:83's growth-table row
  ("You want a debugging view of every event … call `add_log` from the driver's
  ignore-branch") exists as cited. `cli.py:73-77` still carries the `show_logs=True`
  rationale comment; not reverted.

- **R3-3 — CONFIRMED FIXED, verified by execution, not by reading the developer's test.**
  `requirements.txt` exists (`deepseek-harness-sdk>=0.1.5rc1`, `rich>=15.0.0`);
  `scripts/run-dsh-tui.sh:31-38` adds the create-if-missing branch and `:38` replaces the
  old dead-end error message.
  - Clobber safety — CONFIRMED. The guard is `if [[ ! -x "$VENV_PY" ]]`
    (`run-dsh-tui.sh:31`); with the real `investigation/probe/.venv/bin/python` present
    and executable the guard evaluates false, so an existing working venv is never
    re-created. Reviewer evaluated the guard against the real path (skips creation) and
    confirmed the venv was untouched.
  - Creation path — CONFIRMED by the reviewer running the exact block
    (`python3 -m venv` → `pip install --upgrade pip` → `pip install -r requirements.txt`)
    verbatim against a throwaway scratch directory outside the repo: printed the expected
    "no venv at … creating it from requirements.txt", completed, and
    `import deepseek_harness, rich` succeeded. Re-running the same block against the
    now-populated directory was a clean no-op (idempotent). Scratch venv deleted; nothing
    written inside the repo.
  - `bash -n scripts/run-dsh-tui.sh` passes.
  - R5 (informational): under `set -euo pipefail` (`:8`), a failing `python3 -m venv`
    aborts at `:33`, so the fallback message at `:38` is close to unreachable. Harmless
    belt-and-braces, not a defect.
  - The developer's `>=` vs `==` pin question is a deliberate, documented trade-off and
    acceptable for the MVP; leaving as-is.

**No R1/R2 open on this ticket. Approved** — ready for the documentation pass.
