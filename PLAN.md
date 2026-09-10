# Implementation Plan: dsh-tui — Atomic MVP terminal chat for dsh, ported from onit

*(Produced by the real `tech-lead` agent in Plan mode, dispatched to
critique and reframe the prior hand-drafted plan around the Atomic MVP
philosophy. See `investigation/PLAN-DRAFT.md` for the superseded draft and
where this diverges from it.)*

## Requirements

Give `dsh` (`deepseek-ai/deepseek-harness`) an interactive terminal chat UI, which it ships none of, by reusing onit's already-working `ChatUI` renderer driven by dsh's own agent loop.

Reframed per the user's request around **the Atomic MVP** (`/home/bertie/Documents/projects/onit/docs/design-guide/06-mvp-fast-path.md`) and the sibling precedent at `/home/bertie/Documents/projects/another-harness/HANDOFF.md`: define the smallest slice that is a *real, running* terminal chat loop end to end, and explicitly defer everything else into a growth table rather than bundling it into v1.

The atomic slice here is not a stripped-down UI. It is onit's `ChatUI` ported **whole** — all ~1644 lines, every method — with the driver wiring only the subset needed for a working loop. Same property as onit's own MVP: the MVP is a **scope choice about which methods the driver calls**, not a fork. Every deferred capability (think blocks, metrics footer, context bar, compaction/turn-limit notices, verification UI, logs panel) already exists in the ported file, so growing it later is *calling one more method*, never a second implementation to reconcile with the first.

This supersedes the framing in `/home/bertie/Documents/projects/dsh-tui/investigation/PLAN-DRAFT.md` (the prior hand-drafted pass). The investigation backing it — `/home/bertie/Documents/projects/dsh-tui/investigation/NOTES.md` — stands unchanged and is not re-litigated.

## Repo / Feature / Files / Acceptance Criteria / Non-Goals

- **Repo/area:** `/home/bertie/Documents/projects/dsh-tui/` — local project, not a git repo yet.
- **Feature:** interactive terminal chat client for dsh's `sdk` profile, UI ported near-verbatim from `/home/bertie/Documents/projects/onit/src/ui/text.py`.
- **Files:**
  - `/home/bertie/Documents/projects/dsh-tui/investigation/probe/multiturn_probe.py` (Task 1)
  - `/home/bertie/Documents/projects/dsh-tui/dsh_tui/chat_ui.py` (Task 2)
  - `/home/bertie/Documents/projects/dsh-tui/dsh_tui/driver.py`, `dsh_tui/tests/test_replay.py` (Task 3)
  - `/home/bertie/Documents/projects/dsh-tui/dsh_tui/cli.py`, `dsh_tui/__main__.py`, `dsh_tui/settings.yaml.template`, `scripts/run-dsh-tui.sh`, `README.md` (Task 4)
- **Acceptance criteria (the atomic slice, all of it):**
  1. `scripts/run-dsh-tui.sh` opens an interactive terminal chat against dsh's `sdk` profile pointed at the onit/Qwen vLLM endpoint.
  2. A **two-turn** conversation demonstrably carries context (turn 2 answers about turn 1) within one process run.
  3. Tool calls and tool results render inline as onit renders them — the agent never edits a file or runs `bash` invisibly.
  4. A spinner runs while a turn is in flight; the loop never looks hung.
  5. Ctrl+C / EOF at the prompt exits cleanly.
  6. Apache-2.0 attribution preserved on the ported file, with changes recorded per §4b.
- **Non-goals (v1) — all deferred to the growth table below, none of them a rewrite when picked up:**
  real token-level streaming (confirmed unavailable through the documented SDK surface) *and* its throttled client-side approximation; reasoning/think-block rendering; tok/s metrics footer; context-usage bar and context-full warnings; compaction and turn-limit notices; verification/fact-check UI; the execution-logs panel; async input; cross-restart session resume; mid-turn cancellation; a config wizard, OS-keychain credentials, sandbox flags, or multi-provider config; **any change to `ChatUI`'s rendering behavior beyond the two de-onit fixes in Task 2.**

