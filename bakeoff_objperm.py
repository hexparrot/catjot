#!/usr/bin/env python3
"""Approach bake-off for object-permanence capture (place_object under-fire).

Tier 3 of OBJECT_PERMANENCE §4.3. Objects change hands with no deterministic
possession classifier — the same failure class as neg_navto's forced movement
(0% tool-fire on weak models; prohibitions in tool descriptions are inert). The
read side (Tier 1) is green regardless; this harness tunes the model-dependent
CAPTURE channel that Tier 1 does not depend on. A poor score here lowers capture
rate, never correctness.

It measures a 2×2 factorial — does the nudge_pos_desc precedent transfer to
place_object, and does the [OBJECTS HERE] vocabulary block have a tool-selection
side-effect beyond its naming purpose:

  baseline     no fn-desc,  no [OBJECTS HERE] block   (control)
  fn_desc      place_object fn-desc one-liner in the compact schema, no block
  objects_here no fn-desc,  [OBJECTS HERE] block injected into the world state
  fn_desc+oh   both — the proposed production config

Unlike bakeoff_navnudge there is NO CLASSIFIER knob: the arms are schema/context
variants applied UNCONDITIONALLY, not injections gated on a per-turn label.

Scoring keys on expects_place. Every positive bucket's object noun reappears in
a mention-negative twin (the minimal-pair discipline):
  pickup/handover/drop/state-change -> place_object MUST fire
  mention-negative                  -> place_object must NOT fire
  fallback (weak-model)             -> place_object fired OR record_event fired
                                       with an obj: tag word (the layered
                                       fallback contract, not a single tool)

Decision rule: ship the fn-desc one-liner iff it wins mention-negative without
losing pickup/handover (the exact bar nudge_pos_desc cleared for navigate_to;
scope = place_object only). [OBJECTS HERE] ships regardless (its primary job is
I9 vocabulary, Phase 1); the sweep only measures its selection side-effect.

Usage:
  python3 bakeoff_objperm.py                     # default models, ROUNDS=3
  ROUNDS=5 python3 bakeoff_objperm.py            # more repeats
  python3 bakeoff_objperm.py modelA modelB       # explicit model list

Requires openai_api_url (+ key if needed) and openai_api_model-swept endpoint up.
Refuses to run on a dead endpoint (else every call errors into a fake matrix).
Set FORCE=1 to skip preflight. CALL_TIMEOUT caps each LLM call (default 60s).
"""
import json
import os
import sys
import threading
import time
import urllib.request

from rpjot import RPJotEngine
from catjot import ContextBundle, call_llm

ROUNDS = int(os.environ.get("ROUNDS", "3"))
CALL_TIMEOUT = int(os.environ.get("CALL_TIMEOUT", "60"))
PREFLIGHT_TIMEOUT = int(os.environ.get("PREFLIGHT_TIMEOUT", "30"))

DEFAULT_MODELS = [
    "qwen3-30b-a3b-instruct-2507",
    "gemma4-31b-it",
    "qwen3-vl-32b-instruct",
    "qwen3-235b-a22b-instruct-2507",
    "devstral2-123b",
    "granite41-30b",
]

WORLD_DOC = "WORLD STATE: you stand in the manor foyer with Evie."

# The place_object fn-description one-liner (identical to the production tool
# description and the _COMPACT_KEEP_FUNCTION_DESCRIPTIONS entry, so production ==
# the swept harness).
PLACE_FUNCTION_DESC = (
    "Log an object changing hands or rooms — picked up, handed over, dropped, "
    "stowed, left behind; also to note a lasting change to its condition."
)

# The deterministic vocabulary block (canonical slugs). Its primary job is I9
# naming; the sweep measures any tool-selection side-effect.
OBJECTS_HERE_BLOCK = (
    "[OBJECTS HERE] (canonical slugs — reuse these exact names):\n"
    "in this room: locket, iron-key, lantern, music-box, satchel"
)

