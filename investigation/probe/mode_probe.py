"""Probe 4: pre-flight probe — mode switch + approval-policy wire surface.

Ticket: .scratch/dsh-tui-mode-toggle/tickets/01-preflight-probe.md

Closes the stdio-surface unknowns left open by the binary-strings analysis
(see investigation/NOTES.md and the ticket's "Established" section):

  (a) Is there a client-side JSON-RPC method to run /plan on|off for a live
      session? Candidate method names come from the documented binary-strings
      technique; each candidate is tried with timeout_seconds=10 and a
      permissive (no-validation) response, recording data vs unknown-method.
      Structural binary evidence (the dsh stdio server's request dispatcher
      is a switch over exactly initialize / session/prompt / shutdown) says
      NO dedicated method exists; this phase confirms it empirically and, if
      any candidate works, records exact method, params, response shape,
      persistence across subsequent session/prompt turns, and whether the
      state is session-scoped (checked against a fresh session).
  (b) The sdk profile's effective approval behavior + settable knob.
      b1: a tool-triggering turn with a bounded non-blocking peek loop on
          client._requests (private access, fine in probe code; next_request()
          blocks and would hang the probe if nothing arrives), plus the
          runtime-context snapshot's approval-policy sentence as baseline.
      b2: candidate approval-policy setter methods (same technique as (a)),
          plus a gated (sandbox-denied) operation under policy `ask` to test
          whether an approval request reaches the stdio client at all.
  (c) With plan mode ON (via (a)), a trivial file-editing task: how plan text
      arrives (assistant vs system message) and what exit_plan_mode does on a
      stdio client with no interactive review — completes, fails cleanly, or
      hangs. The watcher deliberately does NOT answer any client request here
      (no interactive review). A hang is escalation-worthy per the ticket.
  (d) After each switch: the raw RPC response plus any client-observable
      session.event (plan/mode projection, approval/policy, sandbox/mode,
      permission/preset, command/run|done, system/message) via a bounded
      post-request notification drain.
  (e) Slash commands typed as plain message text: interpreted by the runtime
      or only via the (a) method.

Standalone; same shape as notification_probe.py / multiturn_probe.py:
isolated probe DSH_HOME (never the repo's live .dsh-home), uuid4-suffixed
session ids, launcher-rendered settings (run_probe4.sh sources
~/.secrets/onit/* into its own process only; this file reads os.environ at
most and never touches secret values).

Wall-clock bounds: every candidate RPC uses timeout_seconds=10; every turn
has a per-turn cap; the whole probe has a top-level deadline. On deadline the
probe prints what it has and names the incomplete phases (an incomplete-but-
honest finding beats a hang).

Note on the ticket's "permissive empty response model": client.request()
validates into a pydantic model and would discard the raw shape this probe
needs as evidence, so the probe calls the same transport entry point one
level down (client._request_raw, private access — same class of access the
ticket sanctions for client._requests) to capture the raw JSON value.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig
from deepseek_harness.errors import JsonRpcError  # noqa: F401  (named in ticket)

PROBE_DIR = Path(__file__).resolve().parent
DSH_HOME = str(PROBE_DIR / "dsh-home")
REPO_ROOT = str(PROBE_DIR.parent.parent)
RUN_ID = uuid.uuid4().hex[:8]  # dsh_home persists sessions across runs

START = time.monotonic()
DEADLINE_SECONDS = 630.0  # top-level wall-clock bound for the whole probe
CANDIDATE_TIMEOUT = 10.0  # per-candidate RPC bound (ticket)

CHECK_PROMPT = (
    "Is plan mode currently active in this session? Answer with exactly one "
    "word: ON if active, OFF if not."
)

# Event types logged at full payload (raw evidence for the ticket).
FULL_TYPES = {
    "tool/call",
    "tool/result",
    "assistant/message",
    "system/message",
    "plan/mode",
    "approval/policy",
    "sandbox/mode",
    "permission/preset",
    "command/run",
    "command/done",
    "turn/end",
    "user/message",  # full payloads: runtime-context snapshots carry the (b1) policy sentence
}


def remaining() -> float:
    return max(0.0, DEADLINE_SECONDS - (time.monotonic() - START))


def make_config() -> DeepSeekHarnessConfig:
    return DeepSeekHarnessConfig(
        provider="onit",
        model="Qwen/Qwen3.8-27B",
        profile="sdk",
        dsh_home=DSH_HOME,
        cwd=REPO_ROOT,  # runtime workspace root == repo root (sandbox root)
    )


def is_unknown_method(outcome: str, code, message) -> bool:
    """True when an RPC candidate came back as 'method not found'. The ticket
    expected JSON-RPC -32601; the live SDK runtime instead answers unknown
    methods with code -32603 and the message
    'unknown DeepSeek Harness SDK runtime method: <name>' — classify by
    message (either code) and keep the exact code in the log."""
    if outcome != "jsonrpc-error":
        return False
    if code in (-32601, -32603):
        return True
    msg = str(message or "").lower()
    return "unknown" in msg and "method" in msg


def policy_verdict(sentences: list[str]) -> str | None:
    """Map a runtime-context approval-policy sentence to the effective knob
    value. Sentence shapes from the binary's ApprovalService prompt text:
      ask:   "Approval policy: ask. Operations that require approval may ask ..."
      never: "Approval prompts are disabled in this session: actions that
              require approval are rejected automatically ..."
    """
    blob = "\n".join(sentences)
    if "Approval policy: ask" in blob:
        return "ask"
    if "Approval prompts are disabled" in blob:
        return "never"
    return None


class Collector:
    """Logs every raw notification for one phase and extracts the evidence
    the ticket's verdicts need."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.t0 = time.monotonic()
        self.method_counts: dict[str, int] = {}
        self.event_types: list[str] = []
        self.assistant_texts: list[str] = []
        self.system_texts: list[str] = []
        self.user_texts: list[str] = []
        self.tool_calls: list[dict] = []
        self.tool_results: list[dict] = []
        self.plan_events: list[dict] = []
        self.permission_events: list[dict] = []
        self.command_events: list[dict] = []
        self.approval_policy_sentences: list[str] = []

    def on_notification(self, n) -> None:
        t = time.monotonic() - self.t0
        self.method_counts[n.method] = self.method_counts.get(n.method, 0) + 1
        event = n.payload.get("event") if n.method == "session.event" else None
        etype = event.get("type") if isinstance(event, dict) else None
        if etype is not None:
            self.event_types.append(etype)
        full = etype in FULL_TYPES or self._is_snapshot(n)
        preview = json.dumps(n.payload, default=str) if full else json.dumps(n.payload, default=str)[:160]
        print(f"[{self.label}][{t:7.3f}] {n.method} {etype or ''} {preview}", flush=True)
        if etype is None:
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if etype == "assistant/message":
            self.assistant_texts.append(self._text_of(data.get("message")))
        elif etype == "system/message":
            self.system_texts.append(self._text_of(data.get("message")))
        elif etype == "user/message":
            text = self._text_of(data)
            self.user_texts.append(text)
            for line in text.splitlines():
                if "Approval policy" in line or "Approval prompts" in line:
                    self.approval_policy_sentences.append(line.strip())
        elif etype == "tool/call":
            self.tool_calls.append(
                {"name": data.get("name"), "callId": data.get("callId"), "arguments": data.get("arguments")}
            )
        elif etype == "tool/result":
            self.tool_results.append(self._tool_result_brief(data))
        elif etype == "plan/mode":
            self.plan_events.append(data)
        elif etype in ("approval/policy", "sandbox/mode", "permission/preset"):
            self.permission_events.append({"type": etype, "data": data})
        elif etype in ("command/run", "command/done"):
            self.command_events.append({"type": etype, "data": data})

    @staticmethod
    def _text_of(owner) -> str:
        if not isinstance(owner, dict):
            return ""
        content = owner.get("content")
        if not isinstance(content, list):
            return ""
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )

    @staticmethod
    def _tool_result_brief(data: dict) -> dict:
        msg = data.get("message") if isinstance(data.get("message"), dict) else {}
        text = ""
        call_id = None
        source = msg.get("source")
        if isinstance(source, dict):
            call_id = source.get("callId")
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool-result":
                    call_id = call_id or block.get("toolCallId")
                    inner = block.get("content")
                    if isinstance(inner, list):
                        text += "".join(
                            b.get("text", "") for b in inner if isinstance(b, dict) and b.get("type") == "text"
                        )
        return {"isError": msg.get("isError"), "callId": call_id, "text": text[:800]}

    @staticmethod
    def _is_snapshot(n) -> bool:
        # Runtime-context snapshots arrive as user/message events; log them in
        # full because the approval-policy sentence is (b1)'s baseline evidence.
        if n.method != "session.event":
            return False
        event = n.payload.get("event")
        if not (isinstance(event, dict) and event.get("type") == "user/message"):
            return False
        return "Current runtime context" in json.dumps(n.payload, default=str)

    def policy_sentence_verdict(self) -> str | None:
        return policy_verdict(self.approval_policy_sentences)