## Developer Tasks

Tasks 1 and 2 are independent and run in parallel. Task 3 needs Task 2's file. Task 4 needs Task 3 and Task 1's answer.

### Task 1: Pre-flight probe — close the one unknown the MVP's acceptance criteria rest on (Files: `investigation/probe/multiturn_probe.py`, appends to `investigation/NOTES.md`)

- **Action:** extend the existing probe harness (`investigation/probe/notification_probe.py` + `run_probe.sh`, same isolated `DSH_HOME`, same secret discipline) to run one process that:
  1. holds a single `Session` from `harness.start_session(id)` and calls `.run()` **twice** — "My favourite colour is teal." then "What is my favourite colour?" — recording whether turn 2 sees turn 1's context;
  2. issues one **long-output** prompt (several paragraphs) and reports the `assistant/message` event count and per-event char lengths;
  3. issues one **reasoning-eliciting** prompt and dumps every distinct `event.type` seen plus the raw text of any `assistant/message`, so a `<think>`-wrapped single string is distinguishable from a separate reasoning event type.
  Append the findings to `investigation/NOTES.md` under a new dated "Probe 3" subsection, in the same established-fact style.
- **Established (verified):** the SDK exposes `DeepSeekHarness.run(input, session_id=...)` which is literally `self.start_session(session_id).run(input, ...)` (`api.py:124-131`), and `Session` (`api.py:135`) is a reusable object holding `self.id` — so a two-turn probe on one `Session` is a three-line change to the existing probe. The probe harness, `settings.yaml.template`, isolated `DSH_HOME`, and the venv all already work end to end against the live endpoint (two successful runs, `investigation/probe/probe-output-*.txt`).
- **Must verify:** (a) does turn 2 on the same `Session` carry turn 1's context — **this gates acceptance criterion 2 and therefore the whole MVP**; (b) does a multi-paragraph answer arrive as one `assistant/message` or several (the "chunking might exist but wasn't triggered at 22–78 chars" unknown); (c) reasoning event shape, or `<think>`-wrapped text, or neither.
- **Unknowns:** all three above; this task exists to close them. If (a) comes back negative, **stop and escalate before Task 4** — the MVP's turn loop would need a different session strategy and that is a scope decision, not a developer choice.
- **Dependencies/license:** none new — reuses `investigation/probe/.venv`.
- **Symbols:** `deepseek_harness.api.DeepSeekHarness.start_session`, `Session.run`, `RunResult.final_response`; `probe/notification_probe.py:on_notification`.
- **Acceptance criteria:** all three questions answered with raw captured output saved alongside the existing probe outputs, and NOTES.md updated with the verdict on each.
- **Non-goals:** wiring anything into `ChatUI`; changing `settings.yaml.template`'s provider/model.
- **Security-sensitive:** yes — touches the live endpoint and the API key. Same discipline as the existing probes: the launcher shell script exports from `~/.secrets/onit/*`, Python reads only `os.environ`, the value is never read or printed by the agent.

### Task 2: Port `ChatUI` whole, de-onit it (Files: `dsh_tui/chat_ui.py`)

- **Action:** copy `/home/bertie/Documents/projects/onit/src/ui/text.py` to `dsh_tui/chat_ui.py` near-verbatim. Make exactly two behavioral changes, both mandatory, both recorded:
  1. **`decode_rate` (critical path, not optional).** `stream_end()` does `from src.model.serving.chat import decode_rate` at **line 1060**, and this import is **unconditional on every non-empty stream block** — it sits after the empty-block early-return at line 1050 but before any metrics guard. Left in place it raises `ModuleNotFoundError` on the *first assistant message the MVP ever renders*. Replace with an inline tok/s computation from the same `self._metrics` dict, returning empty/`None` when `set_metrics()` was never called (the MVP case) so `format_meta` simply omits the rate.
  2. **Hardcoded onit branding.** Three identical hardcoded strings — `"OnIt may produce inaccurate information. Verify important details independently."` at **lines 1133, 1544, 1554** — are not constructor-configurable. Hoist to a single class-level/constructor-supplied `disclaimer` attribute defaulting to a dsh-appropriate wording. (`agent_cursor="OnIt"` and `banner_title="OnIt Chat Interface"` at lines 78/85 are already constructor args — pass different values, do **not** edit them.)
  Keep the Apache-2.0 header verbatim (copyright "2025 Rowel Atienza", `text.py:1-15`) and add a short adaptation notice below it naming the source file, the upstream repo, and exactly these two changes.
