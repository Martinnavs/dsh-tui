# 02: Tab-key model-approach toggle (plan / edit / auto)

**Repo:** /home/bertie/Documents/projects/dsh-tui
**Blocked by:** 01 (Pre-flight probe) — and the requirement to read ticket 01's Execution Log verdicts (and `investigation/NOTES.md` "Probe 4") first: the fallback scope rule below is a function of findings (a)–(e), which don't exist yet
**Status:** ready-for-agent

## Files
- dsh_tui/mode.py (new)
- dsh_tui/chat_ui/input.py
- dsh_tui/chat_ui/core.py (attribution header + prompt attribute)
- dsh_tui/cli.py
- dsh_tui/driver.py (conditional)
- dsh_tui/tests/test_mode.py (new)
- dsh_tui/tests/test_mode_pty.py (new)
- README.md
- investigation/mode-toggle-live-run-transcript.txt (new)

## Fallback scope rule (deterministic)
**Fallback scope rule (deterministic — the ticket states the resulting set concretely once the probe lands; the developer implements exactly that, nothing else).** Let P = (a) positive (else the ticket never starts), D = profile's effective approval default from (b), S = approval policy client-settable from (b). Reachable set R: P∧S → {plan, edit, auto}; P∧¬S → {plan} ∪ {state matching D} (D=`never` → {plan, auto}, "edit" dropped; D=`ask` → {plan, edit}, "auto" dropped). The cycle lists R in plan→edit→auto order; **initial mode** = the state in R matching a fresh session (plan off + D). The unreachable state, if any: not shown in the UI, reported in the Execution Log, documented in the README as a limitation + one growth-table row. Never ship a dead, unreachable state.