# --- The corpus: bucket + ground truth (expects_place). Every positive noun
#     reappears in a mention-negative twin (minimal-pair discipline).
CORPUS = [
    dict(id="pickup", bucket="pickup", expects_place=True, obj="locket",
         text="Evie plucks the locket from the bench and pockets it."),
    dict(id="handover", bucket="handover", expects_place=True, obj="iron-key",
         text="[MC action]: I press the iron key into Evie's palm."),
    dict(id="drop", bucket="drop", expects_place=True, obj="lantern",
         text="[MC action]: I leave the lantern by the garden gate."),
    dict(id="state_change", bucket="state-change", expects_place=True,
         obj="music-box",
         text="[MC action]: I wind the music box until the spring snaps."),
    dict(id="neg_locket", bucket="mention-negative", expects_place=False,
         obj="locket",
         text="Evie talks about the locket she lost years ago."),
    dict(id="neg_key", bucket="mention-negative", expects_place=False,
         obj="iron-key",
         text='[MC speaks aloud]: "Bring the iron key tomorrow."'),
    dict(id="fallback", bucket="fallback", expects_place=True, obj="satchel",
         text="The guards confiscate my satchel and drag me to the cells."),
]

ARMS = ["baseline", "fn_desc", "objects_here", "fn_desc+oh"]


def bucket_of(phrase):
    return phrase["bucket"]


def is_correct(cell, phrase):
    """Correct when the residence change was captured as expected.

    Positive buckets: place_object fired. mention-negative: it did NOT.
    fallback: place_object fired OR record_event fired with an obj: tag word
    (the layered-mitigation contract)."""
    if phrase["bucket"] == "fallback":
        return cell["place"] + cell["event_obj"] > 0
    if phrase["expects_place"]:
        return cell["place"]
    return cell["real"] - cell["place"]  # mention-negative: correct == not fired


def _compact_schemas(engine, with_place_desc):
    """A copy of engine._compact_step2_schemas with place_object's fn-description
    explicitly present or absent (the fn_desc arm control), independent of what
    production ships. Built locally so production compaction is untouched."""
    tools = [dict(t, function=dict(t["function"]))
             for t in engine._compact_step2_schemas]
    for t in tools:
        if t["function"]["name"] == "place_object":
            if with_place_desc:
                t["function"]["description"] = PLACE_FUNCTION_DESC
            else:
                t["function"].pop("description", None)
    return tools


def preflight(model):
    """Fire a real completion and validate choices[0].message shape. Any
    deviation => don't sweep (a health-path 200 would fake-green the matrix)."""
    url = os.environ.get("openai_api_url", "")
    if not url:
        return False, "openai_api_url is unset"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with one word: ready"}],
        "max_tokens": 16, "temperature": 0,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    key = os.environ.get("openai_api_key", "")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=PREFLIGHT_TIMEOUT) as r:
            if r.status != 200:
                return False, f"HTTP {r.status}"
            data = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return False, f"HTTP 200 but no choices[0].message (body: {str(data)[:120]})"
    if not msg.get("content") and not msg.get("tool_calls"):
        return False, "HTTP 200 but message empty (no content or tool_calls)"
    return True, "HTTP 200, valid completion"


