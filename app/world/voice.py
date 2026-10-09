"""Voices: how each resident SOUNDS -- the words, never the feelings.

MindForm decides what a resident feels about an experience (its appraisal), what they reach
for (the loudest need) and whether they lean in or pull back (the stance); its reply carries
that. Offline, MindForm v0 says it with a small fixed set of sentences, so every resident
with the same strongest trait ends with the same clause. This layer keeps MindForm's reading
and says it in each resident's own words:

    profile   built at birth from the OCEAN levels (+ an optional short voice profile the
              user writes, e.g. "dry, sarcastic, calls everyone 'mate'") -> style tags,
              plus personal tics (openers, terms of address, closers) that nobody else on
              the island is given
    rules     a seeded compositor: the template is picked by the situation and by
              MindForm's emotion (an angry answer, a scared inner thought...), and the
              resident's live traits shape it (hedging when anxious, clipped when quiet)
    llm       the world's model rewrites MindForm's reply in the resident's voice, on topic,
              keeping its feeling and intent (rules on any failure)

The repetition guard: a resident never says a line close to one of their last N lines, and
no two residents share a template phrase (templates and tics are claimed by the first
resident who uses them). ``voices = "mind"`` turns all of this off: residents then say
MindForm's reply word for word (for experiments on the raw output).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import random
import re

from app import llm

log = logging.getLogger("mindform.world.voice")

VOICE_MODES = ("styled", "mind")
RECENT_LINES = 14                  # the guard looks this far back in a resident's own lines
SIMILAR = 0.72                     # SequenceMatcher ratio above this = "near-identical"
SHARED_RUN = 6                     # this many words in a row shared with someone else = a shared phrase

TAGS = ("warm", "blunt", "anxious", "bold", "dreamy", "dry", "chatty", "formal", "playful", "gruff")

# Profile words the user can write -> the style tags they mean.
_PROFILE_WORDS = {
    "warm": "warm", "sweet": "warm", "kind": "warm", "gentle": "warm", "caring": "warm", "motherly": "warm",
    "blunt": "blunt", "direct": "blunt", "harsh": "blunt", "frank": "blunt", "cold": "blunt",
    "nervous": "anxious", "anxious": "anxious", "shy": "anxious", "worried": "anxious", "timid": "anxious",
    "confident": "bold", "bold": "bold", "cocky": "bold", "brash": "bold", "proud": "bold", "loud": "bold",
    "poetic": "dreamy", "dreamy": "dreamy", "whimsical": "dreamy", "romantic": "dreamy", "philosophical": "dreamy",
    "dry": "dry", "sarcastic": "dry", "deadpan": "dry", "wry": "dry", "ironic": "dry", "cynical": "dry",
    "chatty": "chatty", "talkative": "chatty", "bubbly": "chatty", "excitable": "chatty", "rambling": "chatty",
    "formal": "formal", "proper": "formal", "precise": "formal", "polite": "formal", "stiff": "formal",
    "playful": "playful", "cheeky": "playful", "joker": "playful", "funny": "playful", "slang": "playful", "teasing": "playful",
    "gruff": "gruff", "grumpy": "gruff", "terse": "gruff", "salty": "gruff", "curt": "gruff", "grumbly": "gruff",
}

# ---- personal tics (each one is given to one resident only) --------------------------------
LEADS = {
    "warm": ["Oh, bless.", "Aw,", "Oh, sweetheart,", "Oh, you.", "Goodness,"],
    "blunt": ["Look.", "Listen.", "Straight up:", "Right.", "No sugar-coating:"],
    "anxious": ["Sorry, um…", "Oh no, okay.", "I mean—", "Um.", "Gosh, okay."],
    "bold": ["Here's the thing:", "Easy.", "Trust me,", "Obviously,", "Mark this:"],
    "dreamy": ["Funny, that.", "You know…", "Strange.", "Imagine that.", "Hm. Like the tide."],
    "dry": ["Well.", "Huh.", "Wonderful.", "Great. Just great.", "Fantastic."],
    "chatty": ["Oh my gosh,", "Okay okay okay,", "Wait wait—", "So!", "Guess what—"],
    "formal": ["Frankly,", "To be clear,", "If I may,", "Indeed.", "In fairness,"],
    "playful": ["Ha!", "Oi,", "No way—", "Hey hey,", "Ooh,"],
    "gruff": ["Hmph.", "Aye.", "Bah.", "Tch.", "Mm."],
}
ADDRESS = {
    "warm": ["love", "sweetheart", "dear"], "playful": ["mate", "buddy", "pal"], "gruff": ["lad", "kid"],
    "chatty": ["babe", "hon"], "bold": ["chief", "boss"], "dreamy": ["friend"], "anxious": [],
    "blunt": [], "dry": ["genius"], "formal": [],
}
CLOSERS = {
    "warm": ["Take care of yourself, yeah?", "You know where I am.", "Eat something, okay?"],
    "blunt": ["That's all.", "End of story.", "Take it or leave it."],
    "anxious": ["…Sorry. Forget I said that.", "Is that weird?", "Does that make sense?"],
    "bold": ["Mark my words.", "You'll see.", "Bet on it."],
    "dreamy": ["…don't you think?", "Like everything here.", "The sea always knows."],
    "dry": ["Thrilling.", "As usual.", "Who could have guessed."],
    "chatty": ["Anyway! Ha.", "Okay, I'm rambling.", "Sorry, I talk a lot."],
    "formal": ["That is all I'll say on it.", "Let's be sensible.", "I trust that's clear."],
    "playful": ["Kidding. Mostly.", "Don't quote me.", "You didn't hear it from me."],
    "gruff": ["Enough said.", "That's it.", "Don't make a fuss."],
}
# What MindForm's loudest need / enacted stance reaches for, out loud.
TAILS = {
    "relatedness": ["warm|Stay a bit?", "anxious|Don't go yet, okay?", "chatty|Coffee after? Please?",
                    "dreamy|It's easier with someone around.", "|I could use the company.", "playful|You're stuck with me now."],
    "competence": ["bold|I'll handle it. I always do.", "formal|I intend to get this right.",
                   "anxious|I just need one thing to go right.", "|I need to get this right.",
                   "gruff|I'll fix it myself.", "dry|Somebody has to do it properly."],
    "autonomy": ["blunt|I'll do it my way.", "gruff|Nobody tells me how.", "dreamy|I need room to breathe.",
                 "|I'll figure it out on my own.", "bold|My call. Nobody else's.", "playful|I'm a free spirit, apparently."],
    "approach": ["bold|Let's do something about it.", "chatty|Come on, let's go see!", "|I'm not sitting this one out.",
                 "warm|Let's go together."],
    "withdraw": ["anxious|I'd rather keep out of it.", "gruff|Leave me out of it.", "|I think I'll keep my distance.",
                 "dry|Count me out."],
    "conflicted": ["anxious|Part of me wants in, part of me wants to run.", "dreamy|Half of me says go, half says hide.",
                   "|I can't decide if I care."],
}

# ---- templates ---------------------------------------------------------------------------------
# role -> ["tags|text"]; tags empty = anyone. Slots: {to} {topic} {Topic} {who} {gossip} {goal}
# {goal_ing} {secret} {did} {place} {weather}. A template is skipped when a slot it needs is empty.
T: dict[str, list[str]] = {
    # --- opening a conversation
    "open:event": [
        "|Did you hear about {topic}?", "chatty|{Topic}! Can you believe it?", "dry|So. {Topic}. Thoughts?",
        "|Everyone's talking about {topic}.", "playful|Tell me you saw {topic}.", "anxious|Have you heard about {topic}? It's got me rattled.",
        "formal|I assume you've heard about {topic}.", "gruff|{Topic}. Of all things.", "bold|{Topic}. Somebody's got to deal with it.",
        "dreamy|{Topic}… it feels like an omen.", "warm|Are you alright after {topic}?", "blunt|{Topic}. What a mess.",
    ],
    "open:gossip": [
        "playful|Don't tell anyone, but {gossip}.", "|Have you noticed? {gossip}.", "chatty|Okay, between us: {gossip}!",
        "dry|Fun fact: {gossip}.", "anxious|I probably shouldn't say this, but {gossip}.", "blunt|{gossip}. Explain that.",
        "|You didn't hear this from me — {gossip}.", "dreamy|Something's going on. {gossip}.", "bold|Mark my words, something's up: {gossip}.",
        "formal|I don't mean to gossip, but {gossip}.", "gruff|Saw something. {gossip}.", "warm|I'm a bit worried — {gossip}.",
    ],
    "open:goal": [
        "|Can I tell you something? I keep thinking about {goal_ing}.", "dreamy|Do you ever think about {goal_ing}? I do. Every day.",
        "bold|One day I'm going to {goal}. Watch me.", "anxious|Is it silly that I want to {goal}?", "chatty|Okay, big dream: {goal_ing}!",
        "warm|You're the only one I'd tell — I want to {goal}.", "formal|I've decided I'm going to {goal}.", "dry|Life plan: {goal_ing}. Don't laugh.",
        "blunt|I'm going to {goal}. That's the plan.", "playful|New plan: {goal_ing}. You in?", "gruff|Going to {goal}. Somehow.",
    ],
    "open:small": [
        "warm|How are you holding up, {to}?", "chatty|{to}! What have you been up to?", "|Hey {to}. How's your day going?",
        "dry|Still alive, {to}?", "playful|Look who it is. Up to no good?", "formal|Good to see you, {to}. Busy day?",
        "anxious|Oh, hi {to}. Is now a bad time?", "gruff|{to}. Morning.", "bold|{to}! Just the person I wanted.",
        "dreamy|Funny running into you here, {to}.", "blunt|You look tired, {to}.",
    ],
    "open:outcome": [
        "chatty|Guess what — {did}!", "|So, {did}.", "dry|Highlight of my day: {did}.", "bold|{did}. Not bad, eh?",
        "anxious|Um, so… {did}.", "playful|Ask me what happened. Fine, I'll tell you: {did}.", "gruff|{did}. That's my day.",
        "warm|I have to tell someone — {did}.", "formal|For the record, {did}.", "dreamy|Today… {did}.", "blunt|{did}. Whatever.",
    ],
    "open:weather": [
        "|Some {weather}, huh?", "dry|Lovely {weather}. Truly.", "gruff|{Weather}. Figures.", "chatty|This {weather}! I can't even!",
        "dreamy|I like the {weather}. Everything goes quiet.", "anxious|Is this {weather} going to get worse?",
    ],
    "open:warm": [
        "warm|There you are! I was hoping I'd see you.", "chatty|{to}! Finally, a friendly face!", "playful|Missed me?",
        "|It's good to see you, {to}.", "dreamy|You always turn up at the right moment.", "bold|My favourite person on this rock.",
        "dry|Oh good, someone tolerable.", "gruff|You again. Good.", "formal|Always a pleasure, {to}.", "anxious|Oh thank goodness, it's you.",
    ],
    "open:cool": [
        "dry|Oh. It's you.", "gruff|What do you want?", "blunt|Make it quick, {to}.", "|Hm. {to}.", "formal|{to}. What is it?",
        "anxious|Oh — {to}. Hi. Um.", "playful|Well, well. {to}.", "bold|Didn't expect to see you here.",
    ],
    "open:probe": [
        "|What's going on with you lately, {to}?", "blunt|What are you hiding, {to}?", "playful|So what's under the tarp, so to speak?",
        "dry|You've been very mysterious lately.", "warm|You'd tell me if something was wrong, wouldn't you?",
        "anxious|Is everything okay? You've seemed… off.", "formal|Forgive me, but you've been behaving strangely.",
        "gruff|You're up to something.", "chatty|Okay, spill. Something's going on with you!", "dreamy|You carry something heavy, don't you?",
    ],
    "open:comfort": [
        "warm|Hey. Come here. Are you okay?", "|You don't seem yourself, {to}.", "chatty|Oh no, what happened? Talk to me!",
        "gruff|You look rough. Sit.", "dry|Rough day? You look it.", "anxious|Is it something I did?", "formal|Is there anything I can do?",
        "playful|Who do I need to fight?", "dreamy|Storms pass, you know.", "bold|Whatever it is, we'll sort it.",
    ],
    "open:confront": [
        "|I know about {secret}, {to}.", "blunt|I know about {secret}. Don't lie to me.", "warm|{to}… I know about {secret}. You can talk to me.",
        "dry|So. {Secret}. When were you going to mention it?", "anxious|I — I know about {secret}. I wasn't snooping, I swear.",
        "gruff|{Secret}. I know.", "formal|I'm aware of {secret}, {to}. We should talk.", "playful|So… {secret}, huh? Busted.",
        "bold|Everyone's going to find out about {secret}. Start talking.", "chatty|Okay, I KNOW about {secret} and you need to explain!",
        "dreamy|You can't keep {secret} hidden forever, {to}.",
    ],
    "pass:reply": ["|Morning!", "gruff|Mm.", "playful|Can't stop either!", "warm|Hello, love! Mind how you go.", "dry|Likewise.",
                   "formal|Good day to you.", "chatty|Hi! Bye! Ha!", "anxious|Oh! Hi! Sorry!", "bold|Later!", "dreamy|Lovely day for it.",
                   "blunt|Yep.", "|Hey! Catch you later.", "warm|Oh, hello! Don't work too hard.", "playful|Race you!",
                   "dry|Thrilling to see you too.", "gruff|Aye.", "chatty|Oh hey! Love the hurry!", "formal|Likewise. Good day."],
    "pass:greet": [
        "|Morning, {to}!", "playful|Oi, {to}! Can't stop!", "gruff|{to}.", "chatty|{to}! Hi! Bye! Busy!", "warm|Hello, love!",
        "dry|Don't mind me.", "formal|Good day, {to}.", "anxious|Oh! Hi. Sorry.", "bold|Out of the way, {to} — places to be!",
        "dreamy|Lovely day for it, {to}.", "blunt|Busy. Later.",
    ],
    # --- answering what was just said to them (by MindForm's reading)
    "answer:pos": [
        "warm|That's lovely, {to}. Really.", "chatty|Oh that's brilliant! Tell me everything!", "|That actually made my day.",
        "dry|Well. That's not the worst news.", "bold|Told you things would turn around.", "playful|Ha! Love that.",
        "formal|That's genuinely good to hear.", "gruff|Hm. Good.", "anxious|Oh — that's good, right? That's good.",
        "dreamy|See? The island gives back sometimes.", "blunt|Good. About time.", "|{Topic}? Honestly, that's great.",
        "warm|You always find the bright side.",
        "warm|Oh, I'm so happy for you!", "warm|Come here, you. That's wonderful.", "blunt|Good for you. Really.", "blunt|Fine. That's good.", "anxious|Really? Oh, thank goodness.", "anxious|Oh! That's — that's good news, isn't it?", "bold|Obviously. Keep up.", "bold|Course it did. With me around?", "dreamy|Oh, that's like something out of a song.", "dreamy|See? Magic, still.", "dry|Look at that. Miracles.", "dry|Well, someone's winning.", "chatty|Shut UP. That's amazing!", "chatty|Okay, I love this, keep going!", "formal|That's excellent news.", "formal|I'm genuinely pleased to hear it.", "playful|Get in! Love that for you.", "playful|Ha! Legend.", "gruff|Hm. Decent.", "gruff|Well done, then."
    ],
    "answer:neg": [
        "blunt|That's bad. That's really bad.", "anxious|Please don't say that. I'll worry all night.",
        "dry|Oh, perfect. Just what we needed.", "gruff|Rotten luck.", "warm|Oh no. Come here.", "formal|That's deeply unfortunate.",
        "chatty|No. No no no. Seriously?", "dreamy|Some days the tide just takes things.", "playful|Well, that's a mood killer.",
        "bold|Then we fix it.", "|I don't like where this is going.",
        "warm|Oh, love. That's rotten.", "warm|You poor thing. Sit down a minute.", "blunt|That's on you.", "blunt|Well, that's a mess.", "anxious|Oh no. Is it my fault?", "anxious|I knew something would go wrong.", "bold|Then we fight it.", "bold|Nope. Not accepting that.", "dreamy|Like a tide going out and not coming back.", "dreamy|That's a sad little story.", "dry|Ah. Delightful.", "dry|Couldn't have scripted it worse.", "chatty|No! Oh no, oh no. What happened?", "chatty|Wait, start from the beginning!", "formal|That's most regrettable.", "formal|That should not have happened.", "playful|Oof. That's grim, mate.", "playful|Yikes. Want a biscuit?", "gruff|That's rough.", "gruff|Hmph. Figures."
    ],
    "answer:neutral": [
        "|Huh. I'll think about that.", "dry|Riveting.", "formal|I see.", "gruff|If you say so.", "chatty|Hm! Okay, interesting!",
        "dreamy|Maybe. Who knows what it means.", "anxious|Right. Okay. Sure.", "playful|Noted, captain.", "warm|Mm. I hear you.",
        "bold|Could go either way. I'll make it go mine.", "blunt|Okay.",
        "warm|Well, you know I'm here.", "warm|Mm. Shall I make us some tea?", "blunt|So?", "blunt|And?", "anxious|Is that… good or bad?", "anxious|Sorry — what do you mean?", "bold|Leave it with me.", "bold|I've handled worse.", "dreamy|Hm. I wonder what it means.", "dreamy|Maybe it's a sign.", "dry|Gripping.", "dry|Stop, I can't take the suspense.", "chatty|Okay okay, and then what?", "chatty|Ooh, tell me more!", "formal|I'll take that under advisement.", "formal|Understood.", "playful|Riveting stuff, chief.", "playful|Plot twist?", "gruff|Aye. Maybe.", "gruff|Mm. Could be."
    ],
    "answer:joy": ["chatty|Yes! Oh, this is the best!", "warm|You've made me so happy.", "playful|Ha! I knew it!", "|I'm grinning like an idiot.",
                   "dry|Fine. I'm smiling. Happy?", "bold|That's what I'm talking about!"],
    "answer:excited": ["chatty|Wait — really?! Let's go!", "playful|Oh, this is going to be good.", "|I can't sit still now!", "bold|Finally, something happens!"],
    "answer:pride": ["bold|Told you I could do it.", "|Not bad, right?", "gruff|Did it myself.", "formal|I'm rather pleased, honestly.", "dry|Try not to be too impressed."],
    "answer:warm": ["warm|You're too kind to me, {to}.", "|You always know what to say.", "chatty|Stop it, you'll make me cry!", "gruff|…Thanks. Means something.",
                    "dreamy|I'm glad it's you I ran into.", "anxious|Really? You mean that?"],
    "answer:sad": ["|…Yeah. It's been a day.", "anxious|Sorry, I'm not great company right now.", "gruff|Leave it.", "warm|I just need a minute.",
                   "dry|Ask me tomorrow.", "dreamy|Everything feels far away today."],
    "answer:angry": ["blunt|Are you serious right now?", "gruff|Don't push me.", "|You've got some nerve, {to}.", "dry|Oh, brilliant. Thanks for that.",
                     "bold|Say that again. I dare you.", "formal|That is completely out of line."],
    "answer:fear": ["anxious|Don't say that. Please.", "|That scares me, {to}.", "gruff|Keep your voice down.", "warm|Promise me we'll be okay.",
                    "dry|Great. Now I won't sleep.", "chatty|Okay I'm freaking out a bit!"],
    "answer:embarrassed": ["anxious|Oh god. Did everyone see?", "|Can we not talk about it?", "playful|Ha… yeah. Forget that happened.",
                           "dry|Wonderful. Witnesses.", "gruff|Drop it."],
    "answer:surprise": ["|Wait, what?!", "chatty|No way. NO way.", "dry|Well, that's new.", "gruff|Huh. Didn't see that coming.",
                        "dreamy|The island's full of surprises.", "formal|That's… unexpected."],
    "answer:pos:topic": ["|{Topic}? That actually made my day.", "chatty|{Topic}! Okay, I'm smiling now.", "warm|{Topic} — I'm so glad.",
                         "dry|{Topic}. Fine, that's good news.", "bold|{Topic}? Told you it'd work out.", "playful|{Topic}? Love it.",
                         "formal|{Topic} is good news indeed.", "gruff|{Topic}. Good.", "dreamy|{Topic}… the island has its moments.",
                         "anxious|{Topic}? Oh — that's good, right?"],
    "answer:neg:topic": ["|{Topic}… I can't stop thinking about it.", "blunt|{Topic}? Don't get me started.", "dry|{Topic}. Just what we needed.",
                         "anxious|{Topic}? Please don't. I'll worry all night.", "gruff|{Topic}. Rotten business.", "warm|{Topic}… oh, that's awful.",
                         "formal|{Topic} is deeply unfortunate.", "chatty|{Topic}?! No. No no no.", "bold|{Topic}? Then somebody fixes it.",
                         "dreamy|{Topic}… some days the tide takes things.", "playful|{Topic}? Way to kill the mood."],
    "answer:neutral:topic": ["|{Topic}, huh. We'll see.", "dry|{Topic}. Fascinating. Truly.", "formal|{Topic}. I see.", "gruff|{Topic}. If you say so.",
                             "chatty|{Topic}? Hm! Interesting!", "dreamy|{Topic}… who knows what it means.", "anxious|{Topic}? Right. Okay.",
                             "playful|{Topic}, noted, captain.", "warm|{Topic}. I hear you.", "bold|{Topic}? I'll make it go my way.",
                             "blunt|{Topic}. Okay."],
    "open:ally": ["|Have you told anyone else about {secret}?", "warm|I keep thinking about what you told me. Are you okay?",
                  "anxious|I haven't said a word. About {secret}. I promise.", "playful|Your secret's safe with me. Totally. Mostly kidding. Totally.",
                  "blunt|You need to deal with {secret}.", "dry|So. {Secret}. Still a thing?", "gruff|{Secret}. You alright?",
                  "formal|About {secret} — have you decided what to do?", "chatty|Okay, I can't stop thinking about {secret}!",
                  "dreamy|Does {secret} keep you up at night?", "bold|If you need help with {secret}, say the word."],
    # --- the speaker's last word, after hearing the answer
    "close:pos": ["warm|I'm glad we talked.", "|Good. That's good.", "chatty|Okay, I feel better now!", "dry|Look at us, having a nice time.",
                  "gruff|Right. Good.", "playful|Same time tomorrow?", "formal|Thank you. I mean it.", "bold|See? Told you.",
                  "dreamy|That'll stay with me.", "anxious|Okay. Okay, good.",
        "warm|You've made my day, you know.", "blunt|Good talk.", "anxious|Thank you for talking to me.", "bold|Stick with me.", "dreamy|I'll think of this tonight.", "dry|Don't let it go to your head.", "chatty|Ahh, I'm so glad I ran into you!", "formal|Thank you for telling me.", "playful|Catch you on the flip side.", "gruff|Mind yourself."],
    "close:neg": ["|Forget it.", "blunt|Fine. Whatever.", "anxious|Sorry. I shouldn't have brought it up.", "gruff|Should've kept my mouth shut.",
                  "dry|Well, this was fun.", "warm|I'll leave you be.", "formal|Let's leave it there.", "chatty|Ugh. Never mind!",
                  "dreamy|Maybe another day.", "bold|Your loss.",
        "warm|I'm sorry. Truly.", "blunt|We're done here.", "anxious|I've made it worse, haven't I.", "bold|Not done yet.", "dreamy|Let the sea take it.", "dry|Lovely chat.", "chatty|Sorry, I'll stop talking.", "formal|I'll leave you to it.", "playful|Welp. That killed the vibe.", "gruff|Leave it there."],
    "close:neutral": ["|Anyway.", "dry|So. That happened.", "gruff|Right then.", "chatty|Okay! Well! Bye!", "formal|Very well.",
                      "playful|Laters.", "warm|See you around, yeah?", "anxious|Okay… bye.", "dreamy|Until the next tide.", "bold|Later.",
        "warm|Mind how you go.", "blunt|Right. Go.", "anxious|Sorry for keeping you.", "bold|I'll be around.", "dreamy|Drift safe.", "dry|Try not to miss me.", "chatty|Okay! Bye! Love you! Bye!", "formal|Good day.", "playful|Toodles.", "gruff|Off you go."],
    # --- inner thoughts (the resident to themselves)
    "inner:pos": ["|Okay. That was actually nice.", "dreamy|Maybe this place isn't so bad.", "chatty|Can't stop smiling. Stop smiling.",
                  "dry|Don't get used to it.", "bold|Knew it.", "anxious|Don't jinx it. Don't jinx it.", "gruff|Hm. Not bad.",
                  "warm|Little things. That's what it's about.", "formal|A good day's work.", "playful|Nailed it.",
        "warm|That made my whole morning.", "warm|People are kind here. Really kind.", "blunt|Good. Finally.", "blunt|That worked. Fine.", "anxious|Okay. Okay! That was good. Right?", "anxious|Nobody laughed at me. Small win.", "bold|Who's the best? Me.", "bold|Told them. Told all of them.", "dreamy|The light today. Like honey.", "dreamy|Some days the island hums.", "dry|Shockingly, that went well.", "dry|Mark the calendar. A good thing happened.", "chatty|I have to tell someone. Anyone!", "chatty|Okay that was amazing, ahh!", "formal|Well done. Quietly, well done.", "formal|That was handled correctly.", "playful|Ten out of ten. Would island again.", "playful|Somebody give me a medal.", "gruff|Not bad. Not bad at all.", "gruff|Good day's graft."],
    "inner:neg": ["|Why does this keep happening.", "anxious|Breathe. Just breathe.", "gruff|Should've stayed in bed.",
                  "dry|Fantastic. Truly.", "dreamy|The grey gets in everywhere.", "bold|This isn't over.", "warm|Be kind to yourself.",
                  "chatty|Ugh, ugh, ugh.", "formal|Unacceptable.", "playful|Cool. Cool cool cool.",
        "warm|Don't cry. Not here.", "warm|I just want everyone to be okay.", "blunt|That was stupid.", "blunt|Waste of a morning.", "anxious|Everyone saw. Everyone.", "anxious|Why did I say that. Why.", "bold|Fine. Round two.", "bold|Nobody gets to see me rattled.", "dreamy|The sea's gone grey inside me.", "dreamy|Everything feels further away.", "dry|And the hits keep coming.", "dry|Brilliant. Love that for me.", "chatty|Ugh, and I was having a good day!", "chatty|Who do I complain to about this?", "formal|That was not acceptable.", "formal|One must do better.", "playful|Well, that was a disaster. Fun, though.", "playful|Note to self: never again.", "gruff|Bloody typical.", "gruff|Should've known."],
    "inner:neutral": ["|Hm.", "dreamy|Funny how the days blur here.", "dry|Another thrilling day.", "gruff|Fine.",
                      "anxious|Is that it? Is something coming?", "chatty|Okay, what next?", "formal|Noted.", "bold|Next.",
                      "warm|Alright then.", "playful|Plot twist pending.",
        "warm|Tea. I need tea.", "warm|Wonder how everyone's doing.", "blunt|Next thing.", "blunt|Nothing to see.", "anxious|Did I lock the door?", "anxious|Am I forgetting something?", "bold|What's next to conquer?", "bold|This island's too small for me.", "dreamy|Where do the gulls go at night?", "dreamy|Clouds like unfinished letters.", "dry|Peak island excitement.", "dry|Another day in paradise. Allegedly.", "chatty|Okay, what's everyone up to?", "chatty|So many things! So little island!", "formal|Everything in its place.", "formal|Next item on the list.", "playful|Bored. Must cause chaos.", "playful|What would a pirate do?", "gruff|Tide's turning.", "gruff|Work to do."],
    "inner:pos:topic": ["|{Topic} went better than I thought.", "|Okay, {topic}? Actually good.", "dreamy|{Topic}… I'll keep this one.",
                        "dry|{Topic} didn't ruin my day. Progress.", "bold|Nailed {topic}.", "chatty|{Topic}! Best part of the day!",
                        "warm|{Topic} — it's the little things.", "anxious|{Topic} went fine. Don't jinx it.", "gruff|{Topic}. Could be worse.",
                        "formal|{Topic}: satisfactory.", "playful|{Topic}? Crushed it. Mostly.", "blunt|{Topic}. Worth it."],
    "inner:neg:topic": ["|So much for {topic}.", "|{Topic}. Why is it always me?", "dry|{Topic}. Wonderful. Truly.", "gruff|{Topic}. Rubbish.",
                        "anxious|{Topic} went wrong. Of course it did.", "dreamy|{Topic}… the grey gets in everywhere.",
                        "bold|{Topic} won't beat me.", "warm|{Topic}. Be kind to yourself.", "chatty|Ugh, {topic}! Ugh!",
                        "formal|{Topic} was a mistake.", "playful|{Topic}. Cool. Cool cool cool.", "blunt|{Topic} was a waste of time."],
    "inner:neutral:topic": ["|{Topic}. Fine.", "dreamy|{Topic}… funny how the days blur.", "dry|{Topic}. Thrilling stuff.", "gruff|{Topic}. Done.",
                            "anxious|{Topic}… is something coming?", "chatty|{Topic}, done! What next?", "formal|{Topic}, done properly.",
                            "bold|{Topic}. Next.", "warm|{Topic}. Alright then.", "playful|{Topic}. Plot twist pending.",
                            "blunt|{Topic}. Whatever.", "|{Topic}, and that's that."],
    "inner:angry": ["|Unbelievable.", "gruff|Don't. Lose. It.", "dry|Oh, this is rich.", "bold|They'll regret that."],
    "inner:fear": ["anxious|Something's wrong. I can feel it.", "|Don't panic. Don't panic.", "gruff|Keep your head down."],
    "inner:sad": ["|Just get through today.", "dreamy|Even the gulls sound sad.", "warm|It'll pass. It always passes."],
    "inner:joy": ["chatty|Best. Day.", "|I could get used to this.", "playful|Ha!"],
    "inner:goal": ["|One day I'll {goal}.", "dreamy|Someday, {goal_ing}.", "bold|I will {goal}. Soon.", "anxious|What if I never get to {goal}?",
                   "gruff|Still going to {goal}.", "dry|Step one of {goal_ing}: survive today."],
    # --- reacting out loud to something big (god mode, storms, fires...)
    "react:pos": ["chatty|{Topic}! Yes!", "|Oh, {topic}! Finally something good.", "playful|Now THIS is island life.", "warm|Oh, how wonderful.",
                  "bold|About time something happened!", "dry|{Topic}. Fine, that's actually great."],
    "react:neg": ["|{Topic}? No…", "anxious|Oh no. Oh no no no.", "gruff|{Topic}. Of course.", "blunt|This is bad.",
                  "dry|{Topic}. Perfect timing.", "warm|Is everyone alright?", "bold|Right. Everyone move!", "chatty|What?! {Topic}?!",
                  "formal|This is serious.", "dreamy|I knew the island was restless."],
    "react:neutral": ["|{Topic}. Huh.", "dry|Well, {topic}. Naturally.", "gruff|Hm. {Topic}.", "chatty|Okay, {topic}?! What now?",
                      "formal|We should keep calm about {topic}.", "dreamy|{Topic}… strange days.", "playful|Plot twist: {topic}."],
    "react:fear": ["anxious|Somebody do something!", "|I don't like this. I don't like this at all.", "gruff|Stay away from it."],
    "react:angry": ["blunt|Who did this?!", "|This is a disgrace.", "bold|Someone's going to answer for this."],
    "react:surprise": ["|{Topic}?! Since when?", "chatty|Oh my gosh — {topic}!", "dry|Well. Didn't have that on my list."],
    # --- gossip, secrets, confrontations
    "reply:gossip:pos": ["chatty|No! Tell me more!", "playful|Ooh, scandal.", "|Huh. Didn't know that.", "dry|Riveting. Go on."],
    "reply:gossip:neg": ["warm|That's not ours to talk about.", "blunt|Stop spreading that.", "|I'd rather not hear it.", "anxious|Should we be talking about this?"],
    "reply:gossip:neutral": ["|Huh. Interesting.", "dry|And I care because…?", "gruff|None of my business.", "dreamy|Everyone's carrying something."],
    "reply:confide:pos": ["warm|Thank you for trusting me. I won't tell a soul.", "|Your secret's safe with me.", "playful|My lips are sealed. Mostly. Fully!"],
    "reply:confide:neg": ["|…Wow. Okay. That's a lot.", "anxious|Why are you telling me this?", "blunt|You have to tell someone else. Properly."],
    "reply:confide:neutral": ["|I won't say anything.", "gruff|Hm. Alright.", "dry|Well. That explains a lot."],
    "reply:probe": ["|Nothing's going on.", "gruff|Mind your business.", "anxious|What? No. Why — who said that?", "dry|You've got quite the imagination.",
                    "warm|I'm fine, honestly. Don't worry about me.", "playful|Me? Mysterious? Never.", "formal|I'd rather not discuss it."],
    "reply:confront:deny": ["|I don't know what you're talking about.", "gruff|Who told you that?", "anxious|That's — that's not — who said that?",
                            "dry|Wow. Quite the story.", "blunt|Drop it. Now.", "formal|That is simply untrue."],
    "reply:confront:admit": ["|…Fine. Yes. It's true.", "anxious|Please don't tell anyone. Please.", "gruff|So you know. Now what?",
                             "warm|I wanted to tell you. I didn't know how.", "blunt|Yes. And I'm not sorry."],
}
_REQUIRED = re.compile(r"\{(\w+)\}")
_STATEMENT_STARTS = {"i", "i'm", "i'll", "i've", "it", "it's", "that", "that's", "this", "the", "you", "you're", "we",
                     "everyone", "nobody", "some", "something", "maybe", "there", "people", "they"}
_INTERJECTIONS = {"oh", "okay", "ok", "well", "hm", "hmm", "ha", "so", "right", "anyway", "ugh", "wait", "no", "yes",
                  "fine", "good", "great", "look", "listen", "aye", "mm", "huh", "oi", "hey", "um", "sorry", "fun",
                  "forget", "laters", "later", "same", "until", "see", "noted", "riveting", "rotten"}
_CONTRACTIONS = [("I'm", "I am"), ("don't", "do not"), ("can't", "cannot"), ("won't", "will not"), ("it's", "it is"),
                 ("It's", "It is"), ("That's", "That is"), ("that's", "that is"), ("I'll", "I will"), ("Don't", "Do not"),
                 ("isn't", "is not"), ("didn't", "did not"), ("we'll", "we will"), ("you'll", "you will")]


def _parse(entry: str) -> tuple[frozenset, str]:
    tags, _, text = entry.partition("|")
    return frozenset(t for t in tags.split(",") if t), text


def template_id(role: str, text: str) -> str:
    return role + ":" + hashlib.md5(text.encode()).hexdigest()[:8]


# ---- profile -----------------------------------------------------------------------------------------
def _trait_values(state: dict | None, levels: dict | None) -> dict[str, float]:
    values = {k: 0.0 for k in "OCEAN"}
    for t in (state or {}).get("traits") or []:
        if t.get("key") in values:
            values[t["key"]] = float(t.get("value") or 0.0)
    if levels and not any(values.values()):
        for k, v in levels.items():
            if k in values:
                values[k] = (int(v) - 3) / 2.5
    return values


def style_weights(traits: dict[str, float], esteem: float = 0.0) -> dict[str, float]:
    """OCEAN (+ self-esteem) -> how strongly each style tag fits, signed."""
    O, C, E, A, N = (traits.get(k, 0.0) for k in "OCEAN")
    return {
        "warm": A + 0.2 * E, "blunt": -A + 0.1 * C, "anxious": N - 0.3 * esteem, "bold": 0.6 * E - 0.5 * N + 0.4 * esteem,
        "dreamy": O - 0.3 * C, "dry": -0.4 * E + 0.3 * C - 0.2 * A + 0.1, "chatty": E + 0.1 * O, "formal": 0.8 * C - 0.4 * O - 0.1 * E,
        "playful": 0.5 * E + 0.3 * O - 0.4 * C, "gruff": -0.5 * A - 0.5 * E + 0.1 * C,
    }


def profile_tags(profile_text: str) -> list[str]:
    words = re.findall(r"[a-z]+", (profile_text or "").lower())
    out = []
    for w in words:
        tag = _PROFILE_WORDS.get(w)
        if tag and tag not in out:
            out.append(tag)
    return out


def quoted_phrases(profile_text: str) -> list[str]:
    """Catchphrases the user wrote in quotes: 'says "mate" a lot' -> ["mate"]."""
    found = re.findall(r"[\"“']([^\"”']{2,40})[\"”']", profile_text or "")
    return [f.strip() for f in found if f.strip()][:4]


def pick_tags(traits: dict[str, float], profile_text: str = "") -> list[str]:
    weights = style_weights(traits)
    ranked = sorted(TAGS, key=lambda t: -weights[t])
    tags = [t for t in profile_tags(profile_text)][:2]
    for t in ranked:
        if len(tags) >= 3:
            break
        if t not in tags and (weights[t] > 0.12 or len(tags) < 2):
            tags.append(t)
    return tags


def blip_voice(traits: dict[str, float], rid: str) -> dict:
    """Speech-blip sound for the client: pitch from energy/neuroticism, a per-resident offset."""
    h = int(hashlib.md5(rid.encode()).hexdigest()[:6], 16)
    pitch = 180 + 90 * traits.get("E", 0.0) + 45 * traits.get("N", 0.0) + (h % 80) - 40
    wave = ["triangle", "square", "sine", "sawtooth"][h % 4]
    return {"pitch": round(max(110.0, min(420.0, pitch)), 1), "wave": wave, "rate": round(1.0 + 0.35 * traits.get("E", 0.0), 2)}


class VoiceBook:
    """The world's voices: who owns which tic and template (persisted with the world)."""

    def __init__(self, seed: int, data: dict | None = None):
        self.seed = seed
        data = data or {}
        self.claims: dict[str, str] = dict(data.get("claims") or {})

    def to_json(self) -> dict:
        return {"claims": self.claims}

    # ---- birth
    def make_profile(self, rid: str, name: str, spec: dict, state: dict) -> dict:
        traits = _trait_values(state, spec.get("levels"))
        text = (spec.get("voice") or "").strip()[:200]
        tags = pick_tags(traits, text)
        rng = random.Random(f"{self.seed}:{rid}:voice")

        def take(table: dict, n: int) -> list[str]:
            pool = [x for t in tags for x in table.get(t, [])]
            rng.shuffle(pool)
            out = []
            for x in pool:
                key = "tic:" + x
                if key in self.claims and self.claims[key] != rid:
                    continue
                self.claims[key] = rid
                out.append(x)
                if len(out) >= n:
                    break
            return out

        catch = quoted_phrases(text)
        address = [c for c in catch if len(c.split()) == 1][:1] or take(ADDRESS, 1)
        return {"tags": tags, "profile": text or describe_tags(tags), "leads": take(LEADS, 3),
                "address": address, "closers": take(CLOSERS, 2), "catch": [c for c in catch if c not in address][:2],
                "blip": blip_voice(traits, rid)}

    def allocate(self, residents: list) -> None:
        """Share every template out among the cast, fairly: each goes to a resident whose style
        fits it (anyone, for untagged ones) who has the fewest of that kind so far. After this
        no two residents can ever say the same template phrase."""
        order = sorted(residents, key=lambda r: hashlib.md5(f"{self.seed}:{r.id}".encode()).hexdigest())
        for role, entries in T.items():
            count = {r.id: 0 for r in order}
            for entry in entries:
                ttags, text = _parse(entry)
                tid = template_id(role, text)
                if tid in self.claims:
                    if self.claims[tid] in count:
                        count[self.claims[tid]] += 1
                    continue
                fits = [r for r in order if not ttags or ttags & set((r.voice or {}).get("tags") or [])]
                if not fits:
                    continue
                pick = min(fits, key=lambda r: (count[r.id], order.index(r)))
                self.claims[tid] = pick.id
                count[pick.id] += 1
        for kind, entries in TAILS.items():
            count = {r.id: 0 for r in order}
            for entry in entries:
                ttags, text = _parse(entry)
                tid = template_id("tail:" + kind, text)
                if tid in self.claims:
                    continue
                fits = [r for r in order if not ttags or ttags & set((r.voice or {}).get("tags") or [])]
                if fits:
                    pick = min(fits, key=lambda r: (count[r.id], order.index(r)))
                    self.claims[tid] = pick.id
                    count[pick.id] += 1

    # ---- the rules mouth
    def _candidates(self, rid: str, role: str, tags: list[str], fills: dict, used: set) -> list[tuple[str, str]]:
        out = []
        for entry in T.get(role, []):
            ttags, text = _parse(entry)
            if ttags and not ttags & set(tags):
                continue
            needed = set(_REQUIRED.findall(text))
            if any(not fills.get(k) for k in needed):
                continue
            tid = template_id(role, text)
            owner = self.claims.get(tid)
            if owner and owner != rid:
                continue
            if tid in used:
                continue
            out.append((tid, text))
        return out

    def compose(self, r, role_keys: list[str], ctx: dict, rng: random.Random, recent: list[str],
                others_recent: list[str]) -> tuple[str, str] | None:
        """One line in this resident's voice. ``role_keys`` are tried in order (most specific
        first). Returns (line, template id) or None when nothing fits."""
        voice = r.voice or {}
        tags = voice.get("tags") or ["warm"]
        fills = _fills(ctx)
        used = set(r.said_ids[-RECENT_LINES:]) if hasattr(r, "said_ids") else set()
        relaxed = set(r.said_ids[-4:]) if hasattr(r, "said_ids") else set()
        for attempt in range(12):
            cands = []
            for blocked in (used, relaxed):          # fresh templates first; then their own, re-dressed
                for role in role_keys:
                    cands = self._candidates(r.id, role, tags, fills, blocked)
                    if cands:
                        break
                if cands:
                    break
            if not cands:
                return None
            tid, text = rng.choice(cands)
            line = _fill(text, fills)
            line = self._decorate(r, line, ctx, rng, attempt + (1 if tid in used else 0))
            if (not near_duplicate(line, recent) and not near_duplicate(line, others_recent)
                    and not shares_run(line, others_recent, names=ctx.get("names"), ignore=ctx.get("content") or ())):
                self.claims.setdefault(tid, r.id)
                return line, tid
            used.add(tid)
            relaxed.add(tid)
        return None

    def _decorate(self, r, core: str, ctx: dict, rng: random.Random, attempt: int) -> str:
        voice = r.voice or {}
        tags = set(voice.get("tags") or [])
        style = live_style(ctx.get("traits") or {}, ctx.get("esteem", 0.0))
        names = ctx.get("names") or set()
        recent_text = " ".join((getattr(r, "said", None) or [])[-6:])
        parts_lead = ""
        quiet = style["energy"] < -0.45                  # quiet people don't dress their lines up
        lead_p = (0.22 + 0.2 * max(0.0, style["energy"]) + (0.15 if attempt else 0.0)) * (0.5 if ctx.get("role") == "close" else 1.0)
        recent_text = " ".join((getattr(r, "said", None) or [])[-10:])
        first = core.split(" ", 1)[0].strip(",.!?—…")
        lead_ok = (ctx.get("role") != "inner" and len(core.split()) >= 4 and first not in names
                   and first.lower() not in _INTERJECTIONS)
        leads = [x for x in voice.get("leads") or [] if x not in recent_text]
        if leads and lead_ok and not quiet and rng.random() < lead_p:
            parts_lead = rng.choice(leads)
        line = core
        if parts_lead:
            line = _join(parts_lead, line, names)
        if ctx.get("to") and voice.get("address") and rng.random() < 0.22 and ctx.get("role") in ("answer", "close", "open"):
            term = rng.choice(voice["address"])
            if term.lower() not in line.lower() and term not in recent_text:
                line = re.sub(r"([.!?…])$", rf", {term}\1", line, count=1) if re.search(r"[.!?…]$", line) else f"{line}, {term}"
        tail_p = 0.3 + (0.25 if attempt else 0.0)
        tail_kind = ctx.get("need") if rng.random() < tail_p else (ctx.get("stance") if rng.random() < 0.15 else None)
        if tail_kind in TAILS and not quiet and ctx.get("role") in ("answer", "close", "inner", "react"):
            tail = self._claimed_tail(r.id, tail_kind, tags, rng)
            if tail and tail not in recent_text:
                line = f"{line} {tail}"
        closers = [c for c in voice.get("closers") or [] if c not in recent_text]
        if closers and rng.random() < 0.12 and ctx.get("role") in ("answer", "close"):
            line = f"{line} {rng.choice(closers)}"
        catch = [c for c in voice.get("catch") or [] if c not in recent_text]
        if catch and rng.random() < 0.1 and ctx.get("role") != "inner":
            line = f"{line} {catch[0].rstrip('.')}."
        # live style: MindForm's current traits bend the delivery
        first = line.split(" ", 1)[0].strip(",.!?—…").lower()
        statement = (len(line.split()) >= 4 and not line.rstrip().endswith("?") and first not in _INTERJECTIONS
                     and first in _STATEMENT_STARTS)
        if style["hedge"] > 0.35 and statement and rng.random() < style["hedge"] * 0.5 and not parts_lead:
            line = _join(rng.choice(["I mean,", "I don't know,", "Honestly? I think"]), line, names)
        if "formal" in tags:
            for a, b in _CONTRACTIONS:
                line = re.sub(rf"\b{re.escape(a)}\b", b, line)
        if style["energy"] > 0.45 and ctx.get("family") == "pos" and line.endswith("."):
            line = line[:-1] + "!"
        return re.sub(r"\s+", " ", line).strip()

    def claim_line(self, rid: str, options: list[str], rng: random.Random, recent: list[str]) -> str | None:
        """One of ``options`` (a confession, a slip, a secret thought) that no one else has said
        and this resident hasn't said lately."""
        pool = []
        for text in options:
            tid = template_id("fixed", text)
            owner = self.claims.get(tid)
            if (owner and owner != rid) or near_duplicate(text, recent):
                continue
            pool.append((tid, text))
        if not pool:
            return None
        tid, text = rng.choice(pool)
        self.claims.setdefault(tid, rid)
        return text

    def _claimed_tail(self, rid: str, kind: str, tags: set, rng: random.Random) -> str | None:
        pool = []
        for entry in TAILS[kind]:
            ttags, text = _parse(entry)
            if ttags and not ttags & tags:
                continue
            tid = template_id("tail:" + kind, text)
            owner = self.claims.get(tid)
            if owner and owner != rid:
                continue
            pool.append((tid, text))
        if not pool:
            return None
        tid, text = rng.choice(pool)
        self.claims.setdefault(tid, rid)
        return text


