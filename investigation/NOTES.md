# Investigation: porting onit's terminal UI onto dsh

Session trail for "can we pull onit's TUI and repurpose it onto dsh, and is
that hard." Written up in tech-lead's plan style (established facts with how
they were verified, symbol pointers instead of pasted code, explicit
unknowns) — this is a human-authored investigation record, not agent output.

## Starting premise

vLLM (serving `Qwen/Qwen3.8-27B` behind `~/.secrets/onit/{host_address,api_key}.txt`)
is a bare OpenAI-compatible inference endpoint: no session state, no tool
loop, no sandboxing. Confirmed reachable and authenticated:
`GET /v1/models` → `HTTP 200` (curl, this session, header value never
printed — see `probe/run_probe.sh` for the pattern used throughout).

A harness supplies the missing agent loop. Two are in play:

- **dsh** (`deepseek-ai/deepseek-harness`) — plugin-architecture harness,
  the one the user wants to end up on.
- **onit** (`sibyl-oracles/onit`, Apache-2.0) — already cloned at
  `../onit/`, already installed and working (`../another-harness/.venv-onit/`),
  already wired to this same Qwen endpoint via
  `../another-harness/scripts/run-onit.sh` (built earlier this session).
  Has a real, working terminal chat UI. dsh does not.

## Established facts

**dsh ships no interactive terminal chat surface.**
How verified: fetched the full repo file tree via the GitHub API
(`git/trees/master?recursive=1`). Shipped apps: `apps/cli`, `apps/desktop`,
`apps/web` only. Shipped bundles under `packages/bundle/`: `acp-app`,
`base`, `headless`, `sdk-app`, `sdk-minimal`, `web-app` only — no `tui`.
`packages/bundle/headless/README.md` states outright: *"Avoid it when you
need a multi-turn interactive session or a GUI; the browser surface serves
that."* The CLI docs' own example of installing a community TUI
(`dsh plugin --profile tui add github:deepseek-harness/turtle-ui`) points at
a repo that returns `404` — checked directly against the GitHub API.
Searched the `dsh-plugin` GitHub topic and npm for a real community TUI;
nothing under the actual `@deepseek-ai` org, only unrelated/squatted names.

**onit's `ChatUI` (`../onit/src/ui/text.py`, ~1600 lines, Apache-2.0) is not
fused to onit's agent internals — it's a closed, callback-driven renderer.**
How verified: read the full file. Cross-referenced every `chat_ui.<method>`
call site in `../onit/src/model/serving/chat.py` (the actual turn loop)
against `ChatUI`'s method definitions. Full required contract, all present
on `ChatUI`: `add_log`, `add_tool_call`, `add_tool_result`,
`show_tool_start`, `show_tool_done`, `start_tool_spinner`,
`stop_tool_spinner`, `tool_log`, `tool_progress`, `stream_start`,
`stream_token`, `stream_think_token`, `stream_end`, `model_name` (attr),
`set_context_usage`, `set_metrics`, `show_context_compaction`,
`show_turn_limit`, `verification_start/end/correction`,
`get_user_input`/`get_user_input_async`. Three more methods
(`start_tool_batch`, `end_tool_batch`, `set_turn_context`) are called by
`chat.py` but are *not* on `ChatUI` — confirmed they belong only to
`StreamingAdapter` (`onit.py:312`, onit's own web/A2A streaming adapter,
same file), and every call site is guarded by `hasattr(chat_ui, ...)`
(`chat.py:2592`, `2613`, `2631`, `3384`) — i.e. these are optional
progress-narration extras the terminal UI has never needed.

**`ChatUI` has exactly one onit-specific coupling.**
`stream_end()` imports `decode_rate` from
`src.model.serving.chat` to format a tok/s footer from a `self._metrics`
dict populated via `set_metrics()`. One import, one call site
(`text.py` ~line 1060). Trivial to replace with an inline rate calculation
or drop the footer.

