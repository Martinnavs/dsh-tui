> **Superseded.** This was a hand-drafted plan (mimicking tech-lead's format,
> not produced by the agent) from the investigation-only pass. The real
> `tech-lead` agent was later dispatched to critique and reframe it around
> the Atomic MVP philosophy (`onit/docs/design-guide/06-mvp-fast-path.md`,
> `another-harness/HANDOFF.md`) — see `../PLAN.md` for the current plan.
> Kept here for the record; tech-lead's response explains where and why it
> diverged from this draft, including two real defects this draft missed
> (`decode_rate` is a guaranteed crash, not "trivial to fix or drop"; three
> hardcoded onit-branding strings aren't constructor-configurable).

# Implementation Plan: Terminal UI for dsh, ported from onit (superseded draft)

*(Written in tech-lead's plan format/rigor per user request — not produced
by invoking the tech-lead agent. Investigation backing every claim below is
in `investigation/NOTES.md`.)*

## Requirements

Give dsh (`deepseek-ai/deepseek-harness`) an interactive terminal chat UI
equivalent to `onit`'s, since dsh ships none. Reuse onit's existing
`ChatUI` renderer rather than building one from scratch, driven by dsh's
own agent loop (tools, sessions, plugin ecosystem) instead of onit's.
Investigation-only scope this round — no implementation yet.

## Repo / Feature / Files / Acceptance Criteria / Non-Goals

- Repo/area: new local project, `dsh-tui/` (not yet a git repo)
- Feature: interactive terminal chat client for dsh's `sdk` profile, UI
  ported from `onit/src/ui/text.py`
- Files: `dsh_tui/chat_ui.py`, `dsh_tui/driver.py`, `dsh_tui/cli.py`,
  `dsh_tui/settings.yaml.template`, `README.md`
- Acceptance criteria: interactive terminal chat against dsh's `sdk`
  profile pointed at the onit/Qwen vLLM endpoint; tool calls/results
  rendered inline the way onit renders them; multi-turn conversation
  within one process run; Ctrl+C/EOF exits cleanly; Apache-2.0 attribution
  preserved on the ported file
- Non-goals: real token-level streaming (confirmed unavailable through the
  documented SDK surface — approximated only, see Risks); session
  persistence/resume across process restarts; voice/web/gateway modes;
  matching onit's full config system (`onit setup` wizard, OS-keychain
  credential storage, `--data-path`/sandbox flag surface) — v1 is
  single-process, single-session, env-var-configured only

## Developer Tasks

### Task 1: Port ChatUI (Files: `dsh_tui/chat_ui.py`)

- Action: copy `onit/src/ui/text.py` into `dsh_tui/chat_ui.py` near
  verbatim. Remove the one onit-specific import
  (`from src.model.serving.chat import decode_rate`, used only inside
  `stream_end()` to format a tok/s footer) and replace with an inline
  rate calculation from the same `self._metrics` dict `set_metrics()`
  already receives. Keep the Apache-2.0 header; add a note that the file
  is adapted from `sibyl-oracles/onit`'s `src/ui/text.py`, unchanged
  except that one import.
- Established (verified): full required `ChatUI` interface enumerated by
  cross-referencing every `chat_ui.<method>` call site in onit's
  `chat.py` against `ChatUI`'s own method list — every one present. The
  three methods `chat.py` calls that `ChatUI` lacks
  (`start_tool_batch`/`end_tool_batch`/`set_turn_context`) belong only to
  onit's separate `StreamingAdapter` and are `hasattr()`-guarded at every
  call site — not part of the terminal contract. (`investigation/NOTES.md`
  "Established facts")
- Must verify: after removing the `decode_rate` import, grep the file for
  any other `from src.` / `from .` cross-package reference to confirm it
  is truly the only one.
- Unknowns: none blocking.
- Dependencies/license: adds `rich` (already an onit dependency, MIT-ish
  permissive, no conflict). Apache-2.0 attribution must be retained per
  §4b (state changes made).
