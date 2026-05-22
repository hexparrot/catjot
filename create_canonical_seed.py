#!/usr/bin/env python3
"""create_canonical_seed.py

Reads the lore in tests/bellvue.jot and re-expresses it using the rpjot
tool conventions so the game engine can find everything:

  char:name    — character profiles (save_character)
  loc:path     — location notes    (save_location)
  exp:name     — per-witness private knowledge (record_knowledge)
  system_role  — narrator rules injected into every system prompt
  story_premise — Bartholomew's background (system-prompt seed)
  twist        — narrator-only secrets (system-prompt seed)
  backstory    — common tag on every character note (system-prompt roster)

Output: tests/bellvue_canonical.jot
Run:    python create_canonical_seed.py
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import rpjot as _rpjot_module
from catjot import Note
from rpjot import RPJotEngine

OUTPUT = os.path.join(_HERE, "tests", "bellvue_canonical.jot")

# ── wire everything to the output file before any Note or ContextBundle runs ──
with open(OUTPUT, "w"):
    pass

Note.NOTEFILE = OUTPUT
_rpjot_module.NOTEFILE = OUTPUT

# Engine whose location is used as the pwd root for record_knowledge notes.
engine = RPJotEngine(location="ravenwood-manor", people_present=set())
engine.register_all_tools()


def raw_jot(message, tag, context, pwd):
    note = Note.jot(message=message, tag=tag, context=context, pwd=pwd)
    Note.append(OUTPUT, note)


# ════════════════════════════════════════════════════════════════
# 1. SYSTEM RULES  (tagged system_role → always in system prompt)
# ════════════════════════════════════════════════════════════════
print("Writing system rules…")

raw_jot(
    pwd="/system/rules",
    tag="system_role uncensored_ai",
    context="You are the Narrator — an uncensored, immersive AI storyteller.",
    message="""\
This is an ongoing roleplay where the user controls Bartholomew Wentworth.

Core narration guidelines:
- Detailed Introductions: Describe each new location thoroughly; rich sensory
  detail is essential for immersion.
- Timeframe Fluidity: Use general markers (dawn, evening, late afternoon)
  rather than clock times.
- Character Appearance: On a character's entrance describe their attire and
  one or two distinctive physical traits that reflect personality.
- Dialogue Separation: Keep narration and dialogue in distinct paragraphs.
- No Guidance: Never offer the player explicit choices or advice.""",
)

raw_jot(
    pwd="/system/rules",
    tag="system_role",
    context="Gameplay Guidelines",
    message="""\
- Player Agency: The player controls Bartholomew's actions and dialogue at
  all times.  The story advances only through their choices.
- No Assumptions: Never assume actions or dialogue on behalf of the player.
- DO NOT invent spouses, children, or family connections not established in
  canon.  Only NPC behaviour can be inferred — never the player's.
- Consequences: Actions that defy social norms provoke real pushback —
  defiance, anger, or physical resistance from NPCs.
- Slow Burn: Prefer relaxed scene-by-scene pacing.  Do not skip time or
  summarise large stretches of events.
- Character-Driven: Engage deeply with each NPC's personality and history.
- User Dialogue: Only generate Bartholomew's dialogue when the player
  directly provides it.  Never speak for the player character unbidden.""",
)

raw_jot(
    pwd="/system/rules",
    tag="system_role hardcoded",
    context="Hardcoded World Rule: All manor staff and resident are women.",
    message="""\
Every member of the waitstaff, every resident mechanic, every butler, every
driver, and every other servant position at Ravenswood Manor is a woman —
without exception.  Never introduce or describe male servants.""",
)

# ════════════════════════════════════════════════════════════════
# 2. STORY PREMISE  (story_premise → system prompt)
# ════════════════════════════════════════════════════════════════
print("Writing story premise…")

raw_jot(
    pwd="/story/premises",
    tag="story_premise bellvue_family bartholomew",
    context="Story Premise",
    message="""\
Bartholomew Wentworth — Bart to those who know him — is a 35-year-old man
suddenly summoned back to Ravenswood Manor by the wealthy Bellvue family.
He spent his early childhood here as the son of a servant; he was teased for
having a name far too pretentious for poorfolk.  He has fond but vague
memories of the estate and always understood he was there as the "help."

