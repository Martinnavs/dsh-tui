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

### Scrolling the chat history

While typing at the `➤` prompt, these keys move the visible window over
the chat history instead of being inserted into the input line:

- **PgUp** or **Ctrl+U** — scroll back (older messages)
- **PgDn** or **Ctrl+F** — scroll forward (newer messages)
- **Home** — jump to the oldest message
- **End** — jump to the newest message

There's also a vim-style scroll mode: a standalone **Esc** at the prompt
toggles it, then `j`/`k` (or the arrow keys) step one page,
`gg`/`G` jump to the oldest/newest message, and `Enter`/`i`/`Esc` return
to typing.

The visible window is **height-aware**, not a fixed number of messages:
`ChatUI` measures each message's real rendered height (via Rich's word
wrap) against however many rows are actually free in the terminal right
now, and shows however many trailing messages fit. `display_messages`
(default 15, passed to `ChatUI(...)`) is a *ceiling* on that count, not
the count itself — a handful of verbose responses can fill the whole
window well before `display_messages` is reached, and a terminal resize
changes how many messages fit on the next repaint with no extra
keypress needed.

The footer under the chat panel always shows the visible range and the
total message count (e.g. `Showing 12-26 of 140 messages`), reflecting
the actual (height-fitted) window size, not `display_messages`. Once
you've scrolled back from the bottom it also says so and names the key
that returns you to the latest messages (`— scrolled, End to jump to
latest`).

Note: because the window size varies with position (it's driven by how
tall the messages at that position happen to be, not a fixed count),
`PgUp`/`k` followed by `PgDn`/`j` is not guaranteed to land you back on
the exact same window you started from. `End` (or `G` in scroll mode)
is the reliable way back to the bottom — it always jumps to offset 0
rather than retracing steps.

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
- **Scrolling is message-granular, not line-granular.** The scrollback
  window (`ChatUI._visible_messages()`) now consults the real terminal
  size and each message's actual rendered height (`_content_budget()`,
  `_message_height()`), so it fits as many trailing messages as the
  terminal has room for — but it still fits or drops whole messages, not
  lines within one. A single message taller than the whole terminal
  still overflows the visible area; sub-message (line-granular)
  scrolling is not implemented.
- **No live real-terminal verification of the height-aware scroll
  window.** The behavior above is covered by an extensive unit and pty
  test suite (measured/pinned terminal dimensions, real key-dispatch
  loop), but no agent that built or reviewed it had an interactive tty
  to confirm against. If you hit anything that looks off with a real
  verbose session (`show_logs=True`, a few long turns, Esc, `k`/`j`),
  it's worth a spot-check before relying on it further.
- **Chat history is capped at `max_messages` (1000).** Raised in
  `dsh_tui/cli.py` from `ChatUI`'s original default of 100 specifically
  so the new scroll feature has meaningful depth to reach — the old
  default would have capped scrollback at roughly 25 tool-using turns
  (each turn appends 2+ messages with `show_logs=True`).

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
| A single message taller than the terminal still overflows | The scroll window is already height-aware (`_content_budget`, `_message_height`, `_fit_backward`/`_fit_forward`) — it fits as many whole messages as the terminal allows. What's left is sub-message (line-granular) scrolling within one oversized message, which is a separate, larger change: it needs a line offset *within* a message's rendered content, not just a count of whole messages | `chat_ui.py` `_visible_messages`, `_content_budget`, `_message_height` |
| ⚠ You want mouse-wheel scrolling | Deferred by explicit user request this session (keyboard scrolling was tested first in tmux; mouse support was left for a separate pass if needed). This user's tmux config has `mouse off` globally, so wheel events wouldn't reach the app without also changing that tmux setting — implementing it needs xterm SGR mouse-reporting protocol support added to the raw-byte input loop, a comparable-sized addition to the keyboard bindings that just shipped, not a trivial one | `chat_ui.py:1428` `_input_with_history_unix` |

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
