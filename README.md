# MindForm World

**A living 3D island where MindForm residents are raised by what happens to them.**

You pick a MindForm version, create the residents with that version's own creation form, and
drop them on Halcyon Isle. The island runs a day: work shifts, breakfasts, storms, the ferry,
letters from home, a town meeting about a new pier, a stranger asking questions. Every
stretch of island time becomes an experience that MindForm reads, and each resident forms
from it: traits, values, needs, self-image, voice and stance. They talk to each other in
the voice MindForm has formed for them. You watch, rewind the logs, and run it again with
the same cast.

```
           ┌────────────── the world (this repo) ──────────────┐         ┌──── the mind (MindForm) ────┐
 director ─► events ─► planner ─► what each resident does ─► narrator ─► "I said to Rex: ..."  ──► forms them,
 (weather, ferry,       (LLM or rules, reads the        (facts only, first      (one experience     answers in
  letters, incidents,    resident's MindForm state)      person, no feelings)    per resident)      their voice
  god mode)                          ▲                                                                  │
                                     └──────────── needs, stance, traits, relationships ◄───────────────┘
```

**The rule that keeps it an experiment: the world writes facts, MindForm writes feelings.** The
narrator may say *"Rex walked off toward the dock while I was talking"*, never *"I felt
rejected"*. If the narrator interpreted events, you'd be measuring the narrator, not MindForm.

## Run it (Mac)

Put this repo next to your MindForm v0 checkout:

```
~/PycharmProjects/
  mindform_v0/        # your private MindForm repo (with its own .venv and .env)
  mindform-world/     # this repo
```

```bash
cd mindform-world
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                      # http://127.0.0.1:8090
```

- **No MindForm code is copied in.** For each world, MindForm World starts MindForm v0's own
  cockpit server (`console.py`) as a separate process. It runs inside the world's own folder,
  so every world has its own roster, memories and logs. Your console characters are never touched.
- **v0 runs on v0's own Python** (`mindform_v0/.venv` if it exists) with v0's own `.env`.
- **A different location?** Copy `.env.example` to `.env` and set `MINDFORM_V0_PATH`.
- **The world's LLM** (planner, narrator, director) reuses the API key from MindForm v0's
  `.env`. You can also set a separate key in this repo's `.env`.
- **Three.js loads from the jsDelivr CDN**, so the browser needs internet access. The GPU only
  renders the island.
- **The page looks like the old demo, or the terminal says a tab is running an old cached
  page?** Your browser kept the previous version. That tab now shows "upgraded: reload this
  page". Reload it once (Cmd+Shift+R). The new version is never cached, and an open tab reloads
  itself when the server is upgraded.

## Making a world

1. **Mind**: choose the MindForm version. Future versions show up here once someone adds an
   adapter. *Control (no MindForm)* gives residents a fixed persona that never forms, which is
   the baseline for experiments.
2. **World**:

   | Setting | What it does |
   |---|---|
   | World brain | **LLM**: the model plans what residents do, narrates their experiences from the facts, and invents events. **Rules**: seeded, fast, free, reproducible. |
   | MindForm reads with | **LLM (API)**: MindForm's normal path. **Offline fallback**: lexicon or trained-head appraisal and a rule-based voice. |
   | Experiences per hour | 3–6. Each is a 20–10 minute stretch of island time, fed to MindForm as one experience. |
   | Drama | Calm, lively or dramatic: how often incidents and letters strike, and how hard. |
   | Seed | With rules + offline, the same seed gives exactly the same world. |

3. **Residents**: 1–10 new people, born through MindForm's own creation form:
   - **Biography**: a short bio, read by MindForm genesis.
   - **Fields + sliders**: v0's identity fields and its five temperament questions.

   Each resident also gets a life on the island (one of 10 jobs, or newcomer) and, optionally,
   what they want right now. **✦ Surprise me** fills in a cast with built-in friction.

## Watching

**It plays at a watchable pace.** Everything that happens is queued and shown one moment at a
time. At 1× that's about one every 1.5–3 seconds: a line said, a strong feeling, a relationship
turning, an event, or a notable result ("🎣 I caught two mackerel off the pier").
- People finish walking before they speak.
- The server waits for each beat's moments to finish before running the next beat. At 1× an
  island day takes about 12–15 minutes.
- **2×** and **4×** speed this up, **½×** slows it down, and **max** is for data runs.
- For a short video, record at 1× and timelapse it.

**Feelings, animated.** Each experience MindForm reads is named as an emotion from MindForm's
*own* appraisal (valence, intensity, threat, agency, social…). The world never decides how
anyone feels.

| Emotion | Animation |
|---|---|
| happy / excited | jump with arms up, sparkles |
| proud | chest out, fist up |
| warm | sway, blush, floating hearts |
| sad | slump under a little rain cloud |
| angry | red face, scowl, stomping and shaking |
| scared | trembling, arms up |
| embarrassed | hands over a blushing face |
| surprised | a hop and arms out |
| thoughtful | hand on chin |

- Faces keep the last mood: smile, frown, open mouth, brows.
- Mouths move while someone talks.
- When a relationship turns a corner ("Aya now feels warm toward Rex") hearts or 💔 float
  between them.

- **Story** (right panel): every line said, every inner reaction, every feeling, what formed,
  events and letters. It fills in step by step as the moments play.
- **Resident** (click anyone):
  - their MindForm state: traits against their baseline, the three needs, esteem, stance,
    voice, lens, values, beliefs;
  - what formed them, and their relationships;
  - the last experiences exactly as MindForm was told them, with their replies.