**onit is Apache-2.0, no separate NOTICE file.**
`../onit/LICENSE` is the standard Apache 2.0 text; no `NOTICE` file exists
in the repo (`find -iname 'NOTICE*'` — empty). Reuse requires keeping the
license header/attribution and noting what was changed (§4b) — not more.

**dsh's `sdk` profile has comparable tool coverage to onit, different
names.** `packages/bundle/base/README.md`: "file editing, shell commands,
web search, public HTTP(S) fetch, subagents, task and goal tracking."
onit's roster (`../onit/docs/TOOLS.md`): `bash`, `read_file`/`write_file`/
`edit_file`, `search`, `get_weather`, `grep`, `github_repo`,
`local_search`, etc. Different tool names, similar reach — not a blocker,
just something the driver's tool-name mapping needs to account for later
if `ChatUI`'s rendering ever branches on tool name (it currently doesn't;
it renders generically by name+args).

**dsh's Python SDK (`deepseek-harness-sdk`) is self-contained** — installs
its own bundled runtime binary wheel, no separate Node/npx `dsh` install
needed. `client.py`'s `session_prompt(..., on_notification=callback)` fires
the callback live during the run, not just after — a real streaming
substrate, at whatever granularity dsh actually emits.

**The custom-provider config (`llm-pi-ai` / `$DSH_HOME/settings.yaml`) is
shared infrastructure across every dsh surface** — the same schema used for
the earlier Web-UI wizard works unchanged for the SDK. Confirmed by running
real turns through `provider="onit"` end to end (see Probes below).

### Probes — the empirical check the user asked for

Two real runs against the live Qwen/vLLM endpoint, through dsh's actual
`sdk` profile (not simulated), via
`probe/notification_probe.py` + `probe/run_probe.sh`. Isolated
`DSH_HOME` at `probe/dsh-home/`; `settings.yaml` rendered from
`probe/settings.yaml.template` with the real base URL substituted only
inside the launcher script's own process (never read/printed by the
agent — same discipline as the earlier wizard). Full raw output saved in
`probe/probe-output-1-no-tools.txt` and `probe/probe-output-2-tool.txt`.

