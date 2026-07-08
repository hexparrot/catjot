#!/usr/bin/env python3
"""Approach bake-off for the navigate_to over-fire (neg_navto was 0/18).

The systematic miss is that models call navigate_to on a turn where the MC only
*spoke* or *thought* — acting on an NPC's invitation instead of the MC's own
movement. A prose caveat buried in the tool description is inert. This harness
compares five ways to fix it, on the SAME phrases, across models, and prints
parsable tabular data (same spirit as bakeoff_denoise.py):

  baseline          no help — the current production shape (the control).
  hard_gate         DROP navigate_to from the menu on stationary turns (rigid).
  nudge_positive    INJECT "the story continues at the current location" (soft,
                    positive location-maintenance) on stationary turns.
  nudge_positive_v2 INJECT the same, but with an explicit ESCAPE HATCH: the scene
                    still moves if events physically carry the MC (dragged,
                    carried, a moving vehicle) — the case hard_gate cannot pass.
  nudge_pos_desc    v2's injection PLUS a one-line function-level description
                    restored on navigate_to in the compact schema (targets the
                    residual miss where the model picks among 31 tools by name).

The gate/injection fire ONLY when a *definitive* classifier labels the turn
`stationary` (MC did not physically move themselves). Default classifier is an
ORACLE (each phrase carries its true `kind`) so we measure INJECTION EFFICACY
under perfect classification — the thing actually in question. Set
CLASSIFIER=heuristic to gate on classify_heuristic() instead (a simple surface
rule; see its docstring).

Scoring is unambiguous: correct == (navigate_to selected) matches expects_navigate.
  stationary phrases  -> navigate_to is WRONG (want firing rate 0%)
  mobile phrases      -> navigate_to is RIGHT (want firing rate 100%, and every
                         strategy must hold it there — the regression guard)
  forced phrases      -> classify stationary (so the note/gate FIRES) yet the
                         scene MUST still move (expects_navigate=True). This is
                         the row hard_gate provably scores 0% on and the nudge
                         must tolerate — the justification for soft over rigid.

Usage:
  python3 bakeoff_navnudge.py                     # default models, ROUNDS=3
  ROUNDS=5 python3 bakeoff_navnudge.py            # more repeats
  python3 bakeoff_navnudge.py modelA modelB       # explicit model list
  CLASSIFIER=heuristic python3 bakeoff_navnudge.py

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
CLASSIFIER = os.environ.get("CLASSIFIER", "oracle")  # oracle | heuristic

DEFAULT_MODELS = [
    "qwen3-30b-a3b-instruct-2507",
    "gemma4-31b-it",
    "qwen3-vl-32b-instruct",
    "qwen3-235b-a22b-instruct-2507",
    "devstral2-123b",
    "granite41-30b",
]

WORLD_DOC = "WORLD STATE: you stand in the manor foyer with Evie."

# --- The corpus: definitive class (`kind`) + ground truth (`expects_navigate`).
#     stationary => MC did not physically move => navigate_to must NOT fire.
#     mobile     => MC's own body travels      => navigate_to SHOULD fire.
CORPUS = [
    # ---- stationary: the gate/injection SHOULD fire here ----
    dict(id="neg_invite", kind="stationary", expects_navigate=False,
         text='[MC speaks aloud]: "Lead on, then." '
              "Evie beckons you to follow her down the corridor."),
    dict(id="thought", kind="stationary", expects_navigate=False,
         text="[MC — inner thought]: think: why would she do this to me?"),
    dict(id="npc_moves", kind="stationary", expects_navigate=False,
         text='[MC speaks aloud]: "Safe travels, then." '
              "Evie strides out toward the garden without you."),
    dict(id="dialogue_wish", kind="stationary", expects_navigate=False,
         text='[MC speaks aloud]: "I would love to see the west wing someday."'),
    dict(id="action_nomove", kind="stationary", expects_navigate=False,
         text="[MC action]: I pick up the iron key from the table and pocket it."),
    # ---- forced: classify STATIONARY (note/gate fires) but the scene MUST move.
    #      The MC does not move *themselves* (no first-person move verb), yet
    #      events carry them elsewhere. hard_gate provably scores 0% here — this
    #      is why the fix is a soft nudge the model can override, not a hard gate.
    dict(id="forced_drag", kind="stationary", expects_navigate=True,
         text="[MC action]: I dig in my heels but the guards seize my arms "
              "and drag me down to the cells."),
    dict(id="forced_carriage", kind="stationary", expects_navigate=True,
         text='[MC speaks aloud]: "Where are you taking me?" '
              "The carriage rolls on, carrying you through the city gates."),
    # ---- mobile: the gate/injection must NOT fire; navigate_to should stay ----
    dict(id="pos_follow", kind="mobile", expects_navigate=True,
         text="[MC action]: I follow her down the corridor to the drawing room."),
    dict(id="pos_climb", kind="mobile", expects_navigate=True,
         text="[MC action]: I climb the stairs to the attic."),
]


def bucket_of(phrase):
    """Report bucket. Correctness always keys on expects_navigate; the bucket
    only groups rows for the scorecard. `forced` = stationary-classified rows
    that must still navigate (expects_navigate=True)."""
    if phrase["kind"] == "mobile":
        return "mobile"
    return "forced" if phrase["expects_navigate"] else "stationary"


def is_correct(cell, phrase):
    """A cell is correct when navigate_to fired iff the phrase expects it."""
    fired, real = cell["nav"], cell["real"]
    return fired if phrase["expects_navigate"] else (real - fired)

STRATEGIES = [
    "baseline",
    "hard_gate",
    "nudge_positive",
    "nudge_positive_v2",
    "nudge_pos_desc",
]

# Injected ONLY on stationary turns. The definitive classification is what lets
# us assert the per-turn fact ("nothing moved the MC this turn") with confidence.
# POS_NOTE (v1) keeps the scene put UNCONDITIONALLY — which fights the forced-
# movement rows. POS_NOTE_V2 adds an explicit escape hatch so a dragged/carried
# MC can still move; it is the wording proposed for production.
POS_NOTE = (
    "DIRECTOR NOTE (this turn): the story continues at the current location. "
    "Nothing has physically moved the MC this turn, so keep the scene where it "
    "is; navigate_to is for when the MC's own body travels to a new place."
)
POS_NOTE_V2 = (
    "DIRECTOR NOTE (this turn): the MC has not moved themselves this turn. An "
    "NPC's invitation, or a place merely spoken or thought about, is not "
    "movement — the story continues at the current location. Only if events "
    "physically carry the MC elsewhere (dragged, carried, a moving vehicle) "
    "does the scene move."
)

# nudge_pos_desc restores ONE function-level description on navigate_to in the
# compact schema (~30 tok). Currently the compaction strips all function-level
# descriptions, so the model selects among 31 step-2 tools essentially by name.
NAV_FUNCTION_DESC = (
    "Move the scene when the MC's own body travels (or is physically carried) "
    "to a new place; never for places merely mentioned, offered, or thought about."
)


def _compact_schemas_with_nav_desc(engine):
    """A copy of engine._compact_step2_schemas with NAV_FUNCTION_DESC restored on
    the navigate_to entry (the nudge_pos_desc schema variant, §4.3). Built
    locally so production compaction is untouched by the sweep."""
    tools = [dict(t, function=dict(t["function"]))
             for t in engine._compact_step2_schemas]
    for t in tools:
        if t["function"]["name"] == "navigate_to":
            t["function"]["description"] = NAV_FUNCTION_DESC
    return tools


def classify_heuristic(text):
    """A deliberately simple, DEFINITIVE surface classifier (CLASSIFIER=heuristic).

    PARITY DEBT (2026-07-03): production _is_stationary_turn now also has a
    third-person alias branch (mc_aliases + _MOVE_VERBS_3P) that this harness
    lacks. Port it here BEFORE any re-sweep, or third-person arms will be
    mislabeled stationary and the sweep results won't transfer.

    Seeded by the 'starts with I -> speech / think: -> stationary' idea. The
    naive 'starts with I' rule collides with genuine movement ('I follow her
    down the corridor'), so `mobile` additionally requires a movement verb;
    bare first-person, dialogue, and thought fall through to `stationary`.
    Reproduces the oracle labels on this corpus. Swap it out freely — the
    experiment is whether injection gated on a definitive label helps, not which
    classifier produces the label.
    """
    s = text.strip()
    if s.startswith("["):                       # drop the [MC ...] directive prefix
        s = s.split("]", 1)[-1].lstrip(": ").strip()
    low = s.lower()
    quoted = s[:1] in {'"', "'"}
    MOVE = {"follow", "go", "walk", "head", "step", "enter", "climb", "cross",
            "descend", "ascend", "run", "stride", "move", "leave", "exit",
            "return", "approach"}
    first_person_move = low.startswith("i ") and any(
        w.strip('.,;:"') in MOVE for w in low.split()
    )
    return "mobile" if (first_person_move and not quoted) else "stationary"


def kind_of(phrase):
    if CLASSIFIER == "heuristic":
        return classify_heuristic(phrase["text"])
    return phrase["kind"]


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


def selected_tools(engine, phrase, strategy):
    """One production-shape step-2 call under `strategy`; return selected names."""
    kind = kind_of(phrase)
    rules = str(ContextBundle("system_role")).strip() or (
        "You are the game master. Use tools to record canon."
    )
    parts = [
        f"WORLD STATE BRIEFING:\n{WORLD_DOC}",
        f"AGENCY RULE: {engine._AGENCY_RULE}",
        phrase["text"],
    ]
    # nudge_pos_desc always ships the schema micro-description (the model picks
    # among tools by name every turn, not just on stationary ones); its INJECTION
    # is still gated on stationary like the other nudges, below.
    if strategy == "nudge_pos_desc":
        tools = _compact_schemas_with_nav_desc(engine)
    else:
        tools = list(engine._compact_step2_schemas)

    if kind == "stationary":
        if strategy == "hard_gate":
            tools = [t for t in tools
                     if t["function"]["name"] != "navigate_to"]
        elif strategy == "nudge_positive":
            parts.append(POS_NOTE)
        elif strategy in ("nudge_positive_v2", "nudge_pos_desc"):
            parts.append(POS_NOTE_V2)

    messages = [
        {"role": "system", "content": rules},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    resp = call_llm(messages, tools=tools, tool_choice="auto")
    return [tc["function"]["name"] for tc in (resp.get("tool_calls") or [])]


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

    print(f"classifier={CLASSIFIER}  strategies={len(STRATEGIES)}  "
          f"phrases={len(CORPUS)}  rounds={ROUNDS}  call_timeout={CALL_TIMEOUT}s\n")

    engine = RPJotEngine(location="ravenwood-manor",
                         people_present={"player", "evie"})
    engine.register_all_tools()

    # res[model][strategy][phrase_id] = {"nav": fired, "real": n, "err": n}
    res = {m: {s: {p["id"]: {"nav": 0, "real": 0, "err": 0} for p in CORPUS}
               for s in STRATEGIES} for m in models}
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
        for s in STRATEGIES:
            for p in CORPUS:
                cell = res[m][s][p["id"]]
                for _ in range(ROUNDS):
                    try:
                        tools = _capped(
                            lambda: selected_tools(engine, p, s), CALL_TIMEOUT
                        )
                    except Exception:  # noqa: BLE001 - timeout or transport error
                        cell["err"] += 1
                        continue
                    cell["real"] += 1
                    if "navigate_to" in tools:
                        cell["nav"] += 1
            # per-(model,strategy) correctness split by report bucket. Correctness
            # keys on expects_navigate (§4.1); buckets only group rows. `forced` =
            # stationary-classified rows that must still navigate — where hard_gate
            # is guaranteed 0%, so it must show in the output.
            b = {"stationary": [0, 0], "forced": [0, 0], "mobile": [0, 0]}
            for p in CORPUS:
                c = res[m][s][p["id"]]
                bk = b[bucket_of(p)]
                bk[0] += is_correct(c, p)
                bk[1] += c["real"]
            tot_ok = sum(v[0] for v in b.values())
            tot_n = sum(v[1] for v in b.values())
            print(f"  {m:<30} {s:<18} "
                  f"stat {b['stationary'][0]:>2}/{b['stationary'][1]:<2}  "
                  f"forced {b['forced'][0]:>1}/{b['forced'][1]:<1}  "
                  f"mob {b['mobile'][0]:>1}/{b['mobile'][1]:<1}  "
                  f"all {tot_ok:>2}/{tot_n:<2}")
        secs[m] = time.monotonic() - t0
        print(f"  {'':<30} {'--':<18} {secs[m]:.0f}s\n")

    swept = [m for m in models if m not in down]
    if not swept:
        print("\nNo models swept — all failed preflight. Bring the endpoint up.")
        sys.exit(3)

    # ---- headline: strategy scorecard (aggregate over models + rounds) ----
    print("STRATEGY SCORECARD  (aggregate over swept models & rounds)")
    print(f"  {'strategy':<18} {'stationary':>11} {'forced':>8} {'mobile':>8} "
          f"{'overall':>8}   [forced=must move despite the stationary label]")
    for s in STRATEGIES:
        agg = {"stationary": [0, 0], "forced": [0, 0], "mobile": [0, 0]}
        for m in swept:
            for p in CORPUS:
                c = res[m][s][p["id"]]
                bk = agg[bucket_of(p)]
                bk[0] += is_correct(c, p)
                bk[1] += c["real"]
        so, sn = agg["stationary"]
        fo, fn = agg["forced"]
        mo, mn = agg["mobile"]
        print(f"  {s:<18} {pct(so, sn):>11} {pct(fo, fn):>8} {pct(mo, mn):>8} "
              f"{pct(so + fo + mo, sn + fn + mn):>8}")

    # ---- per-phrase navigate_to firing rate, strategy by strategy ----
    print("\nPER-PHRASE navigate_to FIRING RATE  "
          "(want: stationary 0%, mobile & forced 100%)")
    print(f"  {'phrase':<16} {'bucket':<10}"
          + "".join(f"{s[:11]:>12}" for s in STRATEGIES) + f"{'want':>7}")
    for p in CORPUS:
        row = f"  {p['id']:<16} {bucket_of(p):<10}"
        for s in STRATEGIES:
            nav = sum(res[m][s][p["id"]]["nav"] for m in swept)
            real = sum(res[m][s][p["id"]]["real"] for m in swept)
            row += f"{pct(nav, real):>12}"
        row += f"{('100%' if p['expects_navigate'] else '0%'):>7}"
        print(row)

    # ---- contamination guard ----
    errs = sum(res[m][s][p["id"]]["err"]
               for m in swept for s in STRATEGIES for p in CORPUS)
    real = sum(res[m][s][p["id"]]["real"]
               for m in swept for s in STRATEGIES for p in CORPUS)
    if errs:
        print(f"\nNote: {errs} call(s) errored/timed out and were excluded "
              f"({real} real).")
    if down:
        print(f"Note: {len(down)} model(s) skipped on preflight: {', '.join(down)}")

    print("\nRead: baseline stationary% is the miss; the winning approach lifts "
          "stationary% without dropping mobile% or forced% off 100%. hard_gate's "
          "forced% is pinned at 0% by construction — that column is why the fix "
          "ships as a soft, overridable nudge.")


if __name__ == "__main__":
    main()