def describe_tags(tags: list[str]) -> str:
    words = {"warm": "warm and caring", "blunt": "blunt", "anxious": "nervous, apologetic", "bold": "confident, brash",
             "dreamy": "dreamy, a little poetic", "dry": "dry and sarcastic", "chatty": "chatty, excitable",
             "formal": "precise and formal", "playful": "cheeky and playful", "gruff": "gruff, few words"}
    return ", ".join(words[t] for t in tags if t in words)


def live_style(traits: dict[str, float], esteem: float = 0.0) -> dict[str, float]:
    O, C, E, A, N = (traits.get(k, 0.0) for k in "OCEAN")
    return {"energy": E, "hedge": max(0.0, 0.7 * N - 0.5 * esteem), "warmth": A, "precision": C, "whimsy": O}


# ---- slots -----------------------------------------------------------------------------------------
def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _fills(ctx: dict) -> dict:
    topic = ctx.get("topic") or ""
    goal = ctx.get("goal") or ""
    weather = ctx.get("weather") or ""
    return {"to": ctx.get("to") or "", "topic": topic, "Topic": _cap(topic), "who": ctx.get("who") or "",
            "gossip": (ctx.get("gossip") or "").rstrip("."), "goal": goal, "goal_ing": gerund_phrase(goal),
            "secret": ctx.get("secret_short") or "", "Secret": _cap(ctx.get("secret_short") or ""),
            "did": _did(ctx.get("did") or ""), "place": ctx.get("place") or "",
            "weather": weather, "Weather": _cap(weather)}


