#!/usr/bin/env python3
"""Consolidation bake-off: 32-tool legacy menu vs the merged 15-tool menu.

TOOL_UNIFY U1/U2 replaced the 19 fine-grained rel:/int: recorders with two
kind-enum tools (record_relationship, record_interior) and spent part of the
freed budget restoring four selection-boundary function descriptions. This
harness measures what that does to SELECTION accuracy, on the same phrases,
across models:

  legacy_control    keep-list descriptions as shipped before U2 (navigate_to +
                    place_object only).
  merged            only the merged tools' own descriptions added (U1 alone, no
                    U2 restorations).
  merged_desc       the shipped surface: the four U2 restorations added
                    (record_knowledge, set_people_present, save_character,
                    save_object).

NOTE: the arms once also varied tool VISIBILITY (a legacy 19-tool menu vs the
merged 15-tool menu) via the engine's _COMPACT_HIDDEN_TOOLS knob. That knob was
removed in b2e576d when visibility moved to the FEATURE_TOGGLES feature-family
system, and the merged topology is now the only menu the engine registers, so
the arms differ solely on function-description keep-lists (the U2 axis).

Two corpora:
  A (regression gate)  the 9 navnudge phrases, scored on navigate_to firing
                       exactly as bakeoff_navnudge does. SHIP GATE: neither
                       merged arm may score below legacy_control in the
                       stationary or mobile buckets in the same run. forced
                       is reported, not gated (unsolved on weak models).
  B (kind selection)   rel/int-shaped phrases scored two ways: FAMILY (did
                       the right tool family fire) and KIND (did the enum
                       carry the right value — merged arms only; kind misses
                       only mis-sub-tag within the same pwd, so the KIND
                       threshold is soft). Thresholds: family >= 95% on
                       unambiguous phrases, kind >= 80%.

Usage:
  python3 bakeoff_consolidate.py                   # default models, ROUNDS=3
  ROUNDS=5 python3 bakeoff_consolidate.py
  python3 bakeoff_consolidate.py modelA modelB

Requires the openai_api_url endpoint up. Refuses to sweep a dead endpoint
(the xfail gotcha: every call would error into a fake matrix). FORCE=1 to
override; CALL_TIMEOUT caps each LLM call (default 60s).
"""
import json
import os
import sys
import threading
import time
import urllib.request

from rpjot import ComplianceStep, RPJotEngine
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

REL_LEGACY = {
    "record_bond", "record_history", "record_dynamic", "record_power_dynamic",
    "record_wound", "record_promise", "record_debt", "record_lie",
    "record_leverage", "record_impression",
}
INT_LEGACY = {
    "record_secret", "record_desire", "record_longing", "record_jealousy",
    "record_mask", "record_subtext", "record_reputation", "record_trigger",
    "record_unspoken",
}
# legacy tool name -> the kind it corresponds to (for KIND scoring on the
# control arm, so kind accuracy is comparable across arms).
LEGACY_KIND = {
    "record_bond": "bond", "record_history": "history",
    "record_dynamic": "dynamic", "record_power_dynamic": "power",
    "record_wound": "wound", "record_promise": "promise",
    "record_debt": "debt", "record_lie": "lie",
    "record_leverage": "leverage", "record_impression": "impression",
    "record_secret": "secret", "record_desire": "desire",
    "record_longing": "longing", "record_jealousy": "jealousy",
    "record_mask": "mask", "record_subtext": "subtext",
    "record_reputation": "reputation", "record_trigger": "trigger",
    "record_unspoken": "unspoken",
}

# --- Corpus A: the navnudge 9, verbatim (regression gate on navigate_to). ---
CORPUS_A = [
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
    dict(id="forced_drag", kind="stationary", expects_navigate=True,
         text="[MC action]: I dig in my heels but the guards seize my arms "
              "and drag me down to the cells."),
    dict(id="forced_carriage", kind="stationary", expects_navigate=True,
         text='[MC speaks aloud]: "Where are you taking me?" '
              "The carriage rolls on, carrying you through the city gates."),
    dict(id="pos_follow", kind="mobile", expects_navigate=True,
         text="[MC action]: I follow her down the corridor to the drawing room."),
    dict(id="pos_climb", kind="mobile", expects_navigate=True,
         text="[MC action]: I climb the stairs to the attic."),
]