- **Established (verified):** the full `ChatUI` contract onit's own turn loop uses is present on the class — enumerated by cross-referencing every `chat_ui.<method>` call site in onit's `chat.py` (NOTES.md, "Established facts"). The only cross-package import in the whole 1644-line file is line 1060 (`grep` of every `from`/`import`: everything else is stdlib or `rich`). `start_tool_batch`/`end_tool_batch`/`set_turn_context` belong to onit's separate `StreamingAdapter` and are `hasattr()`-guarded at every call site — not part of the terminal contract. onit is Apache-2.0 with no `NOTICE` file.
- **Must verify:** after the edit, `grep -n "from src\.\|import src\." dsh_tui/chat_ui.py` returns nothing; and `grep -in "onit" dsh_tui/chat_ui.py` returns only the attribution notice and the two constructor defaults.
- **Unknowns:** none blocking.
- **Dependencies/license:** adds `rich` (permissive MIT, already an onit dependency, no transitive or license conflict). Apache-2.0 §4b obligation: retain header, state changes — satisfied by the adaptation notice.
- **Symbols:** `ChatUI.__init__` (`text.py:75`), `stream_end` (`:1033`), `stream_token` (`:891`), `_emit` (`:899`), `get_user_input` (`:1537`), `start_thinking`/`stop_thinking` (`:607`/`:611`), `add_tool_call` (`:254`), `add_tool_result` (`:265`), `show_tool_start` (`:752`), `show_tool_done` (`:787`), `start_tool_spinner`/`stop_tool_spinner` (`:770`/`:773`).
- **Acceptance criteria:** `python -c "from dsh_tui.chat_ui import ChatUI; ChatUI(agent_cursor='dsh', banner_title='dsh Chat').render()"` runs with no `onit`/`src.*` package anywhere on the path; a scripted `stream_start()` → `stream_token("hello")` → `stream_end()` sequence prints a complete answer block **without** raising on the removed import.
- **Non-goals:** any rendering/behavior change beyond those two fixes; pruning unused methods (the unused methods *are* the growth path — deleting them is what would turn a growth row into a rewrite).
- **Security-sensitive:** no — pure rendering, touches no credentials.

### Task 3: MVP driver — event → `ChatUI` mapping (Files: `dsh_tui/driver.py`, `dsh_tui/tests/test_replay.py`)

