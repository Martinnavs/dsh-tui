# 02: Port `ChatUI` whole, de-onit it

**Repo:** /home/bertie/Documents/projects/dsh-tui/
**Blocked by:** None (can start immediately, in parallel with Task 1)
**Status:** ready-for-agent

## Files
- dsh_tui/chat_ui.py

## Action
Copy `/home/bertie/Documents/projects/onit/src/ui/text.py` to `dsh_tui/chat_ui.py` near-verbatim. Make exactly two behavioral changes, both mandatory, both recorded:

1. **`decode_rate` (critical path, not optional).** `stream_end()` does `from src.model.serving.chat import decode_rate` at **line 1060**, and this import is **unconditional on every non-empty stream block** — it sits after the empty-block early-return at line 1050 but before any metrics guard. Left in place it raises `ModuleNotFoundError` on the *first assistant message the MVP ever renders*. Replace with an inline tok/s computation from the same `self._metrics` dict, returning empty/`None` when `set_metrics()` was never called (the MVP case) so `format_meta` simply omits the rate.
2. **Hardcoded onit branding.** Three identical hardcoded strings — `"OnIt may produce inaccurate information. Verify important details independently."` at **lines 1133, 1544, 1554** — are not constructor-configurable. Hoist to a single class-level/constructor-supplied `disclaimer` attribute defaulting to a dsh-appropriate wording. (`agent_cursor="OnIt"` and `banner_title="OnIt Chat Interface"` at lines 78/85 are already constructor args — pass different values, do **not** edit them.)

Keep the Apache-2.0 header verbatim (copyright "2025 Rowel Atienza", `text.py:1-15`) and add a short adaptation notice below it naming the source file, the upstream repo, and exactly these two changes.

## Established (verified — don't re-derive)
The full `ChatUI` contract onit's own turn loop uses is present on the class — enumerated by cross-referencing every `chat_ui.<method>` call site in onit's `chat.py` (NOTES.md, "Established facts"). The only cross-package import in the whole 1644-line file is line 1060 (`grep` of every `from`/`import`: everything else is stdlib or `rich`). `start_tool_batch`/`end_tool_batch`/`set_turn_context` belong to onit's separate `StreamingAdapter` and are `hasattr()`-guarded at every call site — not part of the terminal contract. onit is Apache-2.0 with no `NOTICE` file.

## Must Verify
After the edit, `grep -n "from src\.\|import src\." dsh_tui/chat_ui.py` returns nothing; and `grep -in "onit" dsh_tui/chat_ui.py` returns only the attribution notice and the two constructor defaults.

## Unknowns
None blocking.

## Dependencies/license
Adds `rich` (permissive MIT, already an onit dependency, no transitive or license conflict). Apache-2.0 §4b obligation: retain header, state changes — satisfied by the adaptation notice.

## Symbols
`ChatUI.__init__` (`text.py:75`), `stream_end` (`:1033`), `stream_token` (`:891`), `_emit` (`:899`), `get_user_input` (`:1537`), `start_thinking`/`stop_thinking` (`:607`/`:611`), `add_tool_call` (`:254`), `add_tool_result` (`:265`), `show_tool_start` (`:752`), `show_tool_done` (`:787`), `start_tool_spinner`/`stop_tool_spinner` (`:770`/`:773`).

## Acceptance Criteria
- [ ] `python -c "from dsh_tui.chat_ui import ChatUI; ChatUI(agent_cursor='dsh', banner_title='dsh Chat').render()"` runs with no `onit`/`src.*` package anywhere on the path
- [ ] A scripted `stream_start()` → `stream_token("hello")` → `stream_end()` sequence prints a complete answer block **without** raising on the removed import
- [ ] `grep -n "from src\.\|import src\." dsh_tui/chat_ui.py` returns nothing
- [ ] `grep -in "onit" dsh_tui/chat_ui.py` returns only the attribution notice and the two constructor defaults

## Non-Goals
- Any rendering/behavior change beyond the two fixes above
- Pruning unused methods (the unused methods *are* the growth path — deleting them is what would turn a growth row into a rewrite)

## Security-sensitive
no — pure rendering, touches no credentials.

## Shared Context
`dsh_tui/chat_ui.py` is the shared-infrastructure file Task 3 depends on (its driver calls this class's methods). See `/home/bertie/Documents/projects/dsh-tui/investigation/NOTES.md` "Established facts" section for the full cross-referenced `ChatUI` method contract used by onit's own turn loop.

## Execution Log