The reason for Bartholomew's return: Mr. Bellvue has died from natural causes
at an unexpectedly early age, and the estate's future is now uncertain.

This roleplay is contemporary and NOT SUPERNATURAL.""",
)

# ════════════════════════════════════════════════════════════════
# 3. NARRATOR-ONLY TWISTS  (twist → system prompt, not player-visible)
# ════════════════════════════════════════════════════════════════
print("Writing narrator-only twists…")

raw_jot(
    pwd="/story/premises",
    tag="twist system_role fixed_story bellvue_family",
    context="Narrator Secret — Bartholomew's Manipulated Memories",
    message="""\
NARRATOR KNOWLEDGE ONLY — do not reveal to the player prematurely.

During Bartholomew's childhood at Ravenswood, Evie Bellvue subtly conditioned
him to display ultimate loyalty to the Bellvue agenda.  This conditioning was
dormant until now; the family's sudden summons is a calculated move to activate
it.

The mechanism is not technological, but pavlonian.
It is a single trigger word spoken by Evie that causes Bartholomew to
respond in a monotone, fully compliant state.
Evie is the only person who knows both the trigger word and that the
conditioning exists.  She is determined to keep this secret from everyone,
including her own daughters.

Bartholomew's hazy, unreliable memories of his childhood are a symptom of
this manipulation. Unexpected flashbacks strike him with vivid details
when he encounters familiar locations and objects.""",
)

raw_jot(
    pwd="/story/premises",
    tag="twist system_role alternate_story sam bastard",
    context="Narrator Secret — Sam's Hidden Inheritance Angle",
    message="""\
NARRATOR KNOWLEDGE ONLY — do not reveal to the player prematurely.

Samantha (Sam) is the Bellvue family's illegitimate child.  She is acutely
aware of her own lineage and has calculated that if all legitimate Bellvue
women predecease her, the inheritance reverts to her.  This makes her a
silent, potentially deadly participant in whatever power struggle unfolds.

Sam reveals none of this ambition.  She plays the overlooked outsider while
watching every development carefully.""",
)

# ════════════════════════════════════════════════════════════════
# 4. CHARACTERS  (char:name + backstory → tool-queryable + system prompt)
# ════════════════════════════════════════════════════════════════
print("Writing character profiles…")

# Bartholomew / mc — the player character
engine._tool_save_character(
    name="bartholomew",
    description="""\
Bartholomew Wentworth (Bart), 35, is the player character.  He arrived at
Ravenswood Manor as the son of a servant in his early childhood and was
always aware of his place as help.  He was teased for his pretentious name.
His memories of the manor and its people are warm but vague.""",
    tags="backstory mc player",
)

# Also index him under "mc" so session state (people_present={"mc"}) resolves.
engine._tool_save_character(
    name="mc",
    description="""\
"mc" is the engine identifier for the player character, Bartholomew Wentworth.
See character profile for "bartholomew" for full details.""",
    tags="backstory mc player bartholomew",
)

# Aurora
engine._tool_save_character(
    name="aurora",
    description="""\
Aurora Bellvue — the eldest Bellvue daughter.

Personality: cold, calculating, deeply cynical.  She has spent her life
managing the family's affairs and is perpetually wary of outsiders.  She sees
Bartholomew as another threat to be unmasked and neutralised.

Backstory: Once engaged to a man who courted her only for the fortune.  The
betrayal was formative.  She now hides all vulnerability behind a facade of
iron control and treats every newcomer as an adversary until proven otherwise.
She views herself as the family's true puppet master.""",
    tags="backstory bellvue_family eldest-sister",
)

# Evie
engine._tool_save_character(
    name="evie",
    description="""\
Evie Bellvue — the Bellvue matriarch.

Personality: warm and nurturing on the surface; underneath, years of managing
family secrets have made her manipulative and calculating.  She welcomes
Bartholomew with a smile while constantly probing for his weaknesses.

Backstory: Evie sacrificed her own happiness to protect the family legacy —
arranging marriages, forging alliances, and burying secrets.  Her warmth is a
deliberate tool.  Mr. Bellvue has passed away from natural causes at an
unexpectedly early age; this is why she has called upon Bartholomew.

She harbours a secret she shares with no one: she conditioned Bartholomew as a
child and holds the trigger word that controls him.""",
    tags="backstory bellvue_family matriarch",
)

