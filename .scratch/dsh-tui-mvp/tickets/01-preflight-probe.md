# 01: Pre-flight probe — close the one unknown the MVP's acceptance criteria rest on

**Repo:** /home/bertie/Documents/projects/dsh-tui/
**Blocked by:** None (can start immediately, in parallel with Task 2)
**Status:** ready-for-agent

## Files
- investigation/probe/multiturn_probe.py
- investigation/NOTES.md (append only, new dated "Probe 3" subsection)

## Action
Extend the existing probe harness (`investigation/probe/notification_probe.py` + `run_probe.sh`, same isolated `DSH_HOME`, same secret discipline) to run one process that:
1. holds a single `Session` from `harness.start_session(id)` and calls `.run()` **twice** — "My favourite colour is teal." then "What is my favourite colour?" — recording whether turn 2 sees turn 1's context;
2. issues one **long-output** prompt (several paragraphs) and reports the `assistant/message` event count and per-event char lengths;
3. issues one **reasoning-eliciting** prompt and dumps every distinct `event.type` seen plus the raw text of any `assistant/message`, so a `<think>`-wrapped single string is distinguishable from a separate reasoning event type.

Append the findings to `investigation/NOTES.md` under a new dated "Probe 3" subsection, in the same established-fact style as the existing "Established facts" / "Probes" sections.

See `/home/bertie/Documents/projects/dsh-tui/investigation/NOTES.md` for the full investigation record this probe builds on (Probes 1–2, event catalog, prior unknowns list) — read it rather than have this restated.

## Established (verified — don't re-derive)
The SDK exposes `DeepSeekHarness.run(input, session_id=...)` which is literally `self.start_session(session_id).run(input, ...)` (`api.py:124-131`), and `Session` (`api.py:135`) is a reusable object holding `self.id` — so a two-turn probe on one `Session` is a three-line change to the existing probe. The probe harness, `settings.yaml.template`, isolated `DSH_HOME`, and the venv all already work end to end against the live endpoint (two successful runs, `investigation/probe/probe-output-*.txt`).

## Must Verify
(a) does turn 2 on the same `Session` carry turn 1's context — **this gates acceptance criterion 2 and therefore the whole MVP, and gates Task 4**; (b) does a multi-paragraph answer arrive as one `assistant/message` or several (the "chunking might exist but wasn't triggered at 22–78 chars" unknown); (c) reasoning event shape, or `<think>`-wrapped text, or neither.

## Unknowns
All three above (a, b, c) — this task exists to close them. If (a) comes back negative, **stop and escalate before Task 4** — the MVP's turn loop would need a different session strategy and that is a scope decision, not a developer choice. Report the (a) verdict explicitly and clearly since Task 4 is blocked on it.

## Dependencies/license
None new — reuses `investigation/probe/.venv`.

## Symbols
`deepseek_harness.api.DeepSeekHarness.start_session`, `Session.run`, `RunResult.final_response`; `probe/notification_probe.py:on_notification`.

## Acceptance Criteria
- [ ] All three questions (a, b, c) answered with raw captured output saved alongside the existing probe outputs (`investigation/probe/probe-output-*.txt` convention)
- [ ] `investigation/NOTES.md` updated with a new dated "Probe 3" subsection stating the verdict on each of (a), (b), (c)
- [ ] If (a) is negative, escalation is explicitly flagged in the report rather than silently proceeding

## Non-Goals
- Wiring anything into `ChatUI`
- Changing `settings.yaml.template`'s provider/model

## Security-sensitive
yes — touches the live endpoint and the API key. Same discipline as the existing probes: the launcher shell script exports from `~/.secrets/onit/*`, Python reads only `os.environ`, the value is never read or printed by the agent.

## Shared Context
See `/home/bertie/Documents/projects/dsh-tui/investigation/NOTES.md` for the investigation record this probe extends, and `investigation/PLAN-DRAFT.md`/`PLAN.md` for how finding (a) gates Task 4.

## Execution Log