def _fill(text: str, fills: dict) -> str:
    out = _REQUIRED.sub(lambda m: fills.get(m.group(1), ""), text)
    return _cap(out.strip())


def _did(text: str) -> str:
    first = re.split(r"[;:]", text.strip(), maxsplit=1)[0].rstrip(". ")
    return first[:1].lower() + first[1:] if first and not first.startswith(("I ", "I'")) else first


def _join(lead: str, line: str, names: set) -> str:
    if lead.endswith((".", "!", "?", "…")):
        return f"{lead} {line}"
    first = line.split(" ", 1)[0].strip(",.!?—") if line else ""
    keep = first in ("I", "I'm", "I'll", "I've", "I'd") or first in names
    return f"{lead} {line if keep else line[:1].lower() + line[1:]}"


_IRREGULAR = {"be": "being", "see": "seeing", "flee": "fleeing", "lie": "lying", "die": "dying", "get": "getting",
              "stop": "stopping", "win": "winning", "run": "running", "put": "putting", "sit": "sitting", "swim": "swimming",
              "shop": "shopping", "plan": "planning", "quit": "quitting", "set": "setting"}


def gerund_phrase(goal: str) -> str:
    goal = (goal or "").strip().rstrip(".")
    if not goal:
        return ""
    first, _, rest = goal.partition(" ")
    low = first.lower()
    if low.endswith("ing"):
        return goal
    if low in _IRREGULAR:
        g = _IRREGULAR[low]
    elif low.endswith("ie"):
        g = low[:-2] + "ying"
    elif low.endswith("e") and not low.endswith(("ee", "ye", "oe")):
        g = low[:-1] + "ing"
    else:
        g = low + "ing"
    return (g + (" " + rest if rest else "")).strip()