**Probe 1 — plain turn, no tools** ("Count from 1 to 10, one number per
line."): 14 `session.event` + 2 `session.status` notifications total.
Exactly **one** `assistant/message` event, carrying the complete 22-char
answer. No other notification method appeared.

**Probe 2 — tool-invoking turn** ("Use your bash tool to run: echo
hello-from-dsh-tool"): confirms the full per-event payload shapes:

- `tool/call` → `data: {turn, step, callId, name, arguments: <JSON string>}`
- `tool/result` → `data: {turn, step, message: {source: {kind:"tool", callId}, content: [{type:"tool-result", toolCallId, content: [{type:"text", text}], isError}], role:"user", id}}`
- Two `assistant/message` events this time — one **empty** (0 chars, the
  tool-calling step has no visible prose) then one with the final 78-char
  answer after the tool result came back. `ChatUI.stream_end()` already
  special-cases an empty streamed block (skips it) — matches for free.
- Confirms dsh's multi-step-per-turn loop shape (step 1: tool call, step 2:
  final answer) matches onit's own tool-loop shape closely enough for
  `ChatUI`'s existing tool-call/tool-result rendering to apply unchanged.

**Conclusion: dsh's SDK notification channel does not expose token-level
streaming deltas.** One `assistant/message` event = one complete message,
confirmed twice, both matching the durable event catalog
(`packages/core/session/src/known-event-types.ts`, fetched from source —
no delta/chunk event type exists in the 47-entry set). The architecture
note `2026-09-06-v3-canonical-session-envelopes.md` independently confirms
this by design: *"Assistant messages embed their exact provider stream"* —
the raw stream is embedded as data inside one event once complete, not
trickled out. dsh's own web UI clearly streams live in the browser
(`web-app/README.md` references a live "WebSocket stream" in its auth
model) — that almost certainly uses a separate transport, not the stdio
JSON-RPC channel this SDK exposes. Not proven reachable from Python.

**Practical consequence:** `ChatUI.stream_token()`'s live typewriter effect
has no matching live source through the documented SDK surface. Worst case
without further work: spinner while the turn runs, then the full answer
lands at once. Everything else — tool-call/tool-result blocks, message
history, input handling — maps cleanly.

## Symbols (pointers, not full reads needed downstream)

- `../onit/src/ui/text.py` — `ChatUI` class, to port near-verbatim.
- `../onit/src/model/serving/chat.py` — reference only, for the exact
  call sequence/contract; not executed, not copied (dsh's own agent loop
  replaces this file's job).
- `python/sdk/src/deepseek_harness/api.py` (deepseek-harness-sdk package,
  installed at `probe/.venv/lib/python3.14/site-packages/deepseek_harness/`)
  — `DeepSeekHarness`, `Session.run()`, `RunResult`.
- `python/sdk/src/deepseek_harness/client.py` — `HarnessClient`,
  `session_prompt(on_notification=...)`.
- `probe/notification_probe.py` — working reference implementation of
  "listen to dsh's live notification stream and inspect event shapes";
  the eventual driver's `on_notification` callback is a direct evolution
  of this file's `on_notification()`.

## Remaining unknowns (must-verify before/while building)

- **Reasoning/thinking stream.** The original curl response carried a
  separate `reasoning` field; neither probe elicited visible reasoning
  (prompts were too simple / model may not have reasoned). Whether
  reasoning arrives as its own event type (needed to drive
  `stream_think_token`) is unconfirmed — needs a probe with a
  reasoning-eliciting prompt and `reasoning_effort` set.
  Alternative if it doesn't cleanly break out: check the encoded prompt
  wrapping the model's output for something like `<think>` tags, which
  `ChatUI`/`chat.py` already parse client-side (`_filter_display_token`,
  `<answer>`/`<think>` stripping) — dsh may hand back a similarly-wrapped
  single string rather than a separate channel.
- **Session resume across process restarts.** onit defaults to resuming
  the last session on bare `onit`. Whether reusing a `session_id` across
  separate `DeepSeekHarness` launches (not just within one live process)
  replays durable history the same way is unconfirmed from the SDK docs
  alone.
- **Interrupt/Ctrl+C mid-turn.** `ChatUI.get_user_input` raises
  `KeyboardInterrupt`/`EOFError` to stop the loop; onit's `chat()` has its
  own mid-generation stop handling. The SDK-side equivalent (does
  `session_prompt` support cancellation, or does the caller just stop
  reading notifications and leave the run orphaned server-side?) is
  unconfirmed.
- **Whether an undocumented delta channel exists but wasn't triggered.**
  Two short-answer probes is decent but not exhaustive evidence. A
  longer-output probe (several paragraphs) would make length-based
  chunking show up if it exists and wasn't visible at 22–78 chars.

## Risks

- **No real token streaming** — direct UX regression vs. onit's live
  typewriter effect. Mitigation: fake a client-side throttled reveal of
  the final message per turn (print at a steady rate) to approximate the
  feel without real backpressure; explicitly a workaround, not a fix, and
  should be labeled as such in the UI/code rather than presented as true
  streaming.
- **vLLM compat quirks** — the earlier Web-UI wizard needed
  `compat.supportsDeveloperRole: false` / `compat.maxTokensField: max_tokens`
  for this same backend; carried into `probe/settings.yaml.template`
  preemptively but not stress-tested against a request shape that would
  actually trigger the failure mode.
- **dsh is developer-preview, unaudited** (its own `SAFETY.md`) — running
  the full `sdk` profile's tool roster (bash, file write) against a real
  workspace carries the same host-access caveat onit itself already
  carries; not a new risk, just inherited.
- **License attribution** — must keep `text.py`'s Apache-2.0 header/notice
  and record what was changed when porting it.
