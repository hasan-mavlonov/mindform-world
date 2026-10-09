"""The narrator: turns what happened to a resident this stretch into their experience text.

This text is the ONLY thing MindForm sees, so the rule is strict: the world writes facts,
MindForm writes feelings. First person, past tense, what happened and what others visibly
did or said -- never "I felt", never a judgment. If the narrator interpreted the event, the
experiment would be measuring the narrator, not MindForm.

    llm    the world's model turns the fact list into a few natural sentences
    rules  the fact sentences joined as they are (deterministic)

Whispers (an experimenter speaking directly to one resident, god mode) bypass the LLM and
are appended verbatim, exactly like typing into the MindForm console.
"""
from __future__ import annotations

from app import llm

NARRATOR_SYSTEM = """You write one island resident's lived experience. A personality engine will read it
to decide how this changed them, so it must contain facts only.

Turn the FACTS into 2-5 plain sentences, first person, past tense, in order.
- Keep every quoted line EXACTLY as given, in double quotes, with who said it.
- State only what happened and what people visibly did or said.
- NEVER name or imply the resident's own feelings, thoughts, judgments or interpretations
  (no "I felt", "happily", "awkward", "unfortunately", "it was nice", "I realized").
- You may add at most one small neutral sensory detail that fits the place and weather.
- Do not add people, events or outcomes that are not in the facts.

Return JSON only: {"text": "..."}"""


def fact_sentence(item: dict) -> str:
    k = item["k"]
    if k == "wake":
        weather = f" Outside: {item['weather']}." if item.get("weather") else ""
        return f"I woke up in my cottage at {item['time']}.{weather}"
    if k == "bed":
        return "I walked home and went to bed."
    if k in ("weather", "outcome", "event", "whisper"):
        return item["text"]
    if k == "move":
        return f"I walked from {item['from']} to {item['to']}."
    if k == "look":
        return f"I went to {item['place']} to see what was going on."
    if k == "stay":
        return f"I stayed at {item['place']} for a while."
    if k == "said":
        return f"I said to {item['to']}: \"{item['line']}\""
    if k == "answer":
        return f"{item['who']} answered: \"{item['line']}\""
    if k == "no_answer":
        return f"{item['who']} did not say anything back."
    if k == "brushoff":
        return f"{item['who']} kept walking toward {item['to_place']} without stopping."
    if k == "heard":
        lead = f"{item['who']} came over" + (f" while I was busy ({item['while']})" if item.get("while") else "")
        return (f"{lead} and said to me: \"{item['line']}\"" if item.get("approach")
                else f"{item['who']} said to me: \"{item['line']}\"")
    if k == "heard_leaving":
        return f"As I was leaving, {item['who']} said to me: \"{item['line']}\""
    if k == "overheard":
        return f"{item['who']} said to {item['to']}: \"{item['line']}\""
    if k == "present":
        people = item["people"]
        if len(people) == 1:
            return f"{people[0]['name']} was there too, {people[0]['doing']}."
        names = ", ".join(p["name"] for p in people[:-1]) + f" and {people[-1]['name']}"
        return f"{names} were there too."
    if k == "convo_nearby":
        return f"{item['a']} and {item['b']} were talking nearby."
    if k == "waiting":
        return f"{item['who']} was still standing there, waiting for my answer."
    if k == "letter":
        return f"The ferry brought a letter for me. {item['text']}"
    return item.get("text", "")


def narrate_rules(items: list[dict]) -> str:
    return " ".join(s for s in (fact_sentence(i) for i in items) if s)


def narrate(items: list[dict], header: dict, *, use_llm: bool, stats: llm.Stats | None = None) -> tuple[str, str]:
    """Returns ``(text, source)``. Whispers always pass through verbatim."""
    world_items = [i for i in items if i["k"] != "whisper"]
    whispers = [i["text"] for i in items if i["k"] == "whisper"]
    text, source = narrate_rules(world_items), "rules"
    if use_llm and world_items and llm.available():
        facts = "\n".join(f"- {fact_sentence(i)}" for i in world_items)
        user = (f"Resident: {header['name']}. Time: {header['time_text']}. Weather: {header['weather_text']}. "
                f"Place: {header['place_name']}.\nFACTS (in order):\n{facts}")
        try:
            written = str(llm.complete_json(NARRATOR_SYSTEM, user, temperature=0.7, stats=stats).get("text") or "")
            if written.strip():
                text, source = written.strip(), "llm"
        except Exception:
            pass
    if whispers:
        text = (text + " " + " ".join(whispers)).strip()
    return text, source
