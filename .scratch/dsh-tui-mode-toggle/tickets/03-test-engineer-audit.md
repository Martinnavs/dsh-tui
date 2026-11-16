# 03: Test-engineer audit — mode-toggle tests, Tab-repaint window, full suite

**Repo:** /home/bertie/Documents/projects/dsh-tui
**Blocked by:** 02 (Tab-key model-approach toggle), which is itself blocked on 01 (Pre-flight probe)
**Status:** ready-for-agent

## Files
- dsh_tui/tests/test_mode.py (audit/extend)
- dsh_tui/tests/test_mode_pty.py (audit/extend)
- dsh_tui/tests/test_replay.py (confirm unaffected)

May import the pty fixture machinery from `dsh_tui/tests/test_scroll_pty.py` (same package) or redefine it locally — no new `conftest.py`.

## Action (Test Boundaries, from the plan)

**Test Boundaries:**
- **Task 02 owns** `dsh_tui/tests/test_mode.py` and `test_mode_pty.py` (its own unit tests, per Operating Principle 3). Task 01 has no unit tests (probe script; MVP probe-ticket precedent) — verified by raw output + NOTES.md + the escalation rule.
- **No shared test infrastructure:** `dsh_tui/tests/scroll_helpers.py` exists and may be reused; no new `conftest.py`, no fixture builder.
- **Task 03 (required pass)** independently audits Task 02's tests and adds cross-cutting coverage (repaint-window type-ahead, scroll-mode interaction); its changes land in the feature PR before merge.

