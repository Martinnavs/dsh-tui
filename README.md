# dsh-tui

Investigation + plan for porting `onit`'s terminal chat UI onto `dsh`
(`deepseek-ai/deepseek-harness`), which ships no interactive terminal
surface of its own. Nothing built yet — this is the scoping phase.

- [`investigation/NOTES.md`](investigation/NOTES.md) — full investigation:
  established facts (with how each was verified), the empirical
  streaming-granularity probes, symbol pointers, open unknowns, risks.
- [`investigation/probe/`](investigation/probe/) — the actual scripts used
  to check dsh's SDK notification stream against the live Qwen/vLLM
  endpoint, plus their captured output.
- [`PLAN.md`](PLAN.md) — the current implementation plan, produced by the
  real `tech-lead` agent (Plan mode), reframed around the Atomic MVP
  philosophy from `onit/docs/design-guide/06-mvp-fast-path.md` and
  `another-harness/HANDOFF.md`. **Not yet confirmed or started** — stops
  at WAITING FOR CONFIRMATION.
- [`investigation/PLAN-DRAFT.md`](investigation/PLAN-DRAFT.md) —
  superseded hand-drafted plan from the investigation-only pass, kept for
  the record; `PLAN.md` explains where and why it diverges.

Related: [`../onit/`](../onit/) (upstream terminal agent being ported
from), [`../another-harness/`](../another-harness/) (already wired to the
same Qwen/vLLM endpoint via `onit` directly, working today).