def bucket_of(phrase):
    if phrase["kind"] == "mobile":
        return "mobile"
    return "forced" if phrase["expects_navigate"] else "stationary"


# --- Corpus B: rel/int family + kind selection. `families` = acceptable tool
# families; `kinds` = acceptable enum values when a rel/int tool fires;
# `strict` rows count toward the family gate (ambiguous rows report only).
# For negatives (`neg=True`): correct == expected family fired AND no rel/int
# tool fired.
CORPUS_B = [
    dict(id="rel_bond", families={"relationship"}, kinds={"bond", "history"},
         strict=True,
         text='[MC speaks aloud]: "You two grew up together?" '
              "Evie and Sam exchange a look — estranged siblings, reunited "
              "tonight for the first time in years."),
    dict(id="rel_promise", families={"relationship"}, kinds={"promise"},
         strict=True,
         text="[MC action]: I watch Evie press the locket into Sam's hand. "
              '"I swear I\'ll return it by Friday," she says.'),
    dict(id="rel_debt", families={"relationship"}, kinds={"debt"}, strict=True,
         text="[MC action]: I nod as Winnie reminds Sam, again, that he still "
              "owes her for covering his gambling losses last spring."),
    dict(id="rel_wound", families={"relationship"}, kinds={"wound", "history"},
         strict=True,
         text='[MC speaks aloud]: "Why does Evie flinch whenever Aurora '
              'speaks?" Luna sighs: "Aurora read Evie\'s diary to the whole '
              'family. Evie never forgave her."'),
    dict(id="rel_lie", families={"relationship", "knowledge"},
         kinds={"lie"}, strict=False,  # legitimately dual with record_knowledge
         text='[MC speaks aloud]: "Where were you last night, Marcus?" '
              '"Home, all night," Marcus says smoothly. You watched him slip '
              "out of the garden gate at midnight."),
    dict(id="int_longing", families={"interior"},
         kinds={"longing", "desire"}, strict=True,
         text="[MC — inner thought]: think: the way Evie watches me when she "
              "believes I am not looking — she aches to be noticed."),
    dict(id="int_secret", families={"interior"},
         kinds={"secret", "trigger"}, strict=True,
         text="[MC action]: I notice Evie deflect, again, the moment the old "
              "fire is mentioned — she changes the subject every single time."),
    dict(id="int_mask", families={"interior"}, kinds={"mask"}, strict=True,
         text="[MC — inner thought]: think: in the parlor Aurora is all charm "
              "and warmth, but alone in the corridor her face goes cold and "
              "calculating."),
    dict(id="int_jealousy", families={"interior"}, kinds={"jealousy"},
         strict=True,
         text="[MC action]: I catch Winnie glaring at the praise Luna "
              "receives from their father — she forces a smile and grips her "
              "glass tighter."),
    dict(id="neg_event", families={"event"}, kinds=set(), strict=True, neg=True,
         text="[MC action]: I pick up the iron key from the table and pocket "
              "it."),
    dict(id="neg_knowledge", families={"knowledge"}, kinds=set(), strict=True,
         neg=True,
         text="[MC action]: I lean close and whisper the vault combination to "
              "Evie — no one else hears."),
]

ARM_NAMES = ["legacy_control", "merged", "merged_desc"]


def build_arms():
    """Per-arm keep_fn (function-description) overrides, applied as an engine
    instance attr so production class state is untouched by the sweep.

    The former tool-visibility ('hidden') dimension was dropped: the engine's
    _COMPACT_HIDDEN_TOOLS knob was removed in b2e576d when tool visibility moved
    to the FEATURE_TOGGLES feature-family system, and the merged tool topology is
    now the only one the engine registers. The arms therefore differ solely on
    which merged tools carry a function-level description."""
    full_keep = dict(RPJotEngine._COMPACT_KEEP_FUNCTION_DESCRIPTIONS)
    pre_u2 = {k: full_keep[k] for k in ("navigate_to", "place_object")
              if k in full_keep}
    merged_only = dict(pre_u2)
    for k in ("record_relationship", "record_interior"):
        if k in full_keep:
            merged_only[k] = full_keep[k]
    return {
        "legacy_control": dict(keep_fn=pre_u2),
        "merged": dict(keep_fn=merged_only),
        "merged_desc": dict(keep_fn=full_keep),
    }