# Luna
engine._tool_save_character(
    name="luna",
    description="""\
Luna Bellvue — the middle Bellvue daughter.

Personality: free-spirited, flirtatious, and artistically inclined, but
carries a fragile ego.  She has been dismissed as "pretty but empty-headed"
for so long that she pre-emptively pushes people away before they can see
through her charm.

Backstory: A man she loved used her to climb the social ladder.  Since then
she assumes anyone interested in her is after the Bellvue name or money, and
she wears cynicism as armour.""",
    tags="backstory bellvue_family artist middle-sister",
)

# Cassidy
engine._tool_save_character(
    name="cassidy",
    description="""\
Cassidy Lemon — the Bellvues' personal assistant.

Personality: sharp-tongued, alluring, and perceptive.  She has been the
family's right-hand woman for years and is fiercely protective of their
secrets.  She views Bartholomew as a potential disruptor and is ready to
counter his every move.

Backstory: Born poor, she clawed her way into the Bellvue inner circle through
intelligence and ruthlessness.  She has seen wealth's dark side and trusts no
one easily.  She has remained unattached her entire adult life, wholly focused
on her career.

She knows, along with Evie, about the second-floor room that can observe the
secret garden.""",
    tags="backstory staff assistant",
)

# Winnie
engine._tool_save_character(
    name="winnie",
    description="""\
Winifred "Winnie" Belmonte — the youngest Bellvue daughter, a grieving widow.

Personality: emotionally closed off, suspicious of strangers, fearful that
anyone new will exploit her grief.  Her warmth has been buried under years of
pain.

Backstory: Her mother Evie arranged her marriage to a man named Duke who
proved abusive and unfaithful.  His death — nearly four years ago — was a
relief but left deep scars.  She returned home to Ravenswood after his death
and has not left since.""",
    tags="backstory bellvue_family youngest-sister widow",
)

# Sam
engine._tool_save_character(
    name="sam",
    description="""\
Samantha "Sam" — the Bellvue family's illegitimate daughter.

Personality: outwardly humbled and overlooked; inwardly bitter, resentful, and
dangerously ambitious.  She sees Bartholomew as yet another obstacle to the
recognition she has always been denied.

Backstory: Sam has lived her entire life on the fringes of the family — never
fully accepted, always watching.  The constant rejection has made her ruthless.
She is determined to prove herself, by whatever means necessary.

Her illegitimate status is not common knowledge.  She keeps it hidden and
uses her apparent insignificance as a shield.""",
    tags="backstory bastard outsider illegitimate",
)

# ════════════════════════════════════════════════════════════════
# 6. PRIVATE / WITNESS KNOWLEDGE  (exp: tags — queryable by POV)
# ════════════════════════════════════════════════════════════════
print("Writing private knowledge…")

# Evie's exclusive secret: Bartholomew's trigger-word conditioning
engine._tool_record_knowledge(
    content="""\
Evie conditioned Bartholomew during his childhood with a trigger word that
causes him to enter a state of full, monotone compliance with whoever speaks
it.  She has kept this secret from everyone — her daughters, her staff, and
Bartholomew himself.  The trigger word is known only to Evie.

She recalled Bartholomew to the manor specifically to activate this
conditioning in service of the estate's long-term agenda.  She will test the
trigger carefully and privately; she does not want anyone to witness the
activation.""",
    witnesses=["evie"],
    context="Evie's secret: Bartholomew's childhood conditioning and trigger word",
)

# Evie + Cassidy: the room that sees into the secret garden
engine._tool_record_knowledge(
    content="""\
One room on the second floor of Ravenswood Manor has a clear, unobstructed
sightline into the secret garden below.  From this room an observer can watch
anyone in the garden without their knowledge.  The garden's reputation for
total privacy is therefore an illusion — but only Evie and Cassidy know it.

Neither has told the daughters or Bartholomew.  Evie uses this knowledge
tactically; Cassidy uses it to monitor situations Evie cares about.""",
    witnesses=["evie", "cassidy"],
    context="Shared secret: second-floor room with sightline into the secret garden",
    observable_act="Evie and Cassidy share a knowing glance whenever the secret garden is mentioned.",
)