class RequestWatcher(threading.Thread):
    """Bounded non-blocking peek loop on client._requests.

    Ticket: private access is fine in probe code; next_request() blocks and
    would hang the probe if nothing arrives.
    """

    def __init__(self, client, label: str, respond: str) -> None:
        super().__init__(daemon=True, name=f"reqwatch-{label}")
        self.client = client
        self.label = label
        self.respond_policy = respond  # "allow" | "none"
        self.stop_evt = threading.Event()
        self.received: list = []
        self.t0 = time.monotonic()

    def run(self) -> None:
        while not self.stop_evt.is_set():
            try:
                item = self.client._requests.get_nowait()
            except queue.Empty:
                time.sleep(0.05)
                continue
            if isinstance(item, BaseException):
                print(f"[{self.label}.watcher][{time.monotonic() - self.t0:7.3f}] _requests item is exception: {item!r}", flush=True)
                return
            self.received.append(item)
            print(
                f"[{self.label}.watcher][{time.monotonic() - self.t0:7.3f}] "
                f"RUNTIME->CLIENT REQUEST id={item.id} method={item.method} "
                f"params={json.dumps(item.payload, default=str)[:1200]}",
                flush=True,
            )
            if self.respond_policy == "allow":
                result = {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
                try:
                    self.client.respond(item.id, result)
                    print(f"[{self.label}.watcher] responded id={item.id} method={item.method} result={json.dumps(result)}", flush=True)
                except Exception as exc:
                    print(f"[{self.label}.watcher] respond failed: {exc!r}", flush=True)
            else:
                print(f"[{self.label}.watcher] respond_policy=none — leaving {item.method} pending (no interactive review)", flush=True)

    def stop(self) -> None:
        self.stop_evt.set()
        # Final bounded sweep for anything queued while the last turn settled.
        try:
            while True:
                item = self.client._requests.get_nowait()
                if isinstance(item, BaseException):
                    return
                self.received.append(item)
                print(f"[{self.label}.watcher] late request at stop: id={item.id} method={item.method}", flush=True)
        except queue.Empty:
            return


def drain_subscription(sub, collector, seconds: float, label: str) -> None:
    """Bounded post-RPC notification drain (ticket (d))."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        try:
            sub.drain(collector.on_notification)
        except BaseException as exc:
            print(f"[{label}] drain raised: {exc!r}", flush=True)
            return
        time.sleep(0.1)


def run_turn(client, sid: str, text: str, collector: Collector, turn_cap: float, *, gsub=None) -> dict:
    """Drive one session/prompt turn bounded by turn_cap and the global
    deadline. session_prompt returns the queued message id immediately (SDK
    README); the turn completes at the session.status idle notification.

    Pass gsub (a global subscription) to funnel the turn's notifications
    through the phase-wide drain; otherwise a private per-turn session
    subscription is used and closed here."""
    cap = min(turn_cap, remaining() - 5.0)
    state = {"idle": False}
    out = {"ok": False, "idle": False, "timeout": False, "error": None}
    if cap < 10.0:
        out["error"] = "global deadline reached before turn start"
        return out

    def on_n(n) -> None:
        collector.on_notification(n)
        if (
            n.method == "session.status"
            and n.payload.get("sessionId") == sid
            and n.payload.get("status") == "idle"
        ):
            state["idle"] = True

    own_sub = None
    if gsub is None:
        gsub = client.subscribe_session_notifications(sid)
        own_sub = gsub
    t0 = time.monotonic()
    # session_prompt() takes no timeout kwarg (the SDK makes prompt RPCs
    # unbounded by design; it returns the queued message id immediately per
    # the SDK README). Keep the bound anyway with a watchdog thread on the
    # RPC; the drain loop below bounds the turn observation regardless.
    prompt_result: dict = {"msg_id": None, "error": None}

    def _prompt_worker() -> None:
        try:
            prompt_result["msg_id"] = client.session_prompt(
                sid,
                [{"type": "text", "text": text}],
                notification_subscription=gsub,
            )
        except BaseException as exc:  # noqa: BLE001 — record any failure
            prompt_result["error"] = exc

    pt = threading.Thread(target=_prompt_worker, daemon=True, name=f"prompt-{sid}")
    pt.start()
    pt.join(min(15.0, cap))
    if prompt_result["error"] is not None:
        out["error"] = repr(prompt_result["error"])
        if own_sub is not None:
            own_sub.close()
        return out
    if pt.is_alive():
        out["error"] = "session/prompt RPC did not respond within 15s (drain continues; turn may still complete)"
    try:
        while time.monotonic() - t0 < cap and not state["idle"]:
            try:
                gsub.drain(on_n)
            except BaseException as exc:
                out["error"] = f"notification drain raised: {exc!r}"
                break
            time.sleep(0.15)
    finally:
        if own_sub is not None:
            own_sub.close()
    out["idle"] = state["idle"]
    out["ok"] = state["idle"]
    out["timeout"] = (not state["idle"]) and out["error"] is None and (time.monotonic() - t0 >= cap)
    return out


def try_rpc(client, label: str, method: str, params: dict, rpc_log: list) -> dict:
    """One bounded candidate RPC; records the raw result or the JSON-RPC
    error (code/message/data) for the report."""
    print(f"[rpc] {label}: {method} params={json.dumps(params, default=str)}", flush=True)
    t0 = time.monotonic()
    try:
        res = client._request_raw(method, params, timeout_seconds=CANDIDATE_TIMEOUT)
        rec = {
            "label": label, "method": method, "params": params,
            "outcome": "ok", "ms": round((time.monotonic() - t0) * 1000), "raw": res,
        }
        print(f"[rpc] {label} -> OK in {time.monotonic() - t0:.2f}s raw={json.dumps(res, default=str)[:1200]}", flush=True)
    except JsonRpcError as exc:
        rec = {
            "label": label, "method": method, "params": params,
            "outcome": "jsonrpc-error", "ms": round((time.monotonic() - t0) * 1000),
            "code": exc.code, "message": exc.message, "data": exc.data,
        }
        print(
            f"[rpc] {label} -> JsonRpcError in {time.monotonic() - t0:.2f}s "
            f"code={exc.code} message={exc.message!r} "
            f"data={json.dumps(exc.data, default=str)[:500] if exc.data is not None else None}",
            flush=True,
        )
    except TimeoutError as exc:
        rec = {
            "label": label, "method": method, "params": params,
            "outcome": "timeout", "ms": round((time.monotonic() - t0) * 1000),
            "message": str(exc)[:500],
        }
        print(f"[rpc] {label} -> TimeoutError in {time.monotonic() - t0:.2f}s: {str(exc)[:300]}", flush=True)
    except Exception as exc:
        rec = {
            "label": label, "method": method, "params": params,
            "outcome": "exception", "ms": round((time.monotonic() - t0) * 1000),
            "message": repr(exc)[:500],
        }
        print(f"[rpc] {label} -> {type(exc).__name__}: {exc!r:.400}", flush=True)
    rpc_log.append(rec)
    return rec


class Probe:
    """Holds cross-phase state (the discovered command-params shape, etc.)."""

    def __init__(self, client, gsub) -> None:
        self.client = client
        self.gsub = gsub
        self.rpc_log: list[dict] = []
        # Default wire shape for commands/execute (from the binary's typert
        # descriptor: parameters agentId/line/submittedAttachments); phases
        # may replace it with an empirically working variant.
        self.cmd_shape = "agentId"  # "agentId" | "args-agentId" | "sessionId"
        self.cmd_list_ok = None

    def cmd_params(self, sid: str, line: str) -> dict:
        if self.cmd_shape == "args-agentId":
            return {"args": {"agentId": sid, "line": line}}
        if self.cmd_shape == "sessionId":
            return {"sessionId": sid, "line": line}
        return {"agentId": sid, "line": line}

    def discover_cmd_shape(self, sid: str) -> bool:
        """Try commands/list to pin the wire param shape (and capture the real
        slash-command roster as evidence). Returns True on success."""
        candidates = [
            ("agentId", {"agentId": sid}),
            ("args-agentId", {"args": {"agentId": sid}}),
            ("sessionId", {"sessionId": sid}),
        ]
        for shape, params in candidates:
            rec = try_rpc(self.client, "commands/list shape probe", "commands/list", params, self.rpc_log)
            if rec["outcome"] == "ok":
                self.cmd_shape = shape
                self.cmd_list_ok = rec
                return True
        return False

    def run_cmd(self, sid: str, line: str, collector: Collector, label: str) -> dict:
        params = self.cmd_params(sid, line)
        rec = try_rpc(self.client, label, "commands/execute", params, self.rpc_log)
        # (d): bounded post-request drain for client-observable events.
        drain_subscription(self.gsub, collector, 2.0, f"{label}.drain")
        return rec

    def check_turn(self, sid: str, collector: Collector, cap: float = 50.0) -> dict:
        out = run_turn(self.client, sid, CHECK_PROMPT, collector, cap, gsub=self.gsub)
        final = collector.assistant_texts[-1].strip() if collector.assistant_texts else ""
        out["final_answer"] = final
        print(f"[check] {sid} -> {final!r} {out}", flush=True)
        return out

    def run_cmd_text(self, sid: str, line: str, collector: Collector, label: str,
                     cap: float = 60.0) -> dict:
        """Send a slash command as plain message text (the only client path
        left when (a) is negative). Interpreted commands produce
        command/run + command/done events without a model turn; uninterpreted
        text produces an ordinary assistant reply."""
        cmd_before = len(collector.command_events)
        perm_before = len(collector.permission_events)
        plan_before = len(collector.plan_events)
        assist_before = len(collector.assistant_texts)
        out = run_turn(self.client, sid, line, collector, cap, gsub=self.gsub)
        new_cmd = collector.command_events[cmd_before:]
        new_perm = collector.permission_events[perm_before:]
        new_plan = collector.plan_events[plan_before:]
        new_assist = collector.assistant_texts[assist_before:]
        interpreted = bool(new_cmd) or bool(new_perm) or bool(new_plan)
        done = [ce for ce in new_cmd if ce["type"] == "command/done"]
        rec = {
            "label": label,
            "method": "session/prompt (slash text)",
            "params": {"sessionId": sid, "text": line},
            "outcome": "ok" if out["idle"] else "incomplete",
            "interpreted": interpreted,
            "command_events": new_cmd,
            "permission_events": new_perm,
            "plan_events": new_plan,
            "assistant_reply": new_assist[-1][:400] if new_assist else None,
            "result": done[-1]["data"] if done else None,
        }
        self.rpc_log.append(rec)
        print(
            f"[cmd-text] {label}: interpreted={interpreted} turn={out} "
            f"result={json.dumps(done[-1]['data'], default=str)[:500] if done else None} "
            f"assistant={rec['assistant_reply']!r}",
            flush=True,
        )
        return rec


def phase_a(probe: Probe) -> tuple[dict, Collector, str]:
    """(a) client-side plan switch + (d) switch evidence + persistence/scope.

    Binary evidence (dsh-sdk-jsonrpc-server, this exact runtime): the stdio
    JSON-RPC server's request dispatcher is a switch over exactly three
    methods — initialize, session/prompt, shutdown — everything else throws
    'unknown DeepSeek Harness SDK runtime method: <name>' (observed wire
    code -32603). The commands/execute typert endpoint and session/set_mode
    are candidates from the web/ACP surfaces; this phase confirms empirically
    that they are not on the stdio surface."""
    print("\n===== Phase (a): client-side plan-mode switch (dedicated RPC) =====", flush=True)
    client = probe.client
    sid_a = f"probe4-plan-{RUN_ID}"
    col = Collector("A")
    verdict: dict = {
        "verdict": "incomplete",
        "toggle_method": None,
        "on_params": None,
        "off_params": None,
        "persist_on_turn": None,
        "persist_off_turn": None,
        "scoped_check": None,
        "session": sid_a,
    }

    # 1) commands/list: pin the wire param shape + real command roster
    # (no session exists yet — unknown-method errors come back before any
    # session handling; a success would prove the endpoint is live).
    if probe.discover_cmd_shape(sid_a):
        print("[A] NOTE: commands/list answered ok — continuing with persistence checks.", flush=True)

    # 2) session/set_mode — core-surface candidate, one attempt on a never-
    #    created session id (unknown-method vs no-session are distinct, both
    #    informative; any ok would need a created session, handled below).
    sid_sm = f"probe4-setmode-{RUN_ID}"
    set_mode_rec = try_rpc(client, "session/set_mode candidate", "session/set_mode",
                           {"sessionId": sid_sm, "modeId": "plan"}, probe.rpc_log)
    set_mode_ok = set_mode_rec["outcome"] == "ok"
    print(f"[A] session/set_mode candidate -> {set_mode_rec['outcome']} "
          f"(code={set_mode_rec.get('code')}, message={set_mode_rec.get('message')!r})", flush=True)

    # 3) commands/execute /plan (on) — param-shape variants until one works.
    on_params = None
    exec_outcomes: list[tuple[str, object, object]] = []  # (outcome, code, message)
    lines = ["/plan", "/plan on"]
    for line in lines:
        for params in (
            {"agentId": sid_a, "line": line},
            {"args": {"agentId": sid_a, "line": line}},
            {"sessionId": sid_a, "line": line},
        ):
            rec = try_rpc(client, f"commands/execute {line!r} candidate", "commands/execute", params, probe.rpc_log)
            exec_outcomes.append((rec["outcome"], rec.get("code"), rec.get("message")))
            if rec["outcome"] == "ok":
                value = rec.get("raw", {})
                raw = json.dumps(value, default=str)
                if "unknown command" in raw or (isinstance(value, dict) and value.get("ok") is False):
                    print(f"[A] commands/execute accepted params but reported command error: {raw[:400]}", flush=True)
                    on_params = None
                    continue
                on_params = params
                drain_subscription(probe.gsub, col, 2.0, "A.plan-on.drain")
                break
        if on_params is not None:
            break
    if on_params is None:
        if set_mode_ok:
            verdict["verdict"] = "inconclusive"
            print("[A] (a) INCONCLUSIVE: commands/execute failed on every shape but session/set_mode answered ok (semantics unknown).", flush=True)
        elif exec_outcomes and all(is_unknown_method(o, c, m) for o, c, m in exec_outcomes):
            verdict["verdict"] = "negative"
            codes = sorted({c for _, c, _ in exec_outcomes if c is not None})
            verdict["evidence"] = (
                "commands/execute answered 'unknown DeepSeek Harness SDK runtime method' "
                f"on every param shape (wire code {codes}); session/set_mode likewise "
                f"(code={set_mode_rec.get('code')}, message={set_mode_rec.get('message')!r}). "
                "Binary evidence: the dsh stdio server's dispatcher switch covers only "
                "initialize / session/prompt / shutdown — no command or mode endpoint is "
                "registered on this surface."
            )
            print(f"[A] (a) NEGATIVE: {verdict['evidence']}", flush=True)
        else:
            verdict["verdict"] = "inconclusive"
            print(f"[A] (a) INCONCLUSIVE: commands/execute returned non-unknown-method errors or no response; outcomes={exec_outcomes}", flush=True)
        return verdict, col, sid_a

    verdict["toggle_method"] = "commands/execute"
    verdict["on_params"] = on_params
    if on_params.get("agentId") is not None:
        probe.cmd_shape = "agentId"
    elif "args" in on_params:
        probe.cmd_shape = "args-agentId"
    elif on_params.get("sessionId") is not None:
        probe.cmd_shape = "sessionId"

    # 4) Persistence: a follow-up session/prompt turn must reflect the switch.
    col4 = Collector("A.persist-on")
    out4 = run_turn(client, sid_a, CHECK_PROMPT, col4, 50.0, gsub=probe.gsub)
    ans4 = (col4.assistant_texts[-1].strip() if col4.assistant_texts else "")
    verdict["persist_on_turn"] = ans4
    print(f"[A] persist-on check: {ans4!r}", flush=True)

    # 5) Scope: a fresh session must NOT see the switch (session-scoped?).
    sid_b = f"probe4-planscope-{RUN_ID}"
    col5 = Collector("A.scope")
    out5 = run_turn(client, sid_b, CHECK_PROMPT, col5, 50.0, gsub=probe.gsub)
    ans5 = (col5.assistant_texts[-1].strip() if col5.assistant_texts else "")
    verdict["scoped_check"] = ans5
    print(f"[A] scope check (fresh session): {ans5!r}", flush=True)

    # 6) /plan off + persistence check.
    off_params = dict(on_params)
    for key in ("line",):
        if key in off_params:
            off_params[key] = "/plan off"
    rec = try_rpc(client, "commands/execute /plan off", "commands/execute", off_params, probe.rpc_log)
    if rec["outcome"] == "ok":
        verdict["off_params"] = off_params
        drain_subscription(probe.gsub, col, 2.0, "A.plan-off.drain")
    else:
        verdict["off_params"] = None
        print(f"[A] /plan off failed: {rec}", flush=True)

    col6 = Collector("A.persist-off")
    out6 = run_turn(client, sid_a, CHECK_PROMPT, col6, 50.0, gsub=probe.gsub)
    ans6 = (col6.assistant_texts[-1].strip() if col6.assistant_texts else "")
    verdict["persist_off_turn"] = ans6
    print(f"[A] persist-off check: {ans6!r}", flush=True)

    verdict["verdict"] = "positive"
    print(
        f"[A] (a) POSITIVE: toggle via {verdict['toggle_method']} "
        f"params={json.dumps(on_params)}; on-check={ans4!r} scope-check={ans5!r} off-check={ans6!r}",
        flush=True,
    )

    return verdict, col, sid_a


def phase_b(probe: Probe, cmd_mode: str) -> dict:
    """(b) effective approval behavior + settable knob.

    cmd_mode: "rpc" when (a) found a working commands/execute (switch via
    RPC); "text" when (a) is negative/inconclusive (switch via slash text,
    the only remaining client path — also part of the (e)/(b2) answer).
    """
    print(f"\n===== Phase (b): approval behavior + settable knob (cmd_mode={cmd_mode}) =====", flush=True)
    client = probe.client
    sid = f"probe4-approval-{RUN_ID}"
    col = Collector("B")
    verdict: dict = {
        "session": sid,
        "cmd_mode": cmd_mode,
        "baseline_policy": None,
        "baseline_requests_seen": None,
        "gated_turn": None,
        "gated_requests_seen": None,
        "gated_verdict": None,
        "permission_list": None,
        "settable": None,
        "switched_to_ask": None,
        "switched_to_never": None,
        "never_policy_sentence": None,
    }

    if cmd_mode == "rpc" and not probe.cmd_list_ok and not probe.discover_cmd_shape(sid):
        print("[B] warning: commands/list never succeeded; using default shape", flush=True)

    def switch(line: str, label: str) -> dict:
        if cmd_mode == "rpc":
            return probe.run_cmd(sid, line, col, label)
        return probe.run_cmd_text(sid, line, col, label)

    def switch_ok(rec: dict) -> bool:
        if cmd_mode == "rpc":
            return rec["outcome"] == "ok"
        return rec.get("interpreted") is True

    # B1: baseline tool turn (same shape as probe-output-2-tool.txt) with a
    # bounded non-blocking peek loop on client._requests.
    w = RequestWatcher(client, "B1", respond="allow")
    w.start()
    out = run_turn(
        client, sid,
        "Use your bash tool to run exactly: echo probe4-baseline. Then report the command output verbatim.",
        col, 80.0, gsub=probe.gsub,
    )
    w.stop()
    verdict["baseline_requests_seen"] = [
        {"id": r.id, "method": r.method} for r in w.received
    ]
    verdict["baseline_policy"] = col.policy_sentence_verdict()
    verdict["baseline_sentences"] = col.approval_policy_sentences
    print(f"[B1] baseline tool turn: {out}", flush=True)
    print(f"[B1] runtime->client requests seen: {verdict['baseline_requests_seen']}", flush=True)
    print(f"[B1] baseline policy sentence(s): {col.approval_policy_sentences}", flush=True)

    # B2: /permission with no args — current preset + available presets.
    rec = switch("/permission", "B2 /permission (list)")
    verdict["permission_list"] = rec

    # B3: ensure policy=ask for the gated test (switch only if needed).
    if verdict["baseline_policy"] != "ask":
        rec = switch("/permission workspace-write", "B3 /permission workspace-write")
        verdict["switched_to_ask"] = rec
        verdict["settable"] = switch_ok(rec)
    else:
        verdict["switched_to_ask"] = None  # already ask; knob not needed

    # B4: gated operation (outside the sandbox workspace root) under ask.
    results_before_b4 = len(col.tool_results)
    sentences_before_b4 = len(col.approval_policy_sentences)
    w2 = RequestWatcher(client, "B4", respond="allow")
    w2.start()
    out2 = run_turn(
        client, sid,
        "Use your bash tool to run exactly: touch \"$HOME/probe4-gated-test\" && echo gated-ok && rm -f \"$HOME/probe4-gated-test\". "
        "This deliberately targets outside the workspace so the sandbox policy is exercised. "
        "Report the tool outcome verbatim, including any error text.",
        col, 80.0, gsub=probe.gsub,
    )
    w2.stop()
    verdict["gated_turn"] = out2
    verdict["gated_requests_seen"] = [
        {"id": r.id, "method": r.method} for r in w2.received
    ]
    new_sentences_b4 = col.approval_policy_sentences[sentences_before_b4:]
    verdict["gated_policy_sentences"] = new_sentences_b4
    if new_sentences_b4:
        verdict["gated_policy_at_test"] = policy_verdict(new_sentences_b4)
    elif verdict["switched_to_ask"] is not None:
        verdict["gated_policy_at_test"] = "ask" if switch_ok(verdict["switched_to_ask"]) else "unknown (switch failed)"
    else:
        verdict["gated_policy_at_test"] = verdict["baseline_policy"]
    tool_errs = [tr for tr in col.tool_results[results_before_b4:] if tr["isError"]]
    if w2.received:
        if out2["idle"] and not tool_errs:
            verdict["gated_verdict"] = "approval request reached the stdio client; client answer completed the gated operation"
        else:
            verdict["gated_verdict"] = f"approval request reached the client but the turn did not settle cleanly: {out2}"
    elif tool_errs:
        verdict["gated_verdict"] = (
            "no client request arrived; gated operation failed closed under ask. "
            f"first tool error: {tool_errs[0]['text'][:300]!r}"
        )
    elif out2["idle"]:
        verdict["gated_verdict"] = "unexpected: gated operation succeeded with no approval request"
    else:
        verdict["gated_verdict"] = f"unexpected: no request, no clean turn: {out2}"
    print(f"[B4] gated turn under ask: requests={verdict['gated_requests_seen']} verdict={verdict['gated_verdict']!r}", flush=True)

    # B5: switch to danger-full-access (approval=never) + confirm the new
    # policy sentence on a follow-up turn (persists across session/prompt).
    rec = switch("/permission danger-full-access", "B5 /permission danger-full-access")
    verdict["switched_to_never"] = rec
    if verdict["settable"] is None:
        verdict["settable"] = switch_ok(rec)
    col7 = Collector("B.never")
    out7 = run_turn(
        client, sid,
        "Use your bash tool to run exactly: echo probe4-full-access. Then report the command output verbatim.",
        col7, 60.0, gsub=probe.gsub,
    )
    b5_policy_events = [pe for pe in col.permission_events if pe["type"] == "approval/policy"]
    verdict["b5_policy_events"] = b5_policy_events
    verdict["never_policy_sentence"] = col7.policy_sentence_verdict()
    if verdict["never_policy_sentence"] is not None:
        verdict["never_policy_source"] = "follow-up turn snapshot"
        verdict["never_sentences"] = col7.approval_policy_sentences
    elif b5_policy_events:
        verdict["never_policy_sentence"] = b5_policy_events[-1]["data"].get("policy")
        verdict["never_policy_source"] = "approval/policy session event from the switch"
        verdict["never_sentences"] = col.approval_policy_sentences
    else:
        verdict["never_policy_source"] = "unverified (no new snapshot, no policy event)"
        verdict["never_sentences"] = col.approval_policy_sentences
    print(f"[B5] never-switch: {json.dumps({k: v for k, v in rec.items() if k != 'raw'}, default=str)} raw={json.dumps(rec.get('raw'), default=str)[:600]}", flush=True)
    print(f"[B5] follow-up policy sentence verdict: {verdict['never_policy_sentence']!r} source={verdict.get('never_policy_source')!r} sentences={col7.approval_policy_sentences}", flush=True)

    print(
        f"[B] verdict: baseline={verdict['baseline_policy']!r} settable={verdict['settable']!r} "
        f"baseline-requests={verdict['baseline_requests_seen']} gated={verdict['gated_verdict']!r} "
        f"never-sentence={verdict['never_policy_sentence']!r}",
        flush=True,
    )
    return verdict


def phase_c(probe: Probe, plan_mode: str) -> dict:
    """(c) plan mode ON + trivial file-edit task; exit_plan_mode on a stdio
    client with no interactive review (watcher deliberately does not answer).

    plan_mode: "rpc" when (a) found a working commands/execute; "text" when
    plan mode must be entered by typing /plan as plain message text (the only
    remaining client path when (a) is negative)."""
    print(f"\n===== Phase (c): plan mode ON + file-edit task (plan_mode={plan_mode}) =====", flush=True)
    client = probe.client
    sid = f"probe4-planmode-{RUN_ID}"
    col = Collector("C")
    verdict: dict = {
        "session": sid,
        "plan_on_via": plan_mode,
        "plan_on_ok": None,
        "turn": None,
        "client_requests_seen": None,
        "plan_text_channel": None,
        "exit_plan_mode_calls": 0,
        "verdict": "incomplete",
    }

    out0 = run_turn(
        client, sid,
        "This is a probe session. Reply with exactly one word: ready.",
        col, 40.0, gsub=probe.gsub,
    )
    print(f"[C] session-creation turn: {out0}", flush=True)

    if plan_mode == "rpc":
        rec = probe.run_cmd(sid, "/plan", col, "C plan on")
        plan_on_ok = rec["outcome"] == "ok"
        if not plan_on_ok:
            verdict["plan_on_ok"] = rec["outcome"]
            verdict["verdict"] = f"incomplete: plan on failed ({rec.get('code')}, {rec.get('message')!r})"
            return verdict
    else:
        rec = probe.run_cmd_text(sid, "/plan", col, "C plan on (slash text)")
        plan_on_ok = rec.get("interpreted") is True or bool(
            [pe for pe in rec.get("plan_events", []) if pe.get("active")]
        )
        if not plan_on_ok:
            verdict["plan_on_ok"] = f"text-not-interpreted (assistant: {rec.get('assistant_reply')!r})"
            verdict["verdict"] = (
                "incomplete: plan mode could not be entered — the /plan slash text was "
                f"not interpreted as a command (no command/plan events; reply: {rec.get('assistant_reply')!r})"
            )
            return verdict
    verdict["plan_on_ok"] = "ok"

    w = RequestWatcher(client, "C", respond="none")
    w.start()
    task = (
        "Create a new file at investigation/probe/probe4-plan-test.txt containing exactly the "
        "single line: probe4 plan test. Do it now; when your plan is complete, submit it."
    )
    out = run_turn(client, sid, task, col, 120.0, gsub=probe.gsub)
    w.stop()
    verdict["turn"] = out
    verdict["client_requests_seen"] = [
        {"id": r.id, "method": r.method} for r in w.received
    ]
    exit_calls = [tc for tc in col.tool_calls if tc["name"] == "exit_plan_mode"]
    exit_call_ids = {tc.get("callId") for tc in exit_calls if tc.get("callId")}
    verdict["exit_plan_mode_calls"] = len(exit_calls)
    verdict["exit_plan_mode_call_args"] = [
        str(tc.get("arguments"))[:400] for tc in exit_calls
    ]
    # Results paired to the exit_plan_mode calls by callId (order is not
    # reliable; the raw stream above keeps the full events).
    verdict["exit_plan_mode_results"] = [
        tr for tr in col.tool_results if tr.get("callId") in exit_call_ids
    ]
    if col.assistant_texts:
        verdict["plan_text_channel"] = "assistant/message"
    if col.system_texts:
        verdict["plan_text_channel"] = (verdict["plan_text_channel"] or "") + " + system/message"

    if out["timeout"]:
        verdict["verdict"] = (
            "HANG: turn did not complete within 120s; "
            f"client_requests_seen={verdict['client_requests_seen']}; "
            f"exit_plan_mode calls={len(exit_calls)}; last events={col.event_types[-6:]}"
        )
    elif out["idle"]:
        exit_results = verdict["exit_plan_mode_results"]
        if exit_calls:
            errs = [tr for tr in exit_results if tr["isError"]]
            if not exit_results:
                verdict["verdict"] = (
                    f"UNSETTLED: exit_plan_mode called {len(exit_calls)}x but no paired tool/result "
                    "arrived before turn/end (see raw stream)"
                )
            elif not errs:
                verdict["verdict"] = (
                    f"COMPLETES: exit_plan_mode succeeded on a stdio client (calls={len(exit_calls)}); "
                    f"result: {exit_results[0]['text'][:300]!r}"
                )
            else:
                verdict["verdict"] = (
                    f"FAILS CLEANLY: exit_plan_mode was called {len(exit_calls)}x and failed with "
                    f"error(s): {errs[0]['text'][:300]!r}; turn still ended (reason in turn/end event)"
                )
        else:
            verdict["verdict"] = (
                "TURN COMPLETED WITHOUT exit_plan_mode: the model stayed in plan mode "
                "(plan text only; no exit tool call). last assistant text: "
                f"{(col.assistant_texts[-1][:200] if col.assistant_texts else '')!r}"
            )
    else:
        verdict["verdict"] = f"ERROR: {out.get('error')!r}"
    print(f"[C] verdict: {verdict['verdict']!r}", flush=True)
    return verdict


def phase_e(probe: Probe) -> dict:
    """(e) slash command typed as plain message text: interpreted or not.

    Probe text is a bare "/plan" (the binary maps bare /plan to plan mode ON
    without model input), so an interpreted command produces command/run +
    command/done (and a plan/mode event) with NO assistant reply, while
    uninterpreted text produces an ordinary assistant reply."""
    print("\n===== Phase (e): slash command as plain message text =====", flush=True)
    client = probe.client
    sid = f"probe4-slashtext-{RUN_ID}"
    col = Collector("E")
    verdict: dict = {
        "session": sid,
        "probe_text": "/plan",
        "slash_turn": None,
        "command_events": None,
        "plan_events": None,
        "check_answer": None,
        "verdict": "incomplete",
    }
    out1 = run_turn(client, sid, "/plan", col, 60.0, gsub=probe.gsub)
    verdict["slash_turn"] = out1
    verdict["command_events"] = col.command_events
    verdict["plan_events"] = col.plan_events
    out2 = run_turn(client, sid, CHECK_PROMPT, col, 50.0, gsub=probe.gsub)
    verdict["check_answer"] = (col.assistant_texts[-1].strip() if col.assistant_texts else "")
    if bool(col.command_events) or bool(col.plan_events):
        verdict["verdict"] = (
            "INTERPRETED: runtime handled the plain-text slash command via the command "
            f"pipeline (command_events={len(col.command_events)}, plan_events={len(col.plan_events)})"
        )
    elif "ON" in verdict["check_answer"].upper():
        verdict["verdict"] = (
            f"AMBIGUOUS: no command/plan events but the follow-up check says {verdict['check_answer']!r}"
        )
    else:
        first_reply = col.assistant_texts[0][:160] if col.assistant_texts else "nothing"
        verdict["verdict"] = (
            "NOT INTERPRETED: the slash text was treated as an ordinary message "
            f"(assistant replied: {first_reply!r}; "
            f"follow-up check: {verdict['check_answer']!r}"
        )
    print(f"[E] verdict: {verdict['verdict']!r}", flush=True)
    return verdict


def main() -> int:
    config = make_config()
    print(
        f"probe4: run_id={RUN_ID} dsh_home={DSH_HOME} cwd={REPO_ROOT} "
        f"deadline={DEADLINE_SECONDS:.0f}s ONIT_API_KEY={'set' if os.environ.get('ONIT_API_KEY') else 'MISSING'}",
        file=sys.stderr,
    )
    findings: dict = {}
    escalation: str | None = None
    probe = None
    if not os.environ.get("ONIT_API_KEY"):
        print("FATAL: ONIT_API_KEY not set — run via run_probe4.sh", file=sys.stderr)
        return 2
    with DeepSeekHarness(config) as harness:
        client = harness.client
        # One global subscription (no filter) drives both run_turn and the
        # bounded post-RPC drains; every notification is logged exactly once.
        gsub = client.subscribe_notifications(None)
        try:
            probe = Probe(client, gsub)

            # ---- (a) -------------------------------------------------------
            if remaining() > 200:
                try:
                    findings["a"], col_a, sid_a = phase_a(probe)
                except Exception as exc:
                    findings["a"] = {"verdict": f"incomplete: {exc!r}"}
                    print(f"[A] phase exception: {exc!r}", flush=True)
            else:
                findings["a"] = {"verdict": "incomplete: global deadline"}

            a_verdict = findings["a"]["verdict"]
            if a_verdict == "negative":
                escalation = (
                    "STOP-AND-ESCALATE (per ticket): (a) negative — no dedicated client-side "
                    "JSON-RPC method toggles plan mode on the stdio surface. Per the ticket's "
                    "Must Verify, this run still completes the remaining findings (e)/(b)/(c) "
                    "— (e) in particular determines whether ANY client path exists (slash text "
                    "via session/prompt). This probe does no UI work and attempts no workaround."
                )
                print(f"\n{escalation}", flush=True)

            # ---- (e) promoted before (b)/(c): it gates the text paths ------
            if remaining() > 140:
                try:
                    findings["e"] = phase_e(probe)
                except Exception as exc:
                    findings["e"] = {"verdict": f"incomplete: {exc!r}"}
                    print(f"[E] phase exception: {exc!r}", flush=True)
            else:
                findings["e"] = {"verdict": "incomplete: global deadline"}
            e_verdict = str(findings["e"]["verdict"])

            # ---- (b) --------------------------------------------------------
            cmd_mode = "rpc" if a_verdict == "positive" else "text"
            if remaining() > 240:
                try:
                    findings["b"] = phase_b(probe, cmd_mode)
                except Exception as exc:
                    findings["b"] = {"verdict": f"incomplete: {exc!r}"}
                    print(f"[B] phase exception: {exc!r}", flush=True)
            else:
                findings["b"] = {"verdict": "incomplete: global deadline"}

            # ---- (c) --------------------------------------------------------
            plan_mode = None
            if a_verdict == "positive":
                plan_mode = "rpc"
            elif e_verdict.startswith(("INTERPRETED", "AMBIGUOUS")):
                plan_mode = "text"
            if plan_mode is None:
                findings["c"] = {
                    "verdict": (
                        "skipped: plan mode unreachable from the client in this run "
                        "(a not positive; e not interpreted)"
                    )
                }
            elif remaining() > 190:
                try:
                    findings["c"] = phase_c(probe, plan_mode)
                except Exception as exc:
                    findings["c"] = {"verdict": f"incomplete: {exc!r}"}
                    print(f"[C] phase exception: {exc!r}", flush=True)
                if str(findings["c"]["verdict"]).startswith("HANG"):
                    escalation = (
                        "STOP-AND-ESCALATE (per ticket): (c) shows a hang — "
                        "exit_plan_mode wedged the turn on a stdio client."
                    )
                    print(f"\n{escalation}", flush=True)
            else:
                findings["c"] = {"verdict": "incomplete: global deadline"}
        finally:
            try:
                gsub.close()
            except Exception:
                pass

    print("\n\n========== SUMMARY ==========")
    print(f"run_id={RUN_ID} wall={time.monotonic() - START:.1f}s")
    print(f"(a) {json.dumps(findings.get('a'), default=str)[:800]}")
    print(f"(b) {json.dumps(findings.get('b'), default=str)[:1200]}")
    print(f"(c) {json.dumps(findings.get('c'), default=str)[:800]}")
    print(f"(e) {json.dumps(findings.get('e'), default=str)[:800]}")
    if escalation:
        print(f"ESCALATION: {escalation}")
    print("\nrpc candidate log (method/params/outcome):")
    for rec in (probe.rpc_log if probe is not None else []):
        slim = {k: v for k, v in rec.items() if k != "raw"}
        print(f"  {json.dumps(slim, default=str)[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
