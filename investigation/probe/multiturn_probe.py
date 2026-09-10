"""Probe 3: closes three remaining unknowns from investigation/NOTES.md.

Extends notification_probe.py's approach (same isolated DSH_HOME, same
secret discipline -- run via run_probe3.sh, never reads secrets directly)
with three real turns against the live onit/vLLM Qwen endpoint:

  (a) does a second `.run()` on one held `Session` see the first turn's
      context? (gates the whole MVP's turn loop -- see NOTES.md / PLAN.md)
  (b) does a multi-paragraph prompt arrive as one `assistant/message` event
      or several?
  (c) does a reasoning-eliciting prompt produce a distinct reasoning event
      type, `<think>`-wrapped text inside `assistant/message`, or neither?

Each phase uses its own session id and its own notification-collecting
callback so the three answers don't interleave.
"""
import json
import sys
import time
import uuid
from dataclasses import dataclass, field

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

import os

DSH_HOME = os.path.join(os.path.dirname(__file__), "dsh-home")
RUN_ID = uuid.uuid4().hex[:8]  # dsh_home persists sessions across runs; avoid id collisions


@dataclass
class Collector:
    label: str
    method_counts: dict = field(default_factory=dict)
    event_types: list = field(default_factory=list)
    assistant_messages: list = field(default_factory=list)  # (chars, text)
    reasoning_blocks: list = field(default_factory=list)  # (chars, text)
    content_block_types: set = field(default_factory=set)

    def on_notification(self, n):
        self.method_counts[n.method] = self.method_counts.get(n.method, 0) + 1
        t = time.monotonic()
        event = n.payload.get("event") if n.method == "session.event" else None
        etype = event.get("type") if isinstance(event, dict) else None
        if etype is not None:
            self.event_types.append(etype)
        full = etype in ("tool/call", "tool/result", "assistant/message")
        preview = json.dumps(n.payload)[: None if full else 160]
        print(f"[{self.label}][{t:.3f}] {n.method} {etype or ''} {preview}", flush=True)

        if etype == "assistant/message":
            data = event.get("data", {})
            msg = data.get("message") if isinstance(data, dict) else None
            content = (msg or data).get("content") if isinstance(msg or data, dict) else None
            text = ""
            reasoning_text = ""
            if isinstance(content, list):
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    self.content_block_types.add(b.get("type"))
                    if b.get("type") == "text":
                        text += b.get("text", "")
                    elif b.get("type") == "reasoning":
                        reasoning_text += b.get("text", "")
            self.assistant_messages.append((len(text), text))
            if reasoning_text:
                self.reasoning_blocks.append((len(reasoning_text), reasoning_text))
            print(f"    -> assistant/message #{len(self.assistant_messages)}, "
                  f"{len(text)} chars text, {len(reasoning_text)} chars reasoning: "
                  f"{text!r}", flush=True)
            if reasoning_text:
                print(f"       reasoning block: {reasoning_text!r}", flush=True)


def make_config(**overrides):
    kwargs = dict(
        provider="onit",
        model="Qwen/Qwen3.8-27B",
        profile="sdk",
        dsh_home=DSH_HOME,
    )
    kwargs.update(overrides)
    return DeepSeekHarnessConfig(**kwargs)


def phase_a(harness):
    print("\n===== Phase A: same-Session two-turn context carry =====", flush=True)
    session = harness.start_session(f"probe3-multiturn-{RUN_ID}")
    c1 = Collector("A.turn1")
    r1 = session.run("My favourite colour is teal.", on_notification=c1.on_notification)
    c2 = Collector("A.turn2")
    r2 = session.run("What is my favourite colour?", on_notification=c2.on_notification)
    print(f"\nA turn1 final_response: {r1.final_response!r}")
    print(f"A turn2 final_response: {r2.final_response!r}")
    carried = "teal" in r2.final_response.lower()
    print(f"A VERDICT: turn2 carries turn1 context = {carried}")
    return r1, r2, carried


def phase_b(harness):
    print("\n===== Phase B: multi-paragraph prompt =====", flush=True)
    prompt = (
        "Write exactly four short paragraphs, each about a different season "
        "(spring, summer, autumn, winter), each paragraph 3-4 sentences long, "
        "separated by a blank line. Do not use any headings or numbering."
    )
    c = Collector("B")
    result = harness.run(prompt, session_id=f"probe3-longoutput-{RUN_ID}", on_notification=c.on_notification)
    print(f"\nB final_response ({len(result.final_response)} chars): {result.final_response!r}")
    print(f"B assistant/message event count: {len(c.assistant_messages)}")
    for i, (n, _) in enumerate(c.assistant_messages, 1):
        print(f"  #{i}: {n} chars")
    return result, c


def phase_c(harness):
    print("\n===== Phase C: reasoning-eliciting prompt =====", flush=True)
    prompt = (
        "A farmer has 17 sheep, and all but 9 die. Then he buys twice as many "
        "new sheep as he has left. Think step by step, show your reasoning "
        "explicitly, and then give the final count of sheep."
    )
    c = Collector("C")
    result = harness.run(prompt, session_id=f"probe3-reasoning-{RUN_ID}", on_notification=c.on_notification)
    print(f"\nC final_response ({len(result.final_response)} chars): {result.final_response!r}")
    print(f"C distinct event.type values seen: {sorted(set(c.event_types))}")
    reasoning_types = [t for t in set(c.event_types) if "reason" in t.lower() or "think" in t.lower()]
    print(f"C event types containing 'reason'/'think': {reasoning_types}")
    has_think_tag = "<think>" in result.final_response or "<think>" in "".join(
        t for _, t in c.assistant_messages
    )
    print(f"C '<think>' substring present in assistant text: {has_think_tag}")
    return result, c


def main():
    # No reasoning_effort override: phases A/B already show every
    # assistant/message carries a {"type": "reasoning", "text": ...} content
    # block alongside {"type": "text", ...} unconditionally (model reasons
    # by default on this backend); setting reasoning_effort="high" errored
    # ("model does not support reasoning effort") so the config default is
    # used throughout, including phase C.
    config = make_config()
    print(f"dsh_home={DSH_HOME}", file=sys.stderr)
    with DeepSeekHarness(config) as harness:
        r1, r2, carried = phase_a(harness)
        result_b, c_b = phase_b(harness)
        result_c, c_c = phase_c(harness)

    print("\n\n========== SUMMARY ==========")
    print(f"(a) same-Session second .run() carries first turn's context: {carried}")
    print(f"(b) multi-paragraph prompt -> assistant/message event count: {len(c_b.assistant_messages)}")
    print(f"    per-event char lengths: {[n for n, _ in c_b.assistant_messages]}")
    print(f"(c) distinct event.type values in reasoning-eliciting turn: {sorted(set(c_c.event_types))}")
    print(f"    '<think>' substring present: "
          f"{'<think>' in result_c.final_response}")


if __name__ == "__main__":
    main()
