# SAVING_STATE — True Resume: restore last location, participants, and scene on `play.py <session>`

## 1. Problem

`python play.py sessions/session_X.jot` currently "resumes" only the story digest
(`seed_digest_from_summaries`, play.py:417) — the engine itself is always
reconstructed hardcoded at `location="ravenwood-manor"`, `people_present={"mc"}`,
and a fresh `begin_scene("opening", ...)` (play.py:667–680). The player snaps back
to the manor exterior, alone, regardless of where the story left off.

Goal: first-class restore — engine comes back up in the **last visited location**,
with the **same participants**, re-entering the **last active scene**, plus mood and
attention (full state). State is **inferred from existing notes** — no new
persistence writes; works on old session files.

**Base branch**: build on `object_permanence` (the open PR). It adds the exact
helpers this feature reuses — `_canonicalize_room` (rpjot.py@PR:1902),
`_ensure_location_node` (1948), `_remark_location` (1438) — and its per-turn
location re-marking makes the events trail precise. Do NOT build on `now_coworked`,
which lacks them.

## 2. Recovery design (three layers, deterministic-first)

1. **Deterministic — location**: every nav note, `record_event`, and yomi note lands
   at `pwd=/story/events/{room}` (PR rpjot.py:2652, 2838, 3507, 3535). The newest
   note under the `PWD_EVENTS` tree → pwd suffix = last precise room. (Same parse
   `_parse_residence` already uses, PR:2024–2025.)
2. **Deterministic — scene**: newest note with a tag word starting `scene:`
   (`TAG_SCENE`), written by `_tool_begin_scene` at `pwd=/story/scenes/{name}`.
3. **LLM inference — people/mood/attention** (not persisted anywhere): one
   `call_llm` pass over the newest `_SEED_SUMMARY_COUNT` (15) summary notes
   (same query as `seed_digest_from_summaries`), returning JSON. The deterministic
   room is given as ground truth; the model may override it only if summaries show
   later movement, and the result is gated through `engine._canonicalize_room(...)`
   — never guess an unknown room. On any failure (LLM error, bad JSON), degrade
   gracefully to layer-1/2 + `{"mc"}` + empty mood/attention.

## 3. Changes — all in `play.py` (new functions next to `seed_digest_from_summaries`), plus tests

### 3.1 `recover_deterministic_state() -> tuple[str | None, str | None]`
Pure catjot queries, no engine needed (runs before engine construction):
- Newest note via `NoteContext(Note.NOTEFILE, (SearchType.TREE, PWD_EVENTS))`,
  `max(..., key=lambda n: n.now)` → location = `note.pwd.removeprefix(PWD_EVENTS + "/")`.
- Newest note via `(SearchType.TREE, PWD_SCENES)` → scene = pwd suffix.
- Either may be `None` (e.g. seed-only file).

### 3.2 `infer_resume_state(engine, det_location) -> dict | None`
- Reuse the summaries query from `seed_digest_from_summaries` (play.py:426–427):
  `ContextBundle(PWD_SUMMARIES)`, newest 15.
- One `call_llm([...], max_tokens=MAX_TOKENS_CONDENSE)` (import from rpjot, mirrors
  `_condense_context`'s try/except style, rpjot.py@PR:2286–2311). Prompt: given
  these turn summaries (newest first) and last recorded room `det_location`, reply
  JSON only: `{"location": str, "people_present": [slugs], "mood": {char: state},
  "attention": {char: focus}}`.
- Parse with `RPJotEngine.extract_json_from_response` (rpjot.py:1556). Any
  exception → return `None` (resume degrades, never aborts).

### 3.3 `apply_resume_state(engine, det_location, det_scene, inferred)`
Mirrors `_remark_location`'s commit block (PR:1474–1477) — reuse, don't reinvent:
- **Location**: `path = engine._canonicalize_room(inferred["location"], det_location or engine.session.location)`
  falling back to `det_location`. If it differs from `session.location`:
  `_ensure_location_node(path)`, set `session.location`,
  `session.location_context = ContextBundle(f"{PWD_WORLD}/{path}")`,
  `engine._cache_drop("social_map")`.
- **People**: `session.people_present = {"mc"} | inferred slugs`; for each, mirror
  `__init__`'s present-marking (rpjot.py:1012–1015) via `npc_tracker`
  register/mark_present at the restored location.
- **Mood / attention**: set `session.mood` / `session.attention` from inferred,
  filtered to keys in `people_present` (drop stale cast).
- **Scene** (re-enter, not re-begin): `session.current_scene = det_scene` —
  deliberately NO new scene-header note (that's what makes it a re-entry;
  `_tool_get_scene` history still works). Set `engine._system_refresh_pending = True`.

### 3.4 `main()` rewiring (play.py:643–684)
```python
if resuming:
    det_loc, det_scene = recover_deterministic_state()
    engine = RPJotEngine(location=det_loc or "ravenwood-manor", people_present={"mc"})
    engine.register_all_tools(); engine.init_pipeline()
    inferred = infer_resume_state(engine, det_loc)
    apply_resume_state(engine, det_loc, det_scene, inferred)
    if det_scene is None:  # seed-only file: fall back to today's behavior
        engine._tool_begin_scene("opening", <existing text>)
    print(f"Resumed: {engine.session.location} — present: {sorted(engine.session.people_present)} — scene: {engine.session.current_scene}")
else:
    <unchanged current path: hardcoded engine + begin_scene("opening")>
game_loop(engine, seed_summaries=resuming)   # digest seeding unchanged
```
Note: keep the existing `engine._system_refresh_pending = False` only on the fresh
path; on resume the first turn must rebuild the system doc for the restored
scene/location.

## 4. Tests (`test_rpjot.py`, next to `TestResumeDigestSeeding` ~2515; reuse its tempfile pattern and `_make_engine` helper at :42)

New class `TestTrueResume`:
1. `recover_deterministic_state`: temp .jot with nav notes at
   `/story/events/manor/kitchen` (increasing `now`) + two `scene:` notes → returns
   newest room + newest scene; empty/seed-only file → `(None, None)`.
2. `infer_resume_state`: mock `call_llm` returning JSON (and returning garbage /
   raising) → parsed dict / `None`.
3. `apply_resume_state`: engine + mocked inference → asserts `session.location`
   canonicalized (known room accepted, unknown room falls back to det),
   `location_context` re-pointed, `people_present` includes inferred cast,
   mood/attention filtered to present cast, `current_scene` set with **no new
   scene note appended** (assert note count under `/story/scenes` unchanged),
   `_system_refresh_pending` True.
4. End-to-end resume unit: build a temp session file with a short fake history
   (nav → events → scene → summaries), run the three functions in main()'s order,
   assert final engine state.

## 5. Verification
1. `pytest test_rpjot.py -k TrueResume` (plus full `pytest test_rpjot.py -x` for regressions).
2. Live smoke (needs the :5000 endpoint — **preflight it first**; a dead endpoint
   silently xfails LLM tests): start a new game, play 2–3 turns including a
   `navigate_to` and an NPC arrival, `/quit`; relaunch
   `python play.py sessions/session_<stamp>.jot`; confirm the `Resumed:` banner
   shows the last room + cast, then `/location`, `/people`, `/stats` agree.