- **Action:** an `on_notification` callback mapping dsh `session.event` types to `ChatUI` calls, evolved from `probe/notification_probe.py:on_notification`. **MVP wiring is exactly this and nothing more:**
  - `tool/call` → `add_tool_call(name, args)` + `show_tool_start(name, args)` + `start_tool_spinner(name, args)`; buffer `callId → name` (`arguments` arrives as a **JSON string**, parse it).
  - `tool/result` → `stop_tool_spinner()` + `add_tool_result(name, text)` + `show_tool_done(name, text, success=not isError)`; resolve the name by matching `toolCallId` back to the buffered `callId`; text from `data.message.content[0].content[0].text`.
  - `assistant/message` → extract text; if empty, do nothing (the tool-calling step legitimately emits a 0-char message — `ChatUI.stream_end()`'s own `_stream_header_printed` check at `text.py:1041` already skips empty blocks for free); else `stream_start()` → **one** `stream_token(full_text)` → `stream_end()`.
  - turn lifecycle → `start_thinking()` when a turn begins, `stop_thinking()` on first output, so the loop never looks hung while a turn runs.
  - `system/message`, `user/message`, `request/header`, `request/context`, `session/title`, `agent/inbox/spliced`, `step/start`, `step/end`, `turn/end` → ignored (no MVP `ChatUI` equivalent).
  Every deferred capability gets **no stub, no dead branch, no TODO scaffold** — the driver simply does not call those methods yet.
- **Established (verified):** exact `tool/call` and `tool/result` payload shapes captured live (`probe/probe-output-2-tool.txt`). `assistant/message` is one complete message, confirmed on two live runs plus the 47-entry event catalog (`known-event-types.ts`, no delta type) plus the "assistant messages embed their exact provider stream" architecture note. **The single-`stream_token` path is real, not a hack:** `stream_token` (`text.py:891`) and `_emit` (`:899`) — the tag/link filters are plain string operations with no per-token assumption, and the header prints on first non-whitespace content regardless of chunk count, so one whole-string call renders identically to N token calls.
- **Must verify:** replaying `probe-output-2-tool.txt`'s exact event sequence offline produces the tool block → answer block sequence with the empty first `assistant/message` correctly suppressed; and a `tool/result` with `isError: true` renders as a failure, not a success.
- **Unknowns:** none blocking the MVP. Reasoning shape and cancellation semantics are deferred by scope (growth table rows 1 and 6), not carried as latent risk in this file.
- **Dependencies/license:** none new beyond `deepseek-harness-sdk`, already installed and exercised.
- **Symbols:** `deepseek_harness.models.Notification`; `probe/notification_probe.py:on_notification` (reference implementation).
- **Acceptance criteria:** `test_replay.py` feeds the captured probe events through the driver against a recording stub and asserts the exact `ChatUI` call sequence, including the empty-message suppression and the `isError` branch. Runs offline, no network, no credentials.
- **Non-goals:** throttled/fake streaming; `stream_think_token`; `set_metrics`; `set_context_usage`; any tool-name-specific rendering (`ChatUI` renders generically by name+args, and dsh's tool names differ from onit's — generic rendering is what makes that a non-issue).
- **Security-sensitive:** no — parses event payloads only; the credential path lives entirely in Task 4.

### Task 4: Entry point, config, growth documentation (Files: `dsh_tui/cli.py`, `dsh_tui/__main__.py`, `dsh_tui/settings.yaml.template`, `scripts/run-dsh-tui.sh`, `README.md`)

- **Action:** generalize `probe/settings.yaml.template` so provider id, model, and base URL are real template variables instead of hardcoded `onit`/`Qwen/Qwen3.8-27B`, keeping the `compat.supportsDeveloperRole: false` / `compat.maxTokensField: max_tokens` block this backend needs. `scripts/run-dsh-tui.sh` exports secrets from `~/.secrets/onit/{host_address,api_key}.txt` following the established pattern in `/home/bertie/Documents/projects/another-harness/scripts/run-onit.sh`; Python reads `os.environ` only, never the files. Main loop: `harness.start_session(id)` **once per process**, then `ChatUI.get_user_input()` → `session.run(text, on_notification=driver)` → repeat until `"\bye"`. Write `README.md` containing usage, the **known limitations** (no live streaming — the answer lands complete; Ctrl+C mid-turn exits the client and may leave the run orphaned server-side; single session, no resume across restarts), and **the growth table below, verbatim**, so a future session enables a row instead of re-deriving it.
- **Established (verified):** this `settings.yaml` schema, provider wiring, and `compat` block already work end to end against the live endpoint — both probes succeeded through them. `ChatUI.get_user_input` returns `"\bye"` on **both** `EOFError` and `KeyboardInterrupt` (`text.py:1562-1565`), so acceptance criterion 5 is inherited for free with no signal handling to write. `Session` is reusable across `.run()` calls by construction (`api.py:135-184`).
- **Must verify:** Task 1's answer (a) — that a second `.run()` on the held `Session` continues the conversation. Do not start this task until Task 1 reports on it.
- **Unknowns:** mid-turn Ctrl+C behavior — resolved by *scoping* (documented limitation + growth row 6), not by pretending it is handled. It must be stated in the README, not left silent.
- **Dependencies/license:** none new.
- **Symbols:** `deepseek_harness.api.DeepSeekHarnessConfig`, `DeepSeekHarness.start_session` (`api.py:120`), `Session.run` (`api.py:139`).
- **Acceptance criteria:** MVP acceptance criteria 1–6 all demonstrated in one live run, with the two-turn context check (criterion 2) shown in real transcript output, not asserted.
- **Non-goals:** an `onit setup`-equivalent wizard; keychain storage; `--data-path`/sandbox flags; cross-restart resume; multi-provider selection.
- **Security-sensitive:** yes — the API key reaches the process via environment. It must never be logged, printed, echoed into `settings.yaml` in a world-readable location, or included in any error message. Mirrors the discipline already precedented in `run-onit.sh` and both probe scripts.

## Growth table — capability-driven, not calendar-driven

Mirrors `06-mvp-fast-path.md` §7. Every row is one more method call on a class **already in `dsh_tui/chat_ui.py`**, except the two marked ⚠ — which need an external answer first, and are called out honestly rather than implied to be free.

| You hit this | Do this | Read |
|---|---|---|
| The answer landing all at once feels dead | Throttle the single `stream_token` into timed slices in the driver's `assistant/message` handler (~5 lines). Label it in code and UI as an approximation, never as streaming | `chat_ui.py:891` `stream_token` |
| You want the model's reasoning visible | ⚠ Needs Task 1 finding (c) first — a separate reasoning event, or `<think>`-wrapped text the existing `_filter_display_token` already strips. Then wire `stream_think_token` | `chat_ui.py:874`, NOTES.md "Remaining unknowns" |
| You want tok/s in the answer footer | Call `set_metrics(sink)` with dsh's token accounting; the footer already renders it | `chat_ui.py:835`, `:1062` |
| Long sessions start silently truncating | Call `set_context_usage(pct, max_tokens)`; the ≥75%/≥90% warnings at `stream_end` already exist | `chat_ui.py:625`, `:1073-1082` |
| dsh reports a compaction or turn cap | Call `show_context_compaction()` / `show_turn_limit()` | `chat_ui.py:631`, `:652` |
| A long tool call gives no feedback | Call `tool_log` / `tool_progress` from `step/*` events | `chat_ui.py:776`, `:783` |
| ⚠ You need to abort a turn mid-flight | Unconfirmed whether the SDK supports cancellation at all — probe `session_prompt` before designing UI for it | NOTES.md "Remaining unknowns" |
| You want history to survive a restart | Probe whether reusing a `session_id` across `DeepSeekHarness` launches replays durable history, then pass it through the CLI | `api.py:120` `start_session` |
| You want a debugging view of every event | Construct `ChatUI(show_logs=True)` and call `add_log` from the driver's ignore-branch | `chat_ui.py:277`, `:319` |
| You want a second provider/model | Add template variables to `settings.yaml.template` — the schema is already shared across every dsh surface | `dsh_tui/settings.yaml.template` |

Nothing in that table is a second implementation to reconcile with the first. That is the payoff of porting `ChatUI` whole in Task 2 instead of pruning it to MVP scope.

## Test Boundaries

- **Task 3 owns the only real logic test:** `dsh_tui/tests/test_replay.py`, an offline replay of the already-captured probe events against a recording stub. It doubles as the regression fixture for every future growth row that touches the driver.
- Tasks 1, 2, 4 verify through their own stated acceptance criteria (probe output, standalone import check, live two-turn run).
- No shared test infrastructure: the captured probe outputs in `investigation/probe/` already serve as the fixtures, and no two tasks depend on a common fixture builder.
- No `test-engineer` pass — see rationale below.

## Risks

- **Task 1 finding (a) comes back negative** (a second `.run()` does not continue the conversation) — this invalidates acceptance criterion 2, which is the point of the whole MVP. Mitigation: Task 1 runs *first*, in parallel with Task 2, precisely so this surfaces before Task 4 is built on the assumption. Escalate rather than improvise a session strategy.
- **No real token-level streaming** — a genuine UX regression against onit. Mitigation: the MVP is honest about it (spinner during the turn, complete answer on arrival, limitation stated in the README) rather than shipping a fake by default. The approximation is available as growth row 1 the moment it is actually felt as a problem, which is the criterion `06-mvp-fast-path.md` §7 uses.
- **The `decode_rate` import is a first-message crash, not a cosmetic issue** — verified it is unconditional. Mitigation: it is an explicit, non-deferrable acceptance criterion of Task 2, not a "nice to fix while porting."
- **vLLM request-shape quirks** (`supportsDeveloperRole`, `maxTokensField`) — carried into the template preemptively and proven by two live probes, but never stress-tested against a request shape that would trigger the failure mode. Mitigation: re-check when growth row 2 turns on `reasoning_effort`, which is exactly the kind of change that would.
- **dsh is developer-preview and unaudited** (its own `SAFETY.md`), running a full tool roster including `bash` and file writes against a real workspace. Not a new exposure — the same posture onit already carries — but it is *why* tool-call visibility is in the MVP and not deferred: an agent editing files invisibly is worse than one with no tools at all.
- **License attribution** — Apache-2.0 §4b requires retaining the header and stating changes. Mitigation: an explicit Task 2 acceptance criterion naming both changes.

- **Test pass: skip** — rationale: `dsh-tui` is a new standalone local tool with no downstream consumers, no production surface, and no public interface. The single piece of non-trivial logic (event → UI mapping, including its `isError` failure path and empty-message edge case) is covered by a developer-owned offline replay test in Task 3, per `rules/agents.md`'s developer-owns-unit-tests boundary. The credential path is env-var-only and reused unchanged from an established local precedent rather than newly designed. **This flips to required** if the driver later grows the reasoning or cancellation branches (growth rows 2 and 7), which add real failure paths this MVP deliberately does not have.
- **Documentation pass: skip** — rationale: `investigation/NOTES.md` and this plan are the durable design record, and the two artifacts a future session actually needs — the growth table and the known-limitations list — are explicit deliverables of Task 4's `README.md`, owned by the task that has the context to write them. **This flips to required** if the README grows past usage + limitations + growth table into real feature/API documentation.
- **PR strategy: not applicable** — `/home/bertie/Documents/projects/dsh-tui/` is not a git repository.

## Review-Finding Handling

Surface R1/R2 findings and wait for human approval before each retry; never auto-fix.

**WAITING FOR CONFIRMATION**

---

### Where this diverged from the prior draft (`investigation/PLAN-DRAFT.md`)

1. **The draft buried a blocking unknown as a mid-task "must verify."** Whether a second `.run()` continues the conversation appeared there as a bullet inside Tasks 2 and 3 — but multi-turn context *is* the MVP's reason to exist, and it was unverified. Promoted to Task 1, running first and in parallel, bundled with two other open unknowns in the same probe run since they cost nothing extra.
2. **The draft put fake streaming in v1.** Moved the throttled reveal to a growth row — it's a workaround by the draft's own admission, and a single `stream_token(full_text)` call was confirmed to render correctly through `_emit`'s filters, so the honest path is also the simpler one.
3. **A correction to a fact the draft stated too softly:** the `decode_rate` import at `onit/src/ui/text.py:1060` was described there as "trivial to replace ... or drop the footer," implying it's optional. It is not — it executes on every non-empty stream block, so an unfixed port raises `ModuleNotFoundError` on the first assistant message rendered. Also found: three hardcoded onit-branded disclaimer strings (lines 1133, 1544, 1554) that aren't constructor-configurable, which the draft's "unchanged except that one import" attribution note would have described inaccurately.