# ---- the guard -------------------------------------------------------------------------------------
def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", " ", (text or "").lower()).strip()


def _max_similarity(line: str, recent: list[str]) -> float:
    a = _norm(line)
    best = 0.0
    for other in recent[-RECENT_LINES * 4:]:
        b = _norm(other)
        if not b:
            continue
        if a == b:
            return 1.0
        best = max(best, difflib.SequenceMatcher(None, a, b).ratio())
    return best


def near_duplicate(line: str, recent: list[str]) -> bool:
    return _max_similarity(line, recent) >= SIMILAR


def shares_run(line: str, others: list[str], *, names: set | None = None, run: int = SHARED_RUN,
               ignore: list[str] | tuple = ()) -> bool:
    """Whether ``line`` shares a run of ``run`` words with any line in ``others``. Names and the
    ``ignore`` phrases (what they are talking about: an event, a piece of gossip) don't count --
    two people may both mention "the note on the Town Hall door"; they may not share a phrasing."""
    names = {n.lower() for n in (names or set())}
    masks = sorted({_norm(p) for p in ignore if p and len(_norm(p).split()) >= 2}, key=len, reverse=True)

    def words(text):
        t = _norm(text)
        for m in masks:
            t = t.replace(m, " T ")
        return ["X" if w in names else w for w in t.split()]

    mine = words(line)
    if len(mine) < run:
        return False
    grams = {tuple(mine[i:i + run]) for i in range(len(mine) - run + 1)}
    for other in others:
        ow = words(other)
        for i in range(len(ow) - run + 1):
            if tuple(ow[i:i + run]) in grams:
                return True
    return False