- **Bubbles**: speech is shown for everyone. Inner voice (their reply when nobody addressed
  them) only appears for the resident you're watching, so the screen stays readable. The
  residents list shows each person's current mood.
- **Camera**: *Orbit*, *Follow* (selected resident), *Cinema* (cuts to whoever speaks, with
  subtitles).
- **◉ Record** (or **H**) hides the interface for clean screen recording: just the island,
  bubbles, subtitles and the clock. **Space** plays and pauses.
- **⚡ God mode**:
  - *Whisper*: text that goes into one resident's next experience verbatim, like typing into
    the MindForm console.
  - *Event at a place*: whoever is there perceives it.
  - *Island-wide event*: everyone perceives it.
- **⋯**: download the whole world as a zip, or *clone it* (same cast, re-born from the same
  specs, new seed or modes) for A/B runs.

## How a beat works

One beat is one stretch of island time. Each beat:

1. **Direct**: weather (hourly), the ferry (10:00 and 17:00), letters, incidents, one
   community event a day (bonfire, town meeting, lantern festival, …).
2. **Plan**: every awake resident picks an action (do an activity, go somewhere, talk to
   someone, rest, go home). The planner reads their MindForm state:
   - starved needs push them toward what would meet them;
   - "leaning in" approaches people, "holding back" avoids them;
   - temperament, values, beliefs, relationships, job hours, weather and events also count.
3. **Resolve**: the world decides what actually happened:
   - who walked where (along the roads), and what came of each activity (seeded odds;
     residents do better at their own trade);
   - who spoke to whom, including who **walked off mid-sentence**;
   - who else was there, and what they saw.
4. **Live**: each resident's facts are narrated as a first-person experience and given to
   their mind.
   - **Conversations happen inside the beat.** The listener's mind answers, and the speaker
     hears that answer in the same experience.
   - Only an opening line comes from the planner. After that, every line is a MindForm reply.
5. **Settle**: relationships move by MindForm's own reading of each encounter (the valence
   of its appraisal). Everything is logged and saved, and the clock advances.

At 23:00 everyone walks home and sleeps. The night is skipped to 07:00, and the next morning
starts with them waking up.

## What gets saved (`data/worlds/<id>/`)

| File | Contents |
|---|---|
| `setup.json` | mind version, modes, seed, pace, drama, the cast's creation specs |
| `world.json` | current state, saved every beat (worlds resume after a restart) |
| `experiences.jsonl` | one row per resident per beat: the structured facts, the narrated text MindForm read, its reply, MindForm's appraisal, the emotion it was named as, the formation, and the resident's state afterwards |
| `feed.jsonl` | everything that happened, in order |
| `minds/` | the mind's own files (v0: `data/characters/*.json`, memories, its appraisal log, its server log) |

## The experiments it's built for

- **Nature vs nurture**: clone a world with a different seed or drama level, then compare
  the same people raised by different islands.
- **MindForm vs no MindForm**: run the same setup with *Control*.
- **API vs fallback**:
  - The two switches give 4 combinations: world brain LLM or rules, MindForm LLM or offline.
  - Rules + offline is fully reproducible: same seed, identical `experiences.jsonl`.
- **v0 vs v1**: once v1 has an adapter, run the same cast through both.

## Cost and speed (LLM mode)

- **Calls per resident per beat:** about 1 planner call, 1 narrator call, and one MindForm
  turn (about 5 calls inside v0).
- **Calls per island day:** 5 residents × 4 beats an hour × 16 waking hours comes to about
  2,000 calls on Gemini Flash.
- **Speed:** a beat takes about as long as the slowest resident's chain, roughly 10–20 s.
  Offline/rules is near-instant, paced only by the speed setting.
- **Failures:** if the API fails, each call falls back to rules on its own. If the key is
  rejected, calls pause for 5 minutes. The top bar shows how many calls failed and why.

## Code

```
app/
  main.py              HTTP API + the page
  config.py            settings (env > .env > MindForm v0's .env for the LLM key)
  llm.py               the world's OpenAI-compatible JSON call (httpx, no SDK)
  minds/               mind versions: base contract, mindform_v0 (subprocess adapter), control
  world/
    places.py          Halcyon Isle: places, activities + outcomes, jobs, homes, roads
    director.py        weather, ferry, community events, incidents, letters (+ LLM director)
    planner.py         LLM planner + seeded rules planner
    narrator.py        facts -> first-person experience (LLM or templates)
    engine.py          the beat: plan, resolve, live, settle; persistence
    manager.py         create / open / clone / list / export worlds
static/                the client: lobby wizard, Three.js island, HUD
tests/                 pytest (MindForm v0 integration tests skip without a checkout)
```

### Adding a MindForm version

Write an adapter with `start / stop / create(spec) / experience(ref, text) / state(ref)` that
maps its snapshot onto the small `MindState` summary (`app/minds/base.py`). Then add one row
to `app/minds/__init__.py`. The lobby, the planner and the inspector pick it up from there.

## Tests

```bash
pytest -q          # set MINDFORM_V0_PATH (or keep the sibling checkout) to include the v0 integration tests
```

## How this compares to Emergence World

Emergence World's agents are LLMs with 120+ tools. Their "experience" is an event log plus
diaries, so the base model is the mind, and the same world turns out differently per model.
Here the world only produces events, and MindForm decides who each resident becomes.