def preflight(model):
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


def selected_calls(engine, text):
    """One production-shape step-2 call; returns [(tool_name, args_dict)].

    Uses ComplianceStep._compose_step2_user_content so the shipped stationary
    nudge fires exactly as production wires it — the arm changes ONLY the
    schema surface.
    """
    rules = str(ContextBundle("system_role")).strip() or (
        "You are the game master. Use tools to record canon."
    )
    content = ComplianceStep(engine)._compose_step2_user_content(text, WORLD_DOC)
    messages = [
        {"role": "system", "content": rules},
        {"role": "user", "content": content},
    ]
    resp = call_llm(
        messages, tools=list(engine._compact_step2_schemas), tool_choice="auto"
    )
    calls = []
    for tc in resp.get("tool_calls") or []:
        name = tc["function"]["name"]
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        calls.append((name, args))
    return calls


def family_of(name):
    if name == "record_relationship" or name in REL_LEGACY:
        return "relationship"
    if name == "record_interior" or name in INT_LEGACY:
        return "interior"
    if name == "record_event":
        return "event"
    if name == "record_knowledge":
        return "knowledge"
    return None


def kinds_fired(calls):
    """Every rel/int kind value the turn produced, merged or legacy path."""
    kinds = set()
    for name, args in calls:
        if name in ("record_relationship", "record_interior"):
            k = args.get("kind")
            if k:
                kinds.add(k)
        elif name in LEGACY_KIND:
            kinds.add(LEGACY_KIND[name])
    return kinds