# ---- the LLM mouth -----------------------------------------------------------------------------------
VOICE_SYSTEM = """You write the exact words ONE island resident says out loud (or thinks, for an inner thought).
They live on Halcyon Isle, a small island being filmed for a short-video series.

Their MIND (a personality engine) has already decided how they read the moment: its own words are
given as MIND SAID, with its emotion. Keep that feeling and what it reaches for -- never invent a
different feeling -- but say it the way THIS person talks (VOICE). Make it specific and on TOPIC.

Rules:
- 1-2 short spoken sentences, at most 26 words. Plain spoken English. No narration, no stage
  directions, no emojis, no surrounding quotes.
- Never reveal their SECRET unless the situation says they are confiding or admitting it; it may
  leak into subtext (evasion, nerves, a too-quick change of subject).
- Do not reuse wording from AVOID. Do not start the same way as any line in AVOID.
Return JSON only: {"line": "..."}"""

_ROLE_TEXT = {
    "open": "starting a conversation with {to}", "answer": "answering {to}, who just said: \"{heard}\"",
    "close": "having the last word with {to}, who just answered: \"{heard}\"", "inner": "thinking to themselves (inner voice)",
    "react": "reacting out loud, to nobody in particular, to what just happened", "pass": "greeting {to} in passing while walking",
}