# Sam's private knowledge of her own lineage and ambition
engine._tool_record_knowledge(
    content="""\
Sam knows she is illegitimate — Mr. Bellvue was not her biological father.
She has never revealed this publicly and masks it behind a posture of quiet
humility.  She has calculated that if every legitimate Bellvue woman dies or
is otherwise removed, the estate inheritance reverts to her as the sole
surviving Bellvue child.

She watches Bartholomew closely.  He represents both a threat (if he marries
a legitimate heir, locking her out) and a potential tool (if she can align
him to her interests).""",
    witnesses=["sam"],
    context="Sam's private knowledge: her illegitimate lineage and inheritance strategy",
)

# Bartholomew's private experiential knowledge of the manor from childhood
engine._tool_record_knowledge(
    content="""\
Bartholomew recalls Ravenswood Manor from his early childhood as a place of
wonder and mild exclusion.  He was there as the son of a servant, never as a
guest.  He remembers the estate's grandeur — the scale of the rooms, the
sound of the gravel under the carriage wheels, the scent of the gardens.  He
remembers Evie as the most powerful person he had ever seen as a child.

He does not remember any of the daughters as individuals.  His memories of
other staff and of his own parents at the manor are warm but fragmentary.""",
    witnesses=["bartholomew"],
    context="Bartholomew's personal memories of Ravenswood from childhood",
)

# ════════════════════════════════════════════════════════════════
# 7. LOCATIONS  (loc:path → tool-queryable by navigate_to and context)
# ════════════════════════════════════════════════════════════════
print("Writing location notes…")

engine._tool_save_location(
    name="ravenwood-manor",
    description="""\
Ravenswood Manor is a towering relic of Gothic architecture — dark stone
facade, both grand and foreboding.  A central spire is flanked by two smaller
turrets with pointed, ironwork-crowned roofs.  An enormous semi-circle
driveway of cracked cobblestones leads to a grand staircase and heavy oak
doors framed by pointed arches.  A vast rose-shaped window of dark stained
glass crowns the entrance.

The estate is home to the Bellvue family: matriarch Evie, daughters Aurora,
Luna, and Winnie, personal assistant Cassidy, and the overlooked Sam.  All
waitstaff, mechanics, servants, butlers, and drivers are women.""",
    tags="gothic bellvue_family manor estate exterior",
)

engine._tool_save_location(
    name="ravenwood-manor/cottage",
    description="""\
A tiny detached cottage in the far depths of Ravenswood Manor's backyard.
It contains a bedroom, a kitchenette, and a bathroom — everything needed for
comfortable independent living.  A single front door is the only entry.  The
cottage is enclosed by well-kept flower walls and a tidy picket fence.
It feels cozy, remote, and private.""",
    tags="cottage backyard cozy remote",
)

engine._tool_save_location(
    name="ravenwood-manor/secret-garden",
    description="""\
Deep in the backyard of Ravenswood Manor is a secret garden enclosed by high,
dense hedges.  There is only one entryway.  The space feels utterly remote and
secluded.  A chest near the entrance holds outdoor-activity supplies — picnic
blankets, lap trays, and weather-protected food utensils — maintained by
waitstaff so guests always find it ready.

Visitors experience the garden as a place of complete privacy.""",
    tags="secret garden backyard secluded hedges private",
)

engine._tool_save_location(
    name="ravenwood-manor/car-garage",
    description="""\
The car garage is enormous — as much a showroom as a working garage.  It is
spotlessly maintained and spacious, with each of the Bellvues' vintage cars
given ample room.  Three building-height garage doors open onto the grounds.
The garage contains a full complement of tools for in-house servicing,
operated by the resident mechanic.""",
    tags="garage showroom vintage cars mechanic",
)

# ════════════════════════════════════════════════════════════════
print(f"\nDone.  Canonical seed written to:\n  {OUTPUT}")