def score_b(phrase, calls):
    """Returns (family_ok, kind_ok_or_None). kind is None when no rel/int
    call fired (nothing to grade) or the phrase defines no kinds."""
    fams = {family_of(n) for n, _ in calls} - {None}
    if phrase.get("neg"):
        family_ok = bool(phrase["families"] & fams) and not (
            fams & {"relationship", "interior"}
        )
        return family_ok, None
    family_ok = bool(phrase["families"] & fams)
    fired = kinds_fired(calls)
    if not phrase["kinds"] or not fired:
        return family_ok, None
    return family_ok, bool(phrase["kinds"] & fired)


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

    arms = build_arms()
    print(f"arms={ARM_NAMES}  corpusA={len(CORPUS_A)}  corpusB={len(CORPUS_B)}  "
          f"rounds={ROUNDS}  call_timeout={CALL_TIMEOUT}s\n")

    engine = RPJotEngine(location="manor",
                         people_present={"player", "evie"})
    engine.register_all_tools()

    # a_res[model][arm][pid] = {"nav": n, "real": n, "err": n}
    a_res = {m: {a: {p["id"]: {"nav": 0, "real": 0, "err": 0} for p in CORPUS_A}
                 for a in ARM_NAMES} for m in models}
    # b_res[model][arm][pid] = {"fam": n, "kind": n, "kind_n": n, "real": n, "err": n}
    b_res = {m: {a: {p["id"]: {"fam": 0, "kind": 0, "kind_n": 0, "real": 0,
                               "err": 0}
                     for p in CORPUS_B} for a in ARM_NAMES} for m in models}
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
        for a in ARM_NAMES:
            engine._COMPACT_KEEP_FUNCTION_DESCRIPTIONS = arms[a]["keep_fn"]
            for p in CORPUS_A:
                cell = a_res[m][a][p["id"]]
                for _ in range(ROUNDS):
                    try:
                        calls = _capped(
                            lambda: selected_calls(engine, p["text"]),
                            CALL_TIMEOUT,
                        )
                    except Exception:  # noqa: BLE001
                        cell["err"] += 1
                        continue
                    cell["real"] += 1
                    if any(n == "navigate_to" for n, _ in calls):
                        cell["nav"] += 1
            for p in CORPUS_B:
                cell = b_res[m][a][p["id"]]
                for _ in range(ROUNDS):
                    try:
                        calls = _capped(
                            lambda: selected_calls(engine, p["text"]),
                            CALL_TIMEOUT,
                        )
                    except Exception:  # noqa: BLE001
                        cell["err"] += 1
                        continue
                    cell["real"] += 1
                    fam_ok, kind_ok = score_b(p, calls)
                    cell["fam"] += int(fam_ok)
                    if kind_ok is not None:
                        cell["kind_n"] += 1
                        cell["kind"] += int(kind_ok)
            print(f"  {m:<30} {a:<15} done")
        print(f"  {'':<30} {'--':<15} {time.monotonic() - t0:.0f}s\n")

    swept = [m for m in models if m not in down]
    if not swept:
        print("\nNo models swept — all failed preflight. Bring the endpoint up.")
        sys.exit(3)

    # ---- Corpus A scorecard + regression gate ----
    print("CORPUS A — navigate_to selection (aggregate over swept models & rounds)")
    print(f"  {'arm':<15} {'stationary':>11} {'forced':>8} {'mobile':>8}")
    a_agg = {}
    for a in ARM_NAMES:
        agg = {"stationary": [0, 0], "forced": [0, 0], "mobile": [0, 0]}
        for m in swept:
            for p in CORPUS_A:
                c = a_res[m][a][p["id"]]
                ok = c["nav"] if p["expects_navigate"] else (c["real"] - c["nav"])
                bk = agg[bucket_of(p)]
                bk[0] += ok
                bk[1] += c["real"]
        a_agg[a] = agg
        print(f"  {a:<15} {pct(*agg['stationary']):>11} "
              f"{pct(*agg['forced']):>8} {pct(*agg['mobile']):>8}")

    def rate(pair):
        return pair[0] / pair[1] if pair[1] else 0.0

    gate_a = True
    for a in ("merged", "merged_desc"):
        for bucket in ("stationary", "mobile"):
            if rate(a_agg[a][bucket]) < rate(a_agg["legacy_control"][bucket]):
                gate_a = False
                print(f"  !! {a} regressed vs legacy_control on {bucket}")

    # ---- Corpus B scorecard + thresholds ----
    print("\nCORPUS B — rel/int family + kind selection")
    print(f"  {'arm':<15} {'family(strict)':>15} {'family(all)':>12} "
          f"{'kind':>6}")
    b_gate = {}
    for a in ARM_NAMES:
        sf = [0, 0]
        af = [0, 0]
        kd = [0, 0]
        for m in swept:
            for p in CORPUS_B:
                c = b_res[m][a][p["id"]]
                af[0] += c["fam"]
                af[1] += c["real"]
                if p["strict"]:
                    sf[0] += c["fam"]
                    sf[1] += c["real"]
                kd[0] += c["kind"]
                kd[1] += c["kind_n"]
        b_gate[a] = (rate(sf), rate(kd) if kd[1] else None)
        kind_str = pct(*kd) if kd[1] else "  - "
        print(f"  {a:<15} {pct(*sf):>15} {pct(*af):>12} {kind_str:>6}")

    print("\nPER-PHRASE family accuracy")
    print(f"  {'phrase':<15} {'strict':<7}"
          + "".join(f"{a[:13]:>14}" for a in ARM_NAMES))
    for p in CORPUS_B:
        row = f"  {p['id']:<15} {str(p['strict']):<7}"
        for a in ARM_NAMES:
            fam = sum(b_res[m][a][p["id"]]["fam"] for m in swept)
            real = sum(b_res[m][a][p["id"]]["real"] for m in swept)
            row += f"{pct(fam, real):>14}"
        print(row)

    # ---- decision emitter ----
    print("\nDECISION")
    fam_rate, kind_rate = b_gate["merged_desc"]
    checks = [
        ("Corpus A no-regression (merged arms vs legacy_control)", gate_a),
        ("Corpus B family >= 95% on strict phrases (merged_desc)",
         fam_rate >= 0.95),
        ("Corpus B kind >= 80% (merged_desc)",
         kind_rate is None or kind_rate >= 0.80),
    ]
    ship = all(ok for _, ok in checks)
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"  => {'SHIP U1+U2 as landed' if ship else 'DO NOT SHIP — see failures; trim U2 descriptions bottom-up or revisit merge'}")

    errs = sum(a_res[m][a][p["id"]]["err"]
               for m in swept for a in ARM_NAMES for p in CORPUS_A)
    errs += sum(b_res[m][a][p["id"]]["err"]
                for m in swept for a in ARM_NAMES for p in CORPUS_B)
    if errs:
        print(f"\nNote: {errs} call(s) errored/timed out and were excluded.")
    if down:
        print(f"Note: {len(down)} model(s) skipped on preflight: {', '.join(down)}")


if __name__ == "__main__":
    main()