def llm_line(r, ctx: dict, *, raw: str | None, stats: llm.Stats | None, avoid: list[str]) -> str:
    """Raises on failure or a guard hit (caller falls back to the rules mouth)."""
    voice = r.voice or {}
    situation = _ROLE_TEXT.get(ctx.get("role", "inner"), "speaking").format(to=ctx.get("to") or "someone",
                                                                             heard=(ctx.get("heard") or "")[:200])
    intent = ctx.get("intent")
    if intent == "confide":
        situation += ". They are CONFIDING their secret to this person, quietly"
    elif intent == "confront":
        situation += f". They KNOW {ctx.get('to')}'s secret ({ctx.get('their_secret')}) and are confronting them"
    elif intent == "gossip":
        situation += f". They are passing on gossip about {ctx.get('who')}: {ctx.get('gossip')}"
    elif intent == "probe":
        situation += f". They suspect {ctx.get('to')} is hiding something and are fishing"
    elif intent == "deny":
        situation += ". They are being confronted about their secret and DENY it"
    elif intent == "admit":
        situation += ". They are being confronted about their secret and ADMIT it"
    elif intent == "deflect":
        situation += ". Someone is fishing about their secret; they deflect"
    elif intent == "slip":
        situation += ". Under stress, they let a hint of their secret slip without saying it outright"
    lines = [f"RESIDENT: {r.name}" + (f" ({ctx.get('job')})" if ctx.get("job") else ""),
             f"VOICE: {voice.get('profile') or describe_tags(voice.get('tags') or [])}"
             + (f"; personal expressions: {', '.join(voice.get('leads', []) + voice.get('catch', []))}" if voice.get("leads") else ""),
             f"SITUATION: {situation}.",
             f"TOPIC: {ctx.get('topic') or ctx.get('did') or 'whatever is going on'}"]
    if raw:
        lines.append(f"MIND SAID: \"{raw}\"")
    mood = ctx.get("mood") or {}
    if mood:
        lines.append(f"EMOTION (from their mind): {mood.get('label')} (strength {mood.get('strength', 0):.2f})")
    if ctx.get("need"):
        lines.append(f"Their loudest need right now: {ctx['need']}")
    if ctx.get("goal"):
        lines.append(f"What they want (private): {ctx['goal']}")
    if ctx.get("secret_text"):
        lines.append(f"SECRET (private): {ctx['secret_text']}")
    if ctx.get("relationship"):
        lines.append(f"How they feel about {ctx.get('to')}: {ctx['relationship']}")
    if avoid:
        lines.append("AVOID:\n" + "\n".join(f"- {a}" for a in avoid[-12:]))
    data = llm.complete_json(VOICE_SYSTEM, "\n".join(lines), temperature=0.95, max_tokens=400, stats=stats)
    line = str(data.get("line") or "").strip().strip('"').strip()
    if not line or len(line) > 260:
        raise ValueError("voice returned no usable line")
    return line


def summarize_for_log(profile: dict) -> str:
    return json.dumps({k: profile.get(k) for k in ("tags", "leads", "address")}, ensure_ascii=False)