## Reachable set (fill in from ticket 01's findings before starting; then implement exactly this set and nothing else)
> **PLACEHOLDER — fill in before writing any code, from ticket 01's Execution Log (and `investigation/NOTES.md` "Probe 4"); the orchestrator checks this section before implementation starts.**
> - P ((a) plan switch reachable from the client): …
> - D (profile's effective approval default, from (b)): …
> - S (approval policy client-settable, from (b)): …
> - Resulting reachable set R (per the rule above): …
> - Initial mode (the state in R matching a fresh session, plan off + D): …
> - Unreachable state, if any (report in the Execution Log; document in the README as a limitation + one growth-table row): …

## Action
1. `dsh_tui/mode.py` (new): `Mode` (plan/edit/auto), a pure cycle over an explicit reachable-set parameter (data from the probe findings, not hardcoded guesses), and `ModeController(client: HarnessClient, session_id, initial_mode, reachable)`. `controller.mode` = **last confirmed** mode only; `controller.prompt` = `f"➤ [{mode}] "`. `advance()` computes the next mode, issues the runtime call(s) per the wire protocol recorded in NOTES.md Probe 4 (`apply()` = the minimal set: enter plan → `/plan on`; leave plan → `/plan off`; entering edit/auto additionally sets the policy to `ask`/`never` only if S, skipping if the probe shows the setter is a no-op when already equal), and **assigns `self.mode` only after success** — no optimistic update. Use a bounded `timeout_seconds` (ticket: 10). No "read current runtime state" round-trip (YAGNI): the confirmed mode is the client's source of truth; rejections hit the failure path.
2. `dsh_tui/chat_ui/input.py`: one control-char branch `elif char == '\x09':` alongside the existing Ctrl+U/Ctrl+F branches (fires in normal **and** scroll mode, like the scroll keys, and does not exit scroll mode). It calls a new optional ChatUI hook attribute (`self._tab_handler: Callable[[], str | None] | None`, default `None` = inert, set by `cli.py`). **Hook contract (fixed):** returns the prompt string the loop should repaint with — the new mode prompt on success, the *unchanged* prompt on failure (so the panel repaint still shows the error line) — or `None` for "do nothing". In scroll mode the loop keeps the scroll prompt for the immediate repaint; `self._prompt` (below) is what scroll-mode-exit and the next turn restore, and the hook updates it.
3. `dsh_tui/chat_ui/core.py`: `ChatUI.__init__` gains `self._prompt = "➤ "` and `self._tab_handler = None` (defaults preserve today's behavior for every existing caller/test); **`get_user_input` calls `self._input_with_history(self._prompt)`** (replaces the hardcoded literal at `input.py:620`); §4b header gains change item 7 (Tab hook + prompt attribute).
4. `dsh_tui/cli.py`: construct `ModeController` after `start_session`; set `chat_ui._prompt = controller.prompt` before the loop (initial mode per the fallback rule); the `_tab_handler` closure: on `advance()` success → `chat_ui._prompt = controller.prompt`, `chat_ui.add_message("system", f"Model approach: {old} → {new}")`, return the new prompt; on exception → `chat_ui.notice(..., level="error")` + `chat_ui.add_message("system", f"Mode switch failed: ...")`, return the unchanged prompt. (The recordable mechanism is `add_message` — `notice()` is wiped by the switch's own repaint, R2-1 root cause; keep both, consistent with the failure-path precedent.) The main loop re-derives the prompt from the controller each iteration, so any mid-turn controller change syncs at the next prompt automatically.
5. `dsh_tui/driver.py` — **only if both** (c) shows a model-side plan exit is possible on a stdio client **and** (d) names an observable event: `make_driver(chat_ui, mode_controller=None)` (None default keeps `test_replay.py` valid); map the event to `mode_controller` set-confirmed + `add_message("system", "Model switched ...")`. Rationale to record in the ticket: a *user-initiated* switch happens **between turns, when no driver subscription is active** (the `session.run` subscription exists only during a turn) — so the driver can only ever observe *model-initiated* switches; otherwise the driver is untouched and the client-confirmed state is the single source of truth.
6. **Own tests** (Operating Principle 3): `test_mode.py` — offline, recording stub (no live runtime): cycle wraps over the full and the reduced 2-state sets; `advance()` keeps the old mode when `apply()` raises (incl. timeout); `prompt` derivation; initial-mode derivation. `test_mode_pty.py` — per the `test_scroll_pty.py` precedent (real `\x09` bytes through `_input_with_history_unix`, hook stubed): prompt swaps, no stray `\x09` in the submitted line, Tab in scroll mode (underlying mode changes, scroll prompt retained, no exit); **report infeasibility with evidence rather than shipping a flaky test**. Confirm the full suite green (baseline 62 passed) with pasted output.
7. **Live verification** (ticket 04 tmux precedent): via `scripts/run-dsh-tui.sh`, at the prompt Tab to plan → a task to create a scratch file (`mode-toggle-live-scratch.txt` at repo root) → **file must not exist** after the turn; Tab to edit/auto → same task → **file must exist** (delete it after; record deletion in the Execution Log). Save the `tmux capture-pane` transcript to `investigation/mode-toggle-live-run-transcript.txt`, grepped clean of the API key and endpoint IP before writing.
8. **README.md**: states + dsh semantics (plan mode on; profile-default approval; approval `never`), the prompt-row indicator, what `auto` disables in plain terms, limitations (Tab at the prompt only; model-side `exit_plan_mode` behavior per (c); any unreachable state honestly), and the startup-default note — if the profile already auto-executes, say the app starts labeled `auto` because that behavior was always there. One growth-table row for the out-of-scope `sandbox/mode` axis if the probe confirms the knob.

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
Live two-turn mode check (criterion 3); the no-optimistic-update invariant under a raising/timeout `apply()` (unit, recording stub); prompt swap via real `\x09` bytes through `_input_with_history_unix` (pty, if achievable per the scrollback precedent).

## Unknowns
The plan's four wire-protocol unknowns — exact method names/params for the plan switch and the approval-policy setter; the sdk profile's effective approval default (`ask` or `never`); what `exit_plan_mode` does on a stdio client (clean fail vs hang); whether a mode switch emits any client-observable `session.event` — are all closed by ticket 01's findings; read ticket 01's Execution Log before starting (see Blocked by). Remaining during this task:
- Whether pty testing of the Tab branch is flake-free (scrollback precedent says the technique works; the audit decides for Tab).

## Dependencies/license
None new. Probe reuses `investigation/probe/.venv`; feature uses only stdlib + already-required `rich`/`pydantic` (transitively via the SDK). No new packages, no new licenses. The only attribution duty: **`core.py`'s §4b change list gains item 7** (Tab hook + prompt attribute are upstream deltas to ported code).

## Symbols
`dsh_tui.cli.main`; `dsh_tui.chat_ui.input._input_with_history_unix`, `get_user_input` (`input.py:613`), `_repaint_with_input`, `_print_frame`, `_SCROLL_MODE_PROMPT`; `dsh_tui.chat_ui.core.ChatUI.__init__`, `add_message` (`core.py:237`), `§4b` header change list; `dsh_tui.chat_ui.status.notice`; `dsh_tui.driver.make_driver`; `dsh_tui.tests.test_scroll_pty` (pty precedent).

## Acceptance Criteria
(Plan acceptance criteria 2–7, verbatim; criterion 1 belongs to ticket 01. Plus, per the plan: "cycle set/initial mode exactly as the fallback rule yields for the recorded findings; §4b item 7 present; `test_mode.py` + pty test (or infeasibility report) green.")
- [ ] **2.** Tab at the prompt cycles the reachable set (3-state or reduced 2-state per the fallback rule above) with the mode always visible in the prompt row.
- [ ] **3.** Live transcript: with plan, a file-editing task does **not** create the target scratch file; in edit/auto the same task **does**; transcript saved, grepped clean of API key/endpoint IP (ticket 04 precedent).
- [ ] **4.** A confirmed switch is recorded in chat history; a failed switch leaves the indicator unchanged and records the error.
- [ ] **5.** Startup indicator matches the profile's real default (if the profile already auto-executes, the app starts labeled `auto`).
- [ ] **6.** Full suite green (baseline **62 passed**, verified today) with real output pasted; `test_replay.py` unmodified.
- [ ] **7.** README documents the states, their dsh semantics, the indicator, all limitations (incl. any unreachable state and model-side `exit_plan_mode` behavior), and what `auto` disables.
- [ ] Cycle set/initial mode exactly as the fallback rule yields for the recorded findings.
- [ ] §4b item 7 present.
- [ ] `test_mode.py` + pty test (or infeasibility report) green.

## Non-Goals
The plan's global non-goals:
- `sandbox/mode`/escalation axis (different knob — README growth row only)
- mid-turn Tab
- Windows-loop binding (unix-only, scrollback parity)
- any fix to control-char handling inside bracketed paste
- persistence across restarts
- plan-review UI / Web-question-channel emulation
- config/settings knob for a startup default mode
- multi-session support

Plus (this ticket):
- No changes to `driver.py` unless the (c)∧(d) rule fires
- No new shared test fixtures/`conftest.py`

## Security-sensitive
yes — the live verification step (Action item 7) runs against the live endpoint via `scripts/run-dsh-tui.sh`: identical discipline to the existing probes/runs (launcher sources `~/.secrets/onit/*`, Python reads `os.environ` only, value never read/printed/persisted), and the saved transcript must be grepped clean of the API key and endpoint IP before writing (ticket 04 precedent). Additionally, the mode indicator is a security property (Requirements: "the autonomy level is a security property"): the no-optimistic-update rule in Action item 1 exists so the prompt never lies about the session's autonomy level.

## Shared Context
- **Restated request:** At the `➤` prompt, bind Tab (currently unbound, silently dropped) to cycle the session's model approach: **plan → edit → auto → plan**, where plan = dsh plan mode ON, edit = default mode (plan off, approval at the profile default/ask), auto = approval `never`. Follow the repo's MVP convention: the pre-flight probe is its **own developer ticket** with a stop-and-escalate rule, and the feature is a **separate ticket blocked on the probe's findings**; a test-engineer audit ticket follows the scrollback convention (plan sets `Test pass: required`).
- **Requirements:**
  - Tab at the prompt is the only entry point to mode changes: explicit user action, no defaults changed silently, no mid-turn switching.
  - The prompt-row indicator must always show the **last confirmed** runtime state — never an optimistic guess (the autonomy level is a security property).
  - Whatever the probe finds reachable is shipped; unreachable states are documented, never shipped as dead UI.
- **Feature:** Tab-key model-approach toggle (plan/edit/auto) driven by dsh's plan-mode command and approval-policy session knob, with a mode-tagged prompt indicator.
- **Repo:** `/home/bertie/Documents/projects/dsh-tui` (git; branches `master`, `nightly`, remote `origin/master`; prior features committed to `master`).
- **Documentation pass: required** — rationale: the README grows beyond usage + limitations + growth table into real feature documentation (three autonomy states, indicator semantics, what `auto` disables, model-side plan-exit behavior), which is PLAN.md's stated flip condition from skip. The pass must check the README against the probe verdicts (no claim about a state the client can't reach; startup-default claim matches (b)).
- **PR strategy (this ticket):** PR 2 (tickets 02+03) = the feature — `dsh_tui/*` + `README.md` + transcript. Stays open until the test-engineer audit (03) and both review passes close; the audit's test additions land in PR 2, not a third PR.
- **Environment:**
  - Runtime venv: `investigation/probe/.venv` (Python 3.14, `deepseek-harness-sdk` 0.1.5rc1 + rich + pytest). Live-endpoint probes source secrets from `~/.secrets/onit/{host_address,api_key}.txt` exactly as `scripts/run-dsh-tui.sh` / `investigation/probe/run_probe.sh` do (never print or persist the key; Python reads `os.environ` only).
  - Developer agents run under a workspace-write file sandbox: writes confined to the repo; reads elsewhere expected to work; report a sandbox denial rather than working around it.
  - This whole flow runs inside a live dsh session that lives in the repo's `.dsh-home` — probes must use the probe-only DSH_HOME and never touch `.dsh-home`.

## Execution Log

<!-- Appended to by every dispatched agent (developer / test-engineer / tech-lead). Prior entries are never rewritten. -->
