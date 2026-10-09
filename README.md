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
- **You see the old demo (World Overview, Agent Inspector, no way to create people)?** Your
  browser kept the previous version in its cache. A cached old page now sends itself to the new
  start screen. A tab that was already open while the server restarted has stopped polling:
  click its **Resume** button or reload (Cmd+Shift+R). The new version is never cached, and an
  open tab reloads itself whenever the server is upgraded.

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
   | How residents talk | **Their own voices** (default): MindForm decides how each moment lands, and each resident says it in their own words (see [Voices](#voices)). **MindForm's words**: residents say MindForm's reply verbatim, for experiments on the raw output. |
   | Drama | Calm, lively or dramatic: how often incidents and letters strike, and how hard. |
   | Seed | With rules + offline, the same seed gives exactly the same world. |

3. **Residents**: 1–10 new people, born through MindForm's own creation form:
   - **Biography**: a short bio, read by MindForm genesis.
   - **Fields + sliders**: v0's identity fields and its five temperament questions.

   Each resident also gets:
   - a life on the island (one of 10 jobs, or newcomer);
   - a **goal** ("leave the island for good");
   - a **secret**, written to finish "*Name* …" ("is secretly building a boat to leave the
     island");
   - a **voice**: a few words on how they talk, with catchphrases in quotes ("gruff, dry, says
     "lad"").

   **🎬 Drama cast** loads the opening cast for the Reels series:
   - Aya wants to leave the island and has secretly bought a one-way ticket.
   - Rex is secretly building a boat to leave.
   - Ines is hiding that she mixed up two patients' charts.
   - Leo and Mira have secrets of their own.

   **✦ Surprise me** picks from a larger cast, also with secrets.

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

- **Story** (right panel): every line said, every inner reaction, every feeling, events,
  letters, secrets and clues. It fills in step by step as the moments play.
  - Dramatic moments are marked **★**: a secret worked out or confessed, a confrontation, a
    walk-off, a relationship turning, a trait card, a god-mode event.
  - **★ Highlights** and **Secrets** filter the log down to those.
- **Resident** (click anyone):
  - their goal, their secret (and who knows it), their voice;
  - their MindForm state: traits (smoothed) against their baseline, the three needs, esteem,
    stance, voice, lens, values, beliefs;
  - their relationships: ♥ affection and 🤝 trust per person, a label (friends, rivals, fond
    but wary…), and who knows or suspects whose secret;
  - what they know about others (gossip, clues);
  - the last experiences exactly as MindForm was told them, with MindForm's reply and what
    they actually said.
- **Bubbles**: speech is shown for everyone. Inner voice (their reply when nobody addressed
  them) only appears for the resident you're watching, so the screen stays readable. The
  residents list shows each person's current mood.
- **Camera**: *Orbit*, *Follow* (selected resident), *Cinema* (cuts to whoever speaks, with
  subtitles).
- **◉ Record** (or **H**) hides the interface for clean screen recording: just the island,
  bubbles, subtitles and the clock. **Space** plays and pauses.
- **⚡ God mode** (or **G**): one click and it happens. Every preset hits **every** resident in
  the next beat; whoever isn't there hears about it. Everyone reacts out loud, on screen.
  When paused, it plays that beat right away.

  | Preset | What happens |
  |---|---|
  | ⛈️ Storm | A storm hits and the harbor master cancels every ferry for the day. People shelter at home. |
  | 🕵️ A stranger arrives | A stranger in a grey coat at the dock asks for someone by name (pick who, or random). |
  | ✉️ Anonymous letter | A letter under every door reveals someone's secret. The whole island knows. |
  | 🔥 Fire! | A building burns (pick which). Helpers rush in, and its workers take it personally. |
  | 🎉 Festival | A surprise festival in the plaza: lanterns, a brass band, a pull toward the plaza. |
  | 🗣️ Rumor | A rumor about someone goes around. Everyone now "knows" it, and gossip spreads it. |
  | 📦 Job lost | Someone loses their job. Their days change from the next beat on. |

  Or write it yourself:
  - *Everyone (forced reactions)*: a free-text event that hits everyone like a preset.
  - *At a place*: only whoever is there perceives it.
  - *Whisper*: text that goes into one resident's next experience verbatim, like typing into
    the MindForm console.
- **⋯**: download the whole world as a zip, or *clone it* (same cast, re-born from the same
  specs, new seed or modes) for A/B runs.

### Creator mode (for Reels)

| Control | Key | What it does |
|---|---|---|
| 🎬 Creator | C | Speech and inner-voice bubbles twice as big, bigger name tags, captions on. Debug chips (LLM calls, failures, model) are hidden. Big animated cards play for trait changes ("Aya · Neuroticism ↑"), secrets coming out, breaking news, and the end-of-day recap. |
| ▯ 9:16 | V | A centred portrait frame: the island, bubbles, cards and captions stay inside it, and the panels sit outside. Screen-record just the frame. |
| ◉ Record | H | Hides the panels and controls. Combine with 9:16 for a clean vertical shot. |
| 🔇 / 🔊 | M | Ambient sound (sea, wind and rain by weather, birds by day, crickets by night), a speech blip per resident in their own pitch, and stings for the big moments. |

- **Trait changes don't flip-flop.** MindForm's traits wobble up and down a little every
  turn. What you see is a slow moving average:
  - A card plays only when a trait has really moved (0.09).
  - At most one card per resident every ~4 island hours.
  - Turning back the other way needs twice the move.
- **Today on Halcyon Isle.** At bedtime a recap card plays the day's three strongest moments.
  With the LLM world brain, the model tightens the wording.

## Voices

**MindForm decides what a resident feels; the world decides the words.** v0's offline reply is
built from a small fixed set of sentences. So everyone whose strongest trait is
Conscientiousness used to end with "…so I'd rather make sense of it and handle it properly."
Now each resident says MindForm's reading in their own way.

- **Their style comes from OCEAN.** It mixes ten styles (warm, blunt, anxious, bold, dreamy,
  dry, chatty, formal, playful, gruff), refined by the written voice profile.
  - Catchphrases in quotes become theirs.
  - Their *current* traits bend the delivery: they hedge when anxious, skip the frills when
    quiet.
- **MindForm picks the line.**
  - Its emotion picks the pool: an angry answer, a scared inner thought, a relieved reaction
    to being exposed.
  - Its loudest need and stance pick what they reach for: "Stay a bit?", "I'll do it my way."
- **The world picks the topic.** That's what happened this beat: the event everyone is talking
  about, the gossip, what they were doing.
- **With the LLM world brain**, the model rewrites MindForm's reply in the resident's voice:
  - it keeps MindForm's feeling;
  - it stays on topic;
  - it never spills a secret unless they're confiding.
  - Rules take over on any failure.
- **The repetition guard:**
  - Nobody says a line close to one of their own last 14 lines, or to anyone's recent lines.
  - No two residents share a template phrase: ~600 templates and every tic are shared out
    among the cast at birth.
  - When a resident has nothing fresh to say, they stay quiet.
  - Event, outcome and letter texts don't repeat either: no incident while it's recent, nobody
    gets the same result twice in a row, nobody gets the same letter twice.
- **MindForm's own words are still logged.** `experiences.jsonl` keeps MindForm's reply
  (`reply`) next to what was said (`spoken`, `voice`). Choose *MindForm's words* at setup to
  turn the voice layer off.

## Relationships, gossip and secrets

- **Two numbers per pair.**
  - **Affection** moves with MindForm's own reading of each encounter (the valence of its
    appraisal).
  - **Trust** moves with what visibly happened: a walk-off, a confession, a confrontation, a
    lie found out.
  - Labels (friends, rivals, fond but wary, distrustful…) change only when a number is clearly
    past the line, so they don't flip-flop.
- **People who end up together talk.**
  - Two residents at the same place may start a short conversation: an opener, MindForm's
    answer, and the opener's last word, which the other hears next beat.
  - Two residents whose walks cross on the road exchange a line in passing, shown mid-walk.
  - What they talk about is the world's pick: the news, gossip, a goal, a worry, how the other
    looks.
- **Gossip.** Residents pass on what they know about each other: things they saw, rumors,
  secrets they heard.
- **Secrets** shape what a resident does and thinks:
  - They tend it out of sight: Rex works on the boat after hours, Ines re-checks the charts.
  - It surfaces in their inner voice.
  - Others find out by:
    - seeing clues when they're there while the holder works on it;
    - gossip;
    - a slip under stress;
    - a confession to someone they trust;
    - god mode's anonymous letter.
  - Suspicion adds up per person until they've worked it out.
  - Whoever knows may confront the holder. The holder denies or admits it, depending on their
    stance and MindForm's reading.

## How a beat works

One beat is one stretch of island time. Each beat:

1. **Direct**: weather (hourly), the ferry (10:00 and 17:00), letters, incidents, one
   community event a day (bonfire, town meeting, lantern festival, …).
2. **Plan**: every awake resident picks an action (do an activity, go somewhere, talk to
   someone, rest, go home). The planner reads their MindForm state:
   - starved needs push them toward what would meet them;
   - "leaning in" approaches people, "holding back" avoids them;
   - temperament, values, beliefs, relationships, job hours, weather and events also count.
3. **Arrange and voice**: the world decides what actually happens:
   - who walks where (along the roads);
   - who talks to whom: planned conversations, plus people who meet at a place or cross on
     the road;
   - what each conversation is about.

   Opening lines are written in each speaker's voice.
4. **Commit**: the facts:
   - what came of each activity (seeded odds; residents do better at their own trade);
   - who **walked off mid-sentence**;
   - who saw someone working on their secret;
   - who else was there.
5. **Live**: each resident's facts are narrated as a first-person experience and given to
   their mind. Their reply comes out in their voice.
   - **Conversations happen inside the beat.** The listener's mind answers, and the speaker
     hears that answer in the same experience.
   - Only an opening line comes from the planner. After that, every line is a MindForm reply.
6. **Settle**:
   - Affection moves by MindForm's own reading of each encounter (the valence of its
     appraisal); trust moves by what happened.
   - Secrets spread.
   - Traits that really moved get a card.
   - Everything is logged and saved, and the clock advances.

At 23:00 everyone walks home and sleeps. The night is skipped to 07:00, and the next morning
starts with them waking up.

## What gets saved (`data/worlds/<id>/`)

| File | Contents |
|---|---|
| `setup.json` | mind version, modes, seed, pace, drama, the cast's creation specs |
| `world.json` | current state, saved every beat (worlds resume after a restart) |
| `experiences.jsonl` | one row per resident per beat: the structured facts, the narrated text MindForm read, its reply, what they actually said (`spoken`, and which `voice` wrote it), MindForm's appraisal, the emotion it was named as, the formation, and the resident's state afterwards |
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

- **Calls per resident per beat:**
  - about 1 planner call;
  - 1 narrator call;
  - 1–2 voice calls (their reply, plus an opening line when they start a conversation);
  - one MindForm turn (about 5 calls inside v0).
- **Calls per island day:** 5 residents × 4 beats an hour × 16 waking hours comes to about
  2,500 calls on Gemini Flash.
- **Speed:** a beat takes about as long as the slowest resident's chain, roughly 10–20 s.
  Offline/rules is near-instant, paced only by the speed setting.
- **Failures don't interrupt a recording:**
  - Transient errors (timeouts, dropped connections, 429, 5xx) are retried with backoff,
    honouring `Retry-After`.
  - After repeated failures the LLM is skipped for 45 seconds and every call uses its rules
    straight away.
  - A rejected key pauses LLM calls for 5 minutes.
  - A failed MindForm turn is retried once (the v0 process is restarted if it died).
  - A beat that fails is skipped; the simulation only pauses if three beats fail in a row.
  - Outside creator mode, the top bar shows how many calls failed and why.

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
    planner.py         LLM planner + seeded rules planner (goals, secrets, event pulls)
    narrator.py        facts -> first-person experience (LLM or templates)
    voice.py           each resident's voice: profiles, ~600 shared-out templates, the repetition guard, LLM voice
    intrigue.py        secrets, clues, gossip, trust/affection, conversation topics
    presets.py         god-mode presets (storm, stranger, letter, fire, festival, rumor, job loss)
    recap.py           the end-of-day recap
    engine.py          the beat: plan, arrange, voice, commit, live, settle; persistence
    manager.py         create / open / clone / list / export worlds
static/                the client: lobby wizard, Three.js island (outfits, fires, the stranger), HUD,
                       creator mode, synthesised sound (audio.js)
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
