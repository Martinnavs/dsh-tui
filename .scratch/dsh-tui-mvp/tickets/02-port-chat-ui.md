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

### 2026-09-10 — developer
Ported `/home/bertie/Documents/projects/onit/src/ui/text.py` to `/home/bertie/Documents/projects/dsh-tui/dsh_tui/chat_ui.py` near-verbatim (1644 → 1676 lines; `diff` confirms the only deltas are the two mandated changes plus the header/notice). Also created `dsh_tui/__init__.py` (empty) as minimal scaffolding so the package imports; no other files touched.

Changes made:
1. Kept the Apache-2.0 header verbatim and added an adaptation notice directly below it (lines 15–23) naming `onit/src/ui/text.py`, the upstream repo `sibyl-oracles/onit`, and the two changes below.
2. `stream_end()`'s unconditional `from src.model.serving.chat import decode_rate` (old line 1060) is removed. Replaced with a new `_decode_rate()` method that inlines onit's own `decode_rate(m)` logic (`tokens = m.get("completion_tokens") or 0; seconds = m.get("decode_s") or 0.0; return tokens/seconds if both > 0 else 0.0`) reading directly from `self._metrics`. Returns `0.0` when `set_metrics()` was never called, so `format_meta` omits the tok/s segment — matches upstream behavior exactly (verified: with `set_metrics` + `completion_tokens=100, decode_s=5.0`, footer prints `20.0 tok/s`; without it, no rate is shown and no exception is raised).
3. Hoisted the three identical hardcoded `"OnIt may produce inaccurate information..."` strings (old lines 1133, 1544, 1554) into a new constructor-supplied `disclaimer: str` parameter, defaulting to `"dsh may produce inaccurate information. Verify important details independently."`. All three call sites now read `self.disclaimer`. Left `agent_cursor="OnIt"` / `banner_title="OnIt Chat Interface"` (constructor defaults) untouched per the ticket's explicit instruction.
4. Two incidental comments/docstrings mentioning `onit.py` (unrelated to the two mandated changes — one in `verification_end`, one in `stream_end`'s docstring) were genericized ("the caller" instead of "onit.py") because the ticket's Must-Verify grep requires `grep -in "onit" dsh_tui/chat_ui.py` to return only the attribution notice and the two constructor defaults; left as originally written they would have added two more unrelated hits. No logic changed, only the wording of two comments.

No methods pruned or renamed; no other rendering/behavior changes.

Verification run (via `uv run --with rich python3 -c ...` since no venv existed for the empty package yet — ephemeral, not persisted to the repo):
- `grep -n "from src\.\|import src\." dsh_tui/chat_ui.py` → empty. PASS
- `grep -in "onit" dsh_tui/chat_ui.py` → only lines 16/18/20/21 (attribution notice) and 87/94/112 (the two constructor defaults + their docstring line). PASS
- `python3 -m py_compile dsh_tui/chat_ui.py` → OK
- `ChatUI(agent_cursor='dsh', banner_title='dsh Chat').render()` → ran cleanly, printed banner + dsh disclaimer, no exception. PASS
- `stream_start()` → `stream_token('hello')` → `stream_end()` → printed a complete streamed block (header, token, footer with elapsed) with no exception, no `ModuleNotFoundError`. PASS
- Same sequence with `set_metrics()` wired to a sink with `completion_tokens=100, decode_s=5.0` → footer printed `20.0 tok/s`, confirming `_decode_rate()` matches upstream `decode_rate()` arithmetic.

Status: DONE. All four acceptance criteria met.
