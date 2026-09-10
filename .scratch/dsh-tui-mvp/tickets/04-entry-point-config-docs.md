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