- Symbols: `ChatUI` class, `Message` dataclass (`onit/src/ui/text.py`);
  `decode_rate` (`onit/src/model/serving/chat.py`, read-only reference).
- Acceptance criteria: `chat_ui.py` imports standalone with zero
  dependency on any `onit`/`src.*` package; `ChatUI()` instantiates and
  renders its welcome panel with no other module present.
- Non-goals: any change to ChatUI's rendering or behavior beyond the one
  import fix.
- Security-sensitive: no — pure rendering code, touches no credentials.

### Task 2: Event-mapping driver (Files: `dsh_tui/driver.py`)

- Action: write the `on_notification` callback that maps dsh
  `session.event` types to `ChatUI` calls, evolving
  `investigation/probe/notification_probe.py`'s callback:
  - `tool/call` → `add_tool_call()` + `show_tool_start()` (+
    `start_tool_spinner()`)
  - `tool/result` → `stop_tool_spinner()` + `add_tool_result()` +
    `show_tool_done()` (`success = not data.message.content[0].isError`;
    text from `data.message.content[0].content[0].text`; tool name
    resolved by matching `toolCallId`/`callId` back to the buffered
    `tool/call`)
  - `assistant/message` → skip if the text is empty (matches `ChatUI`'s
    own existing empty-block skip in `stream_end()`), else
    `stream_start()` → throttled `stream_token()` reveal → `stream_end()`
    (explicitly an approximation of streaming, not real streaming — see
    Risks)
  - `turn/end` → turn-timing bookkeeping
  - `system/message`, `user/message`, `request/header`, `request/context`,
    `session/title`, `agent/inbox/spliced`, `step/start`, `step/end` →
    ignored; internal bookkeeping with no `ChatUI` equivalent needed
- Established (verified): exact payload shapes for `tool/call` and
  `tool/result` captured live
  (`investigation/probe/probe-output-2-tool.txt`); `assistant/message`
  arrives as one complete-message event, confirmed twice
  (`probe-output-1-no-tools.txt`, `probe-output-2-tool.txt`), consistent
  with the persisted event catalog (`known-event-types.ts`, no delta
  type) and the "assistant messages embed their exact provider stream"
  architecture note.
- Must verify: reasoning/thinking event shape (open unknown — needs a
  reasoning-eliciting probe before wiring `stream_think_token`); whether
  a second `.run()` call against an already-open session continues the
  same conversation (needed for Task 3's multi-turn loop; neither probe
  tested a second turn).
- Unknowns: reasoning-stream shape; interrupt/cancellation semantics over
  the SDK (`investigation/NOTES.md` "Remaining unknowns").
- Dependencies/license: none beyond `deepseek-harness-sdk` (already
  installed and exercised in `investigation/probe/.venv`).
- Symbols: `deepseek_harness.api.Session.run`,
  `deepseek_harness.client.HarnessClient.session_prompt`,
  `deepseek_harness.models.Notification`.
- Acceptance criteria: replaying `probe-output-2-tool.txt`'s exact event
  sequence offline through the driver produces the same `ChatUI` call
  sequence onit's own `chat.py` would for an equivalent tool-calling turn
  (tool block, then answer block, empty first `assistant/message`
  correctly skipped).
- Non-goals: real streaming (throttled-reveal only, must be labeled as
  such in code/UI, not presented as true streaming); reasoning-token
  rendering (deferred pending the open unknown — degrade gracefully,
  show no thinking block, rather than guess at an unconfirmed shape).
- Security-sensitive: no — no new credential handling beyond Task 3.

### Task 3: Entry point / CLI (Files: `dsh_tui/cli.py`,
`dsh_tui/settings.yaml.template`, `README.md`)