**Action (scrollback ticket 03 convention):**
1. Independently audit `test_mode.py` against `dsh_tui/mode.py` and the hook plumbing — do not trust the developer's report; in particular: no-optimistic-update for **timeout** as well as raise; both cycle sets; initial mode; prompt strings; the hook's failure-returns-unchanged-prompt contract.
2. Extend `test_mode_pty.py`: bytes arriving **during the Tab-repaint window** survive (the R2-1/R3-2 class — the Tab repaint goes through the same `_repaint_with_input` path; reuse the `_print_frame`-injection seam technique from `test_scroll_pty.py`'s third test); Tab in scroll mode; no `\x09` leak.
3. Confirm `test_replay.py` passes unmodified (and, if the driver change landed, that the driver's new branch is covered offline).
4. Full suite with real output pasted: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` (baseline before this feature: **62 passed**).

## Established (verified — don't re-derive)
*Orchestrator-verified (binary strings, `deepseek-harness-sdk` 0.1.5rc1): the runtime contains the `@deepseek-ai/dsh-plan-mode` package — `/plan` command, `/plan off` via `ctx.remote.commands.execute(sessionId, "/plan off", [])`, a `plan/mode` projection, the `exit_plan_mode` tool, switch messages "The user switched this session to plan mode." / "...back to the default mode."; its docs: `exit_plan_mode`'s review "uses the composed Web question channel" and "if no interactive review is available, `exit_plan_mode` cannot run and you can still leave plan mode with `/plan off`". Approval is a session knob `z.union([literal("ask"), literal("never")]).nullable()`; `approval/asked`/`approval/decided` are log-only events; a `permissions` projection folds the whole-value knob events over `ctx.shell.sandboxMode` and the approval config; a `sandbox/mode` knob exists (out of scope).*

*Verified by the tech-lead this session (by reading the SDK and repo code; fresh suite run):*
1. **`harness.client` is a public property** (`deepseek_harness/api.py:99-101`) returning the `HarnessClient` — the feature calls mode RPCs through `client.request(...)`, no SDK changes.
2. **`HarnessClient.request(method, params, *, response_model, ...)` requires `response_model`** (no default; `client.py:191-212`) and raises `TypeError` if the result is not a JSON object. Since the mode methods' response shapes are unknown, pass a permissive empty pydantic model (any dict validates). `JsonRpcError` carries `code`/`message` (record `-32601 method not found` per candidate).
3. **A mode request from the prompt thread is safe**: the reader thread (`client.py:344-368`, daemon) dispatches all responses; there is no global in-flight lock (only `_write_lock` around pipe writes). `Session.run` blocks its caller until the session goes idle (`api.py:169-181`), so Tab can only ever be pressed **between turns** — no in-flight turn can race a mode-switch RPC. If the runtime dies mid-session, `advance()` raises `TransportClosedError` → the failure path records it; the next `session.run` hits the existing turn-failure path.
4. **Tab is unbound** in `_input_with_history_unix`: the control-char chain handles `\r`/`\n`, `\x03`, `\x04`, `\x15`, `\x06`, `\x1b`, `('\x7f','\b')`, then the printable range; `\x09` is consumed and dropped. Binding it regresses nothing. No paste suppression for control chars exists (pasted `\x03`/`\x04` already raise) — a pasted Tab triggering a switch is consistent; do not fix paste handling here.
5. **`get_user_input()` hardcodes `"➤ "`** at the `_input_with_history` call (`input.py:620`). `display_prompt` is a local in `_input_with_history_unix`, swapped by the scroll-mode precedent (`_SCROLL_MODE_PROMPT`, `input.py:25`, swap+repaint shape at `input.py:321-333`). `_repaint_with_input` (`input.py:55-92`) is the TCSADRAIN-safe repaint path (patches only OPOST|ONLCR onto raw attrs).
6. **`notice()` prints only and never touches `self.messages`** (`status.py:37-49`) — it is wiped by the next redraw, *including the switch's own immediate repaint*; `add_message` appends to `self.messages` and resets scroll (`core.py:237`). The R2-1 mechanism (MVP ticket 04 review): `add_message("system", ...)` is what makes an at-prompt event visible.
7. **Driver** (`driver.py:95-97`): `system/message` and other types are ignored outright; `make_driver(chat_ui)` takes one argument — an optional second argument defaulting to `None` keeps every `test_replay.py` fixture valid.
8. **Baseline suite: 62 passed** — `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` run today (test_replay 4, test_scroll 46, test_scroll_pty 12). The "27 passed" in the scrollback ticket 03 log is stale (predates the line-buffer refactor `b90ed77`).
9. **No `plan`/`approval`/`mode` identifiers exist in `dsh_tui/` today** (grep verified); `dsh_tui/mode.py` is collision-free. `core.py`'s Apache-2.0 §4b change list (items 1–6) is where upstream-delta changes are recorded — the Tab hook and prompt attribute are such a change and get item 7.
10. **Probe launcher pattern** (`investigation/probe/run_probe3.sh`): render `settings.yaml.template` (placeholder `__ONIT_BASE_URL__`) into the probe-only `investigation/probe/dsh-home/`, export `ONIT_API_KEY` sourced from `~/.secrets/onit/` into the script's own process only, exec the probe via `.venv/bin/python`. The probe uses **uuid4-suffixed session ids** (MVP Probe 3 hit `session "...already exists"` otherwise) and the probe-only DSH_HOME — never the repo's live `.dsh-home` (a live dsh session lives there; probes spawn their own runtime subprocess via the SDK).
11. The sdk profile already auto-executes tools without approval prompts (`investigation/task4-live-run-transcript.txt`) — i.e. the profile default *behaves like* `never`; the probe must confirm this rather than assume it.

## Must Verify
That Task 02's tests exercise what they claim; type-ahead survival across the **Tab-repaint** termios window (the R2-1/R3-2 bug class from scrollback applies to the Tab repaint path too); full suite.

## Unknowns
- Whether pty testing of the Tab branch is flake-free (scrollback precedent says the technique works; the audit decides for Tab).

## Dependencies/license
None new. Probe reuses `investigation/probe/.venv`; feature uses only stdlib + already-required `rich`/`pydantic` (transitively via the SDK). No new packages, no new licenses. The only attribution duty: **`core.py`'s §4b change list gains item 7** (Tab hook + prompt attribute are upstream deltas to ported code).

## Symbols
`dsh_tui/tests/test_mode.py`, `dsh_tui/tests/test_mode_pty.py`, `dsh_tui/tests/test_replay.py`, `dsh_tui/tests/scroll_helpers.py`, `dsh_tui/tests/test_scroll_pty.py` (`_print_frame`-injection seam, third test).

## Acceptance Criteria
- [ ] Gap audit of `dsh_tui/tests/test_mode.py` completed and reported (no-optimistic-update for **timeout** as well as raise; both cycle sets; initial mode; prompt strings; the hook's failure-returns-unchanged-prompt contract)
- [ ] `dsh_tui/tests/test_mode_pty.py` extended: bytes arriving **during the Tab-repaint window** survive (the R2-1/R3-2 class); Tab in scroll mode; no `\x09` leak — OR pty feasibility reported infeasible with concrete evidence
- [ ] `test_replay.py` confirmed passing unmodified (and, if the driver change landed, the driver's new branch is covered offline)
- [ ] Full suite run with real output pasted: `PYTHONPATH=. investigation/probe/.venv/bin/python -m pytest dsh_tui/tests -q` (baseline before this feature: **62 passed**)

## Non-Goals
- Duplicating Task 02's tests
- Production code changes (report, don't fix — findings go through Review-Finding Handling)

## Security-sensitive
no — test-only, no credentials, no network.

## Shared Context
- **Test pass: required** — rationale: the feature (a) adds a control-char dispatch with a real termios window to the raw input loop (the exact bug class scrollback hit twice: R2-1, R3-2), (b) manages security-critical state where the indicator must never lie (the optimistic-update bug class), (c) has an external I/O contract (runtime RPC) whose failure path is security-relevant. This also matches PLAN.md's own "flips to required" trigger — new real failure paths beyond the MVP's deliberately-absent ones.
- **PR strategy:** PR 2 (tickets 02+03) = the feature — `dsh_tui/*` + `README.md` + transcript. Stays open until the test-engineer audit (03) and both review passes close; the audit's test additions land in PR 2, not a third PR.
- **Review-Finding Handling (plan default):** surface R1/R2 findings and wait for human approval before each retry; never auto-fix.
- **Environment:**
  - Runtime venv: `investigation/probe/.venv` (Python 3.14, `deepseek-harness-sdk` 0.1.5rc1 + rich + pytest).
  - Developer agents run under a workspace-write file sandbox: writes confined to the repo; reads elsewhere expected to work; report a sandbox denial rather than working around it.

## Execution Log

<!-- Appended to by every dispatched agent (developer / test-engineer / tech-lead). Prior entries are never rewritten. -->