def _capped(fn, timeout):
    """Run fn() in a daemon thread; raise TimeoutError past `timeout` seconds.
    call_llm sets no request timeout, so this is the only 60s cap that holds."""
    box = {}

    def run():
        try:
            box["r"] = fn()
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"exceeded {timeout}s")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def selected_calls(engine, phrase, arm):
    """One production-shape step-2 call under `arm`; return [(name, args_dict)].

    The arm is applied unconditionally (no per-turn classification): fn_desc
    controls place_object's compact fn-description; objects_here injects the
    vocabulary block into the world state briefing."""
    with_desc = arm in ("fn_desc", "fn_desc+oh")
    with_block = arm in ("objects_here", "fn_desc+oh")
    tools = _compact_schemas(engine, with_desc)

    rules = str(ContextBundle("system_role")).strip() or (
        "You are the game master. Use tools to record canon."
    )
    world = WORLD_DOC + (f"\n\n{OBJECTS_HERE_BLOCK}" if with_block else "")
    parts = [
        f"WORLD STATE BRIEFING:\n{world}",
        f"NARRATOR RULE: {engine._NARRATOR_RULE}",
        phrase["text"],
    ]
    messages = [
        {"role": "system", "content": rules},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    resp = call_llm(messages, tools=tools, tool_choice="auto")
    out = []
    for tc in (resp.get("tool_calls") or []):
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        out.append((name, args))
    return out


def _tallies(calls):
    """From [(name, args)] compute (place_fired, event_obj_fired)."""
    place = any(n == "place_object" for n, _ in calls)
    event_obj = any(
        n == "record_event"
        and any(t.startswith("obj:") for t in str(a.get("tags", "")).split())
        for n, a in calls
    )
    return place, event_obj


def pct(num, den):
    return f"{100 * num / den:4.0f}%" if den else "  - "


def main():
    models = sys.argv[1:] or DEFAULT_MODELS
    force = os.environ.get("FORCE") == "1"
    if not force:
        ok, detail = preflight(models[0])
        if not ok:
            print(f"PREFLIGHT FAILED against {os.environ.get('openai_api_url')}")
            print(f"  {detail}")
            print("  Endpoint looks down — every call would error into a fake")
            print("  matrix. Bring the model server up, then re-run. (FORCE=1 to override.)")
            sys.exit(2)
        print(f"preflight ok ({detail})")

    print(f"arms={len(ARMS)}  phrases={len(CORPUS)}  rounds={ROUNDS}  "
          f"call_timeout={CALL_TIMEOUT}s\n")

    engine = RPJotEngine(location="ravenwood-manor",
                         people_present={"player", "evie"})
    engine.register_all_tools()

    # res[model][arm][phrase_id] = {"place": n, "event_obj": n, "real": n, "err": n}
    res = {m: {a: {p["id"]: {"place": 0, "event_obj": 0, "real": 0, "err": 0}
                   for p in CORPUS} for a in ARMS} for m in models}
    secs = {m: 0.0 for m in models}
    down = {}

    for m in models:
        if not force:
            ok, detail = preflight(m)
            if not ok:
                down[m] = detail
                print(f"  {m:<30} SKIPPED — preflight failed: {detail}")
                continue
        os.environ["openai_api_model"] = m
        t0 = time.monotonic()
        for a in ARMS:
            for p in CORPUS:
                cell = res[m][a][p["id"]]
                for _ in range(ROUNDS):
                    try:
                        calls = _capped(
                            lambda: selected_calls(engine, p, a), CALL_TIMEOUT
                        )
                    except Exception:  # noqa: BLE001 - timeout or transport error
                        cell["err"] += 1
                        continue
                    cell["real"] += 1
                    place, event_obj = _tallies(calls)
                    cell["place"] += place
                    cell["event_obj"] += event_obj
            # per-(model,arm) correctness split by bucket group
            pos = [0, 0]   # pickup/handover/drop/state-change
            neg = [0, 0]   # mention-negative
            fb = [0, 0]    # fallback
            for p in CORPUS:
                c = res[m][a][p["id"]]
                tgt = fb if p["bucket"] == "fallback" else (
                    neg if not p["expects_place"] else pos
                )
                tgt[0] += is_correct(c, p)
                tgt[1] += c["real"]
            tot_ok = pos[0] + neg[0] + fb[0]
            tot_n = pos[1] + neg[1] + fb[1]
            print(f"  {m:<30} {a:<13} "
                  f"pos {pos[0]:>2}/{pos[1]:<2}  "
                  f"neg {neg[0]:>1}/{neg[1]:<1}  "
                  f"fb {fb[0]:>1}/{fb[1]:<1}  "
                  f"all {tot_ok:>2}/{tot_n:<2}")
        secs[m] = time.monotonic() - t0
        print(f"  {'':<30} {'--':<13} {secs[m]:.0f}s\n")

    swept = [m for m in models if m not in down]
    if not swept:
        print("\nNo models swept — all failed preflight. Bring the endpoint up.")
        sys.exit(3)

    # ---- headline: arm scorecard (aggregate over models + rounds) ----
    print("ARM SCORECARD  (aggregate over swept models & rounds)")
    print(f"  {'arm':<13} {'pos(place)':>11} {'neg(mention)':>13} "
          f"{'fallback':>9} {'overall':>8}")
    agg = {}
    for a in ARMS:
        pos = [0, 0]
        neg = [0, 0]
        fb = [0, 0]
        for m in swept:
            for p in CORPUS:
                c = res[m][a][p["id"]]
                tgt = fb if p["bucket"] == "fallback" else (
                    neg if not p["expects_place"] else pos
                )
                tgt[0] += is_correct(c, p)
                tgt[1] += c["real"]
        agg[a] = (pos, neg, fb)
        print(f"  {a:<13} {pct(pos[0], pos[1]):>11} {pct(neg[0], neg[1]):>13} "
              f"{pct(fb[0], fb[1]):>9} "
              f"{pct(pos[0] + neg[0] + fb[0], pos[1] + neg[1] + fb[1]):>8}")

    # ---- per-phrase place_object firing rate ----
    print("\nPER-PHRASE place_object FIRING RATE  "
          "(want: positives 100%, mention-negative 0%)")
    print(f"  {'phrase':<14} {'bucket':<15}"
          + "".join(f"{a[:11]:>12}" for a in ARMS) + f"{'want':>7}")
    for p in CORPUS:
        row = f"  {p['id']:<14} {p['bucket']:<15}"
        for a in ARMS:
            place = sum(res[m][a][p["id"]]["place"] for m in swept)
            real = sum(res[m][a][p["id"]]["real"] for m in swept)
            row += f"{pct(place, real):>12}"
        row += f"{('0%' if not p['expects_place'] else '100%'):>7}"
        print(row)

    # ---- the §4.3 decision: does the fn-desc one-liner clear the bar? ----
    def _rate(a, group):  # group: 0=pos, 1=neg, 2=fb
        g = agg[a][group]
        return (g[0] / g[1]) if g[1] else 0.0

    print("\nDECISION (§4.3): ship the place_object fn-desc iff it wins "
          "mention-negative\nwithout losing pickup/handover (vs the same "
          "[OBJECTS HERE] condition).")
    for base, desc, label in (
        ("baseline", "fn_desc", "no [OBJECTS HERE]"),
        ("objects_here", "fn_desc+oh", "with [OBJECTS HERE]"),
    ):
        neg_win = _rate(desc, 1) >= _rate(base, 1)
        pos_hold = _rate(desc, 0) >= _rate(base, 0) - 1e-9
        verdict = "SHIP" if (neg_win and pos_hold) else "HOLD"
        print(f"  {label:<20} {base} -> {desc}: "
              f"neg {pct(*agg[base][1])}->{pct(*agg[desc][1])}  "
              f"pos {pct(*agg[base][0])}->{pct(*agg[desc][0])}  => {verdict}")

    # ---- contamination guard ----
    errs = sum(res[m][a][p["id"]]["err"]
               for m in swept for a in ARMS for p in CORPUS)
    real = sum(res[m][a][p["id"]]["real"]
               for m in swept for a in ARMS for p in CORPUS)
    if errs:
        print(f"\nNote: {errs} call(s) errored/timed out and were excluded "
              f"({real} real).")
    if down:
        print(f"Note: {len(down)} model(s) skipped on preflight: {', '.join(down)}")

    print("\nRead: [OBJECTS HERE] ships regardless (Phase 1 vocabulary). This "
          "sweep only decides the fn-desc one-liner and measures the capture "
          "channel — a poor score lowers capture rate, never Tier-1 correctness.")


if __name__ == "__main__":
    main()