- Action: argument parsing for provider/model/session-id passthrough;
  bootstrap `$DSH_HOME/settings.yaml` from a generalized version of
  `investigation/probe/settings.yaml.template` (provider id, model,
  base URL become real template variables instead of hardcoded
  `onit`/`Qwen/Qwen3.8-27B`); secret sourcing follows the exact
  discipline already established in `another-harness/scripts/run-onit.sh`
  — a shell wrapper exports env vars from the secret files, and Python
  only ever reads `os.environ`, never the files directly. Main loop:
  `ChatUI.get_user_input()` → `DeepSeekHarness.run(task, session_id=<one
  fixed id per process>, on_notification=driver.callback)` → repeat until
  EOF/Ctrl+C (both already handled by `ChatUI.get_user_input`, which
  returns `"\\bye"` on either).
- Established (verified): this exact `settings.yaml` schema and
  provider/model wiring already works end-to-end against the live
  endpoint — both probes succeeded through it. The env-var-only secret
  discipline is the established local convention
  (`another-harness/scripts/run-onit.sh`, this session).
- Must verify: whether a second `.run()` call on the same open
  `DeepSeekHarness`/`session_id` behaves as a continued conversation —
  shared with Task 2's must-verify, neither probe ran a second turn.
- Unknowns: session-resume-across-restart (resolved by scoping it as a
  Non-goal, not by verifying it); interrupt handling (shared with Task 2).
- Dependencies/license: none new.
- Symbols: `deepseek_harness.api.DeepSeekHarnessConfig`,
  `DeepSeekHarness.run`/`start_session`.
- Acceptance criteria: `python -m dsh_tui` starts an interactive loop; a
  two-turn conversation ("what's 2+2" then "what did I just ask")
  demonstrates the second answer uses the first turn's context within one
  process run.
- Non-goals: `onit setup`-equivalent config wizard; OS-keychain credential
  storage; cross-restart session resume; onit's `--data-path`/sandbox
  flag surface.
- Security-sensitive: yes — reads the API key from environment; must
  never log or print it. Mirrors the discipline already precedented in
  `run-onit.sh` and both probe scripts this session.

## Test Boundaries

- Each task owns its own verification (Task 1: standalone import check;
  Task 2: offline replay against the already-captured probe output;
  Task 3: live two-turn conversation) — scope is small enough that no
  shared test fixture is needed.
- No test-engineer pass: single porting-task chain, no parallel file
  ownership across developers, no security surface beyond the env-var
  credential hygiene already precedented and reused as-is in this
  session (not newly designed).

## Risks

- No real token-level streaming, confirmed empirically (two probes) and
  architecturally (event catalog has no delta type) — mitigation: Task 2's
  throttled client-side reveal approximates it; must stay clearly labeled
  as an approximation, not presented as true streaming.
- vLLM request-shape quirks for this backend (`supportsDeveloperRole`,
  `maxTokensField`) — mitigation: carry the same `compat` block the
  earlier Web-UI wizard and both probes already use; re-check if a later
  change (e.g. turning on `reasoning_effort`) triggers the failure mode
  that block guards against.
- Reasoning-stream shape unconfirmed — mitigation: Task 2 ships without
  `stream_think_token` wiring until confirmed; degrades to "no thinking
  block shown" rather than a wrong guess.
- dsh is developer-preview, unaudited software running a full tool
  roster (bash, file write) — same host-access posture onit itself
  already carries, not a new exposure introduced by this plan.

- Test pass: skip — rationale: no production behavior, data, public
  interface, or externally observable configuration impact beyond a new
  standalone local tool with no other consumers; manual two-turn
  verification specified per task covers the stated acceptance criteria.
- Documentation pass: skip — rationale: `investigation/NOTES.md` plus this
  plan already document the design and findings; Task 3 includes a
  `README.md` for basic usage as part of the task itself, not as a
  separate documentation pass.
- PR strategy: not applicable — `dsh-tui/` is not yet a git repository.

## Review-Finding Handling

Surface and wait for human approval before each R1/R2 retry; never
auto-fix.

**WAITING FOR CONFIRMATION**
