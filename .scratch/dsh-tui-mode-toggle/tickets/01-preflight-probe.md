# 01: Pre-flight probe — mode switch + approval-policy wire surface (blocks Task 02)

**Repo:** /home/bertie/Documents/projects/dsh-tui
**Blocked by:** None (can start immediately)
**Status:** ready-for-agent

## Files
- investigation/probe/mode_probe.py (new)
- investigation/probe/run_probe4.sh (new)
- investigation/probe/probe-output-4-*.txt (new raw captures)
- investigation/NOTES.md (append-only)

## Action
Write `investigation/probe/mode_probe.py` (standalone, same shape as `notification_probe.py`, same isolated probe DSH_HOME, same secret discipline, uuid4-suffixed session ids) + `investigation/probe/run_probe4.sh` (verbatim `run_probe3.sh` pattern — pre-authorized in the Files list; MVP ticket 01 needed exactly this extra file, so don't re-derive the justification). One process resolves, with raw evidence:

- **(a)** Is there a client-side JSON-RPC method to run `/plan on` / `/plan off` for a live session? Enumerate candidate method names from the runtime binary's strings (the documented technique; `ctx.remote.commands.execute` shows the server-side call), try each via `client.request(..., timeout_seconds=10)` with the permissive empty response model, and record which return data vs. `-32601`. Record the exact method name, params, response shape, and whether the state is session-scoped and **persists across subsequent `session/prompt` turns** (a follow-up turn must reflect the switch).
- **(b)** The sdk profile's effective approval behavior + settable knob. (b1) Send the tool-triggering turn as in `probe-output-2-tool.txt` and check whether any runtime→client **request** (approval) arrives — use a **bounded non-blocking peek loop** on `client._requests` (private access is fine in probe code; `next_request()` blocks and would hang the probe if nothing arrives). (b2) Try candidate approval-policy setter methods the same way as (a); record which work, with what params (`ask`/`never`), and the verdict on the profile's effective default (`ask` or `never`).
- **(c)** With plan mode ON (via (a)), send a trivial file-editing task and capture the full stream: how plan text arrives (assistant vs system message), and what `exit_plan_mode` does on a stdio client with no interactive review — completes, **fails cleanly, or hangs** (hang = escalate, per Unknowns).
- **(d)** After each switch, record the raw RPC response **and** any client-observable `session.event` (a `plan/mode` projection event, or `system/message` such as "The user switched this session to plan mode.") via a bounded post-request notification drain.
- **(e)** Are slash commands typed as plain message text interpreted by the runtime, or only via the (a) method? (Cheap; informs the README's honest framing.)

Save raw streams as `investigation/probe/probe-output-4-*.txt`; append a dated "Probe 4" subsection to `investigation/NOTES.md` in the established (a)/(b)/… verdict style, updating "Remaining unknowns".

## Stop-and-escalate
**Stop-and-escalate rule** (ticket 04 precedent): if (a) is negative — plan mode cannot be toggled from the client — **stop and escalate; do not build UI for a knob the client cannot turn**. If (c) shows a hang, stop and escalate. If (a) positive but (b) shows the policy is not client-settable: **no escalation** — apply Task 02's fallback rule (ticket 02; pre-approved scope reduction). Report every verdict explicitly.

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
Findings (a)–(e) (above), each with raw stream evidence.

## Unknowns
- Exact wire method name/params for the plan switch and the approval-policy setter (binary proves the mechanisms exist server-side; the stdio surface is unverified) — Task 01 exists to close this.
- Whether the sdk profile's effective approval default is `ask` or `never` (evidence says `never`; confirm, don't assert).
- What `exit_plan_mode` does on a stdio client: fails cleanly, hangs, or completes (Task 01 (c)) — a **hang** is escalation-worthy (it would wedge the app); a clean failure is a documented limitation.
- Whether a mode switch emits any client-observable `session.event` (Task 01 (d)).

## Dependencies/license
None new. Probe reuses `investigation/probe/.venv`; feature uses only stdlib + already-required `rich`/`pydantic` (transitively via the SDK). No new packages, no new licenses. The only attribution duty: **`core.py`'s §4b change list gains item 7** (Tab hook + prompt attribute are upstream deltas to ported code).

## Symbols
`deepseek_harness.api.DeepSeekHarness`, `Harness.start_session`, `Harness.client` property; `deepseek_harness.client.HarnessClient.request`, `next_request`, `_requests`, `respond`; `deepseek_harness.errors.JsonRpcError`, `TransportClosedError`; `investigation/probe/notification_probe.py`, `investigation/probe/run_probe3.sh`, `investigation/probe/multiturn_probe.py` (shape references); `investigation/NOTES.md` "Remaining unknowns".

## Acceptance Criteria
- [ ] All of (a)–(e) answered with raw evidence saved
- [ ] `investigation/NOTES.md` updated
- [ ] (a) verdict stated clearly (it gates Task 02)
- [ ] Stop-and-escalate respected

## Non-Goals
- Wiring anything into `ChatUI`
- Touching the repo's `.dsh-home` or `dsh_tui/settings.yaml.template`
- Changing provider/model
- Fixing `exit_plan_mode` (record only)

## Security-sensitive
yes — live endpoint + API key; identical discipline to the existing probes (launcher sources `~/.secrets/onit/*`, Python reads `os.environ` only, value never read/printed/persisted). Developers run under a workspace-write sandbox: reads of `~/.secrets` are expected to work; **report a sandbox denial rather than working around it**.

## Shared Context
- **Restated request:** At the `➤` prompt, bind Tab (currently unbound, silently dropped) to cycle the session's model approach: **plan → edit → auto → plan**, where plan = dsh plan mode ON, edit = default mode (plan off, approval at the profile default/ask), auto = approval `never`. Follow the repo's MVP convention: the pre-flight probe is its **own developer ticket** with a stop-and-escalate rule, and the feature is a **separate ticket blocked on the probe's findings**; a test-engineer audit ticket follows the scrollback convention (plan sets `Test pass: required`).
- **Requirements:**
  - Tab at the prompt is the only entry point to mode changes: explicit user action, no defaults changed silently, no mid-turn switching.
  - The prompt-row indicator must always show the **last confirmed** runtime state — never an optimistic guess (the autonomy level is a security property).
  - Whatever the probe finds reachable is shipped; unreachable states are documented, never shipped as dead UI.
- **Feature:** Tab-key model-approach toggle (plan/edit/auto) driven by dsh's plan-mode command and approval-policy session knob, with a mode-tagged prompt indicator.
- **Repo:** `/home/bertie/Documents/projects/dsh-tui` (git; branches `master`, `nightly`, remote `origin/master`; prior features committed to `master`).
- **PR strategy (this ticket):** PR 1 (ticket 01) = probe artifacts only — `investigation/probe/*` + `NOTES.md` append. No product code; mergeable on its own; records the findings that determine PR 2's scope.
- **Environment:**
  - Runtime venv: `investigation/probe/.venv` (Python 3.14, `deepseek-harness-sdk` 0.1.5rc1 + rich + pytest). Live-endpoint probes source secrets from `~/.secrets/onit/{host_address,api_key}.txt` exactly as `scripts/run-dsh-tui.sh` / `investigation/probe/run_probe.sh` do (never print or persist the key; Python reads `os.environ` only).
  - Developer agents run under a workspace-write file sandbox: writes confined to the repo; reads elsewhere expected to work; report a sandbox denial rather than working around it.
  - This whole flow runs inside a live dsh session that lives in the repo's `.dsh-home` — probes must use the probe-only DSH_HOME and never touch `.dsh-home`.

## Execution Log

<!-- Appended to by every dispatched agent (developer / test-engineer / tech-lead). Prior entries are never rewritten. -->
