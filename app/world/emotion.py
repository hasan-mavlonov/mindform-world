"""What a resident visibly feels -- read off MindForm's OWN appraisal of the experience.

The world never decides how someone feels. MindForm reads every experience into an appraisal
(valence, intensity, novelty, agency, social, outcome, self-relevance, threat/challenge) through
the resident's own lens; this module only names that reading with an everyday emotion so the
island can show it (a jump for joy, a slump, a red-faced stomp). Nothing here feeds back into
the mind.

The mapping follows appraisal theory (Scherer, Lazarus): pleasant + caused-by-me -> pride,
pleasant + about people -> warmth, unpleasant + threat -> fear, unpleasant + caused-by-others
-> anger, unpleasant + caused-by-me in front of others -> embarrassment, unpleasant otherwise
-> sadness, neutral but novel -> surprise.
"""
from __future__ import annotations

EMOTIONS = {
    "joy": ("happy", "😊"),
    "excited": ("excited", "🤩"),
    "pride": ("proud", "😤"),
    "warm": ("warm", "🥰"),
    "sad": ("sad", "😢"),
    "angry": ("angry", "😠"),
    "fear": ("scared", "😨"),
    "embarrassed": ("embarrassed", "😳"),
    "surprise": ("surprised", "😮"),
    "calm": ("calm", "😌"),
    "thoughtful": ("thoughtful", "🤔"),
}
SHOW_THRESHOLD = 0.25       # weaker readings only change the face
HEADLINE_THRESHOLD = 0.45   # strong ones get a moment on screen of their own


def read_emotion(appraisal: dict | None) -> dict | None:
    """Name MindForm's appraisal. None when there is no appraisal (control residents)."""
    if not appraisal:
        return None
    g = lambda k: float(appraisal.get(k, 0.0) or 0.0)
    v, i, n = g("valence"), g("intensity"), g("novelty")
    agency, social, outcome, tc = g("agency"), g("social"), g("outcome"), g("threat_challenge")
    strength = min(1.0, abs(v) * (0.5 + 0.5 * i))
    if v >= 0.15:
        if social >= 0.45:
            key = "warm"
        elif agency >= 0.35 or outcome >= 0.45:
            key = "pride"
        elif n >= 0.6 and i >= 0.5:
            key = "excited"
        else:
            key = "joy"
    elif v <= -0.15:
        if tc <= -0.35:
            key = "fear"
        elif agency >= 0.35 and social >= 0.2:
            key = "embarrassed"
        elif agency <= -0.2 or (tc >= 0.2 and i >= 0.5):
            key = "angry"
        else:
            key = "sad"
    elif n >= 0.65 and i >= 0.4:
        key, strength = "surprise", min(1.0, 0.3 + 0.6 * n * i)
    elif i <= 0.35:
        key, strength = "calm", 0.15
    else:
        key, strength = "thoughtful", 0.2
    label, emoji = EMOTIONS[key]
    return {"key": key, "label": label, "emoji": emoji, "strength": round(strength, 3),
            "valence": round(v, 3)}
