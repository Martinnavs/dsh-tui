"""Empirical check: does dsh's Python SDK expose token-level streaming deltas
over on_notification, or only whole-message events?

Prints every raw notification (method + a truncated payload preview) as it
arrives during one real turn against the onit/vLLM Qwen endpoint, plus a
running count of assistant/message occurrences (repeated growing-prefix
deltas vs. a single final message would both show up as this event type per
known-event-types.ts -- the printed count/preview distinguishes them).
"""
import json
import os
import sys
import time

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

DSH_HOME = os.path.join(os.path.dirname(__file__), "dsh-home")

counts: dict[str, int] = {}
assistant_message_previews: list[str] = []


def on_notification(n):
    counts[n.method] = counts.get(n.method, 0) + 1
    t = time.monotonic()
    event = n.payload.get("event") if n.method == "session.event" else None
    etype = event.get("type") if isinstance(event, dict) else None
    full = etype in ("tool/call", "tool/result")
    preview = json.dumps(n.payload)[: None if full else 160]
    print(f"[{t:.3f}] {n.method} {etype or ''} {preview}", flush=True)

    if n.method == "session.event":
        if isinstance(event, dict) and event.get("type") == "assistant/message":
            data = event.get("data", {})
            msg = data.get("message") if isinstance(data, dict) else None
            content = (msg or data).get("content") if isinstance(msg or data, dict) else None
            text = ""
            if isinstance(content, list):
                text = "".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            assistant_message_previews.append(text)
            print(f"    -> assistant/message #{len(assistant_message_previews)}, "
                  f"{len(text)} chars: {text[:80]!r}", flush=True)


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else "Count from 1 to 10, one number per line, nothing else."
    session_id = sys.argv[2] if len(sys.argv) > 2 else "probe-001"
    config = DeepSeekHarnessConfig(
        provider="onit",
        model="Qwen/Qwen3.8-27B",
        profile="sdk",
        dsh_home=DSH_HOME,
    )
    print(f"dsh_home={DSH_HOME}", file=sys.stderr)
    with DeepSeekHarness(config) as harness:
        result = harness.run(
            task,
            session_id=session_id,
            on_notification=on_notification,
        )
    print("\n=== notification method counts ===")
    for method, n in sorted(counts.items()):
        print(f"  {method}: {n}")
    print(f"\nassistant/message events: {len(assistant_message_previews)}")
    for i, text in enumerate(assistant_message_previews, 1):
        print(f"  #{i} ({len(text)} chars): {text!r}")
    print(f"\nfinal_response ({len(result.final_response)} chars): {result.final_response!r}")
    print(f"finish_reason: {result.finish_reason}")


if __name__ == "__main__":
    main()
