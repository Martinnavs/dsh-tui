# dsh-tui

An interactive terminal chat client for `dsh` (`deepseek-ai/deepseek-harness`),
which ships no interactive terminal surface of its own. `ChatUI`
(`dsh_tui/chat_ui.py`) is ported near-verbatim from `onit`'s
`src/ui/text.py`, driven by a small event mapper (`dsh_tui/driver.py`) over
dsh's own `session.event` notification stream.

See [`PLAN.md`](PLAN.md) for the confirmed implementation plan and
[`investigation/NOTES.md`](investigation/NOTES.md) for the full
investigation record (established facts, probes, symbol pointers).

## Usage

```sh
scripts/run-dsh-tui.sh
```

This opens an interactive terminal chat against dsh's `sdk` profile,
pointed at the onit/Qwen vLLM endpoint. Requirements:

- `~/.secrets/onit/host_address.txt` and `~/.secrets/onit/api_key.txt` —
  the same files `../another-harness/scripts/run-onit.sh` and
  `investigation/probe/run_probe.sh` read. The script sources them into
  its own process's environment only; the API key is never logged,
  printed, or written into a config file in the clear.
- A Python 3 interpreter on `PATH` able to run `python3 -m venv` — the
  venv itself does **not** need to exist ahead of time. `investigation/probe/.venv`
  (the same venv Task 1/3's probes used) doubles as dsh-tui's runtime venv;
  if it isn't there yet, `scripts/run-dsh-tui.sh` creates it and installs
  everything in [`requirements.txt`](requirements.txt) (`deepseek-harness-sdk`,
  `rich`) automatically on first run. If it already exists (e.g. you ran a
  probe already), the script reuses it as-is and only installs `rich` if
  missing.

Override the provider or model without editing any file:

```sh
DSH_TUI_PROVIDER=onit DSH_TUI_MODEL=Qwen/Qwen3.8-27B scripts/run-dsh-tui.sh
```

Type at the `➤` prompt and press Enter to send a turn. Type `\bye`, or
press Ctrl+C / Ctrl+D, to exit.

## Known limitations

- **No live token streaming.** The answer lands complete, not
  token-by-token — `assistant/message` arrives as one whole-string event
  on this backend (confirmed up to ~1.2k chars, investigation/NOTES.md
  "Probe 3" finding (b)), so `ChatUI.stream_token()` is called once per
  turn with the full text. A spinner runs while the turn is in flight so
  the loop never looks hung, but it is not a typewriter effect.
- **Ctrl+C mid-turn exits the client and may leave the run orphaned
  server-side.** `ChatUI.get_user_input()` raises on Ctrl+C/EOF and the
  main loop exits cleanly, but whether dsh's SDK supports cancelling an
  in-flight `session_prompt` is unconfirmed (investigation/NOTES.md
  "Remaining unknowns"). Ctrl+C at the `➤` prompt (between turns) is
  always clean; Ctrl+C during a turn is not guaranteed to stop the
  work happening on the server.
- **Single session, no resume across restarts.** `harness.start_session(id)`
  is called once per process and a fresh session id is generated per run;
  closing and reopening the client starts a new conversation with no
  history from the previous run.

## Growth table — capability-driven, not calendar-driven

Copied verbatim from `PLAN.md` ("Growth table — capability-driven, not
calendar-driven") so a future session enables a row instead of
re-deriving it. Every row is one more method call on a class already in
`dsh_tui/chat_ui.py`, except the two marked ⚠, which need an external
answer first and are called out honestly rather than implied to be free.

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

Nothing in that table is a second implementation to reconcile with the
first. That is the payoff of porting `ChatUI` whole in Task 2 instead of
pruning it to MVP scope.

Note: `dsh_tui`'s `ChatUI` is constructed with `show_logs=True`
(`dsh_tui/cli.py`) — `add_tool_call`/`show_tool_start`/`add_tool_result`/
`show_tool_done` are all no-ops when `show_logs` is `False`, and rendering
tool calls inline (never invisibly) is acceptance criterion 3.

## Attribution

`dsh_tui/chat_ui.py` is ported from `onit`'s `src/ui/text.py`
(Apache-2.0, copyright 2025 Rowel Atienza). See the adaptation notice at
the top of that file for the exact changes made, per Apache-2.0 §4(b).
