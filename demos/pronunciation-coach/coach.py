"""Scoring and lesson logic for the pronunciation coach. No LiveKit imports.

The coach shows a line, the learner reads it aloud, and Deepgram returns every
word it heard with a confidence. This module lines those words up against the
line, scores each target word, picks the one or two weakest to drill, and moves
the learner up or down a level. The LLM only phrases the coaching.

A word's score is the recogniser's confidence that it heard that word. It is a
proxy for pronunciation, not a phoneme-level assessment: a clear, native-like
word comes back near 1.0, a word the model had to guess comes back low, and a
word it heard as something else scores zero. Tune the thresholds per STT model.
"""

import difflib
import random
import unicodedata

APP = "Parlo"

# Word scores are recogniser confidences in [0, 1]. Calibrated by ear against
# Deepgram Nova-3 on read speech; retune them for another model.
CLEAR = 0.85
PASS = 0.70
# A first read at or above this moves the learner up a level; below DOWN, down.
UP = 0.88
DOWN = 0.55
MAX_TARGETS = 2
MAX_DRILLS = 2

LANGUAGES = {
    "en": {"name": "English", "stt": "en-US", "tts": "en"},
    "fr": {"name": "French", "stt": "fr", "tts": "fr"},
}
LEVELS = ("Starter", "Intermediate", "Advanced")

# Lines picked for the sounds learners of each language get wrong. Each has the
# sound it trains and one mouth-position tip the coach can lean on.
PHRASES: dict[str, tuple[tuple[dict, ...], ...]] = {
    "en": (
        (
            {
                "text": "I think it is Thursday.",
                "focus": "th",
                "tip": "Tongue tip lightly between the teeth, then blow air.",
            },
            {
                "text": "Turn right at the red light.",
                "focus": "r and l",
                "tip": "For r the tongue touches nothing; for l the tip touches behind the top teeth.",
            },
            {
                "text": "Very well, we will wait.",
                "focus": "v and w",
                "tip": "For v the top teeth touch the lower lip; for w round the lips, no teeth.",
            },
        ),
        (
            {
                "text": "The weather this month is rather cold.",
                "focus": "voiced th",
                "tip": "Same tongue as th in think, but hum while you push the air.",
            },
            {
                "text": "She chose cheap shoes for the show.",
                "focus": "sh and ch",
                "tip": "sh is a long hiss; ch starts with a stop, like t then sh.",
            },
            {
                "text": "The world record was really rare.",
                "focus": "r and l",
                "tip": "Curl the tongue back for r and keep it off the roof of the mouth.",
            },
        ),
        (
            {
                "text": "Thirty three thousand thoughtful thinkers.",
                "focus": "th",
                "tip": "Keep the tongue between the teeth for every th, even at speed.",
            },
            {
                "text": "Red lorry, yellow lorry, rolling rapidly.",
                "focus": "r and l",
                "tip": "Slow down: lift the tongue tip for l, pull it back for r.",
            },
            {
                "text": "Particularly rural worlds rarely rule.",
                "focus": "r, l and w",
                "tip": "Round the lips for w, then let the tongue curl for r.",
            },
        ),
    ),
    "fr": (
        (
            {
                "text": "Bonjour, je voudrais un croissant.",
                "focus": "r français",
                "tip": "Le r se fait au fond de la gorge, comme un léger gargarisme.",
            },
            {
                "text": "Merci beaucoup, au revoir.",
                "focus": "r et ou",
                "tip": "Pour ou, arrondis les lèvres; le r reste au fond de la gorge.",
            },
            {
                "text": "Tu as vu la rue?",
                "focus": "u",
                "tip": "Dis i, puis arrondis les lèvres sans bouger la langue.",
            },
        ),
        (
            {
                "text": "Il y a un bon vin blanc.",
                "focus": "voyelles nasales",
                "tip": "Ne prononce pas le n: l'air passe par le nez.",
            },
            {
                "text": "La grenouille rouge saute sur le mur.",
                "focus": "u et ou",
                "tip": "ou: lèvres rondes, langue en arrière; u: langue en avant.",
            },
            {
                "text": "Le chat dort sur le fauteuil.",
                "focus": "eu et euil",
                "tip": "Pour eu, dis é puis arrondis les lèvres.",
            },
        ),
        (
            {
                "text": "Les chaussettes de l'archiduchesse sont sèches.",
                "focus": "s et ch",
                "tip": "s: sourire et siffler; ch: lèvres en avant, comme pour chut.",
            },
            {
                "text": "Un chasseur sachant chasser sans son chien.",
                "focus": "s et ch",
                "tip": "Ralentis et sépare bien chaque s et chaque ch.",
            },
            {
                "text": "Trois tortues trottaient sur un trottoir étroit.",
                "focus": "r et t",
                "tip": "Garde le r au fond de la gorge et le t sur les dents.",
            },
        ),
    ),
}


def key(word: str) -> str:
    """A spelling-insensitive key: lowercase, no accents, no punctuation."""
    text = unicodedata.normalize("NFKD", word.replace("’", "'").lower())
    return "".join(c for c in text if c.isalnum())


def tokenize(text: str) -> list[str]:
    """Display words of a line, without surrounding punctuation."""
    words = (w.strip('.,!?;:"«»()') for w in text.replace("-", " ").split())
    return [w for w in words if key(w)]


def align(target: list[str], heard: list[tuple[str, float]]) -> list[dict]:
    """Score every target word against what the recogniser heard.

    Matched words take their confidence; a word heard as something else, or
    not heard at all, scores zero. Extra words the learner said are ignored.
    """
    tkeys = [key(w) for w in target]
    heard = [(w, c) for w, c in heard if key(w)]
    hkeys = [key(w) for w, _ in heard]
    scored = [{"word": w, "score": 0.0, "status": "missed"} for w in target]
    matcher = difflib.SequenceMatcher(None, tkeys, hkeys, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op not in ("equal", "replace"):
            continue
        for i, j in zip(range(i1, i2), range(j1, j2)):
            said, conf = heard[j]
            # A near spelling ("color", "seche") is the same sound written differently.
            if op == "equal" or difflib.SequenceMatcher(None, tkeys[i], hkeys[j]).ratio() >= 0.8:
                score = _conf(conf)
                status = "clear" if score >= CLEAR else "close" if score >= PASS else "weak"
                scored[i].update(score=score, status=status)
            else:
                scored[i].update(status="misheard", heard=said[:24])
    return scored


def _conf(value) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 3)
    except (TypeError, ValueError):
        return 0.0


def overall(words: list[dict]) -> float:
    return round(sum(w["score"] for w in words) / len(words), 3) if words else 0.0


def initial_state() -> dict:
    return {
        "language": None,
        "level": 1,
        "phrase": None,
        "mode": "choose",
        "targets": [],
        "first": None,
        "drills": 0,
        "attempts": [],
        "history": [],
        "used": [],
        "note": "",
    }


def choose_language(state: dict, language: str) -> dict:
    if language not in LANGUAGES:
        raise ValueError("Only English and French are offered")
    state.update(language=language, level=1, history=[], used=[], note="")
    return next_phrase(state)


def next_phrase(state: dict, rng: random.Random | None = None) -> dict:
    pool = PHRASES[state["language"]][state["level"]]
    fresh = [p for p in pool if p["text"] not in state["used"]] or list(pool)
    phrase = (rng or random).choice(fresh)
    state["used"].append(phrase["text"])
    state.update(
        phrase={**phrase, "words": tokenize(phrase["text"])},
        mode="phrase",
        targets=[],
        first=None,
        drills=0,
        attempts=[],
    )
    return state["phrase"]


def skip(state: dict) -> dict:
    if state["phrase"]:
        _close(state, None)
    return next_phrase(state)


def is_attempt(state: dict, heard: list[tuple[str, float]], words: list[dict]) -> bool:
    """Tell a read-aloud attempt from talk ("can you repeat that?")."""
    if not heard:
        return False
    found = sum(1 for w in words if w["status"] not in ("missed", "misheard"))
    if state["mode"] == "drill":
        return found > 0 or len(heard) <= len(words) + 1
    return found / len(words) >= 0.34


def score_turn(state: dict, heard: list[tuple[str, float]]) -> dict | None:
    """Score one learner turn and advance the lesson. None means not an attempt."""
    phrase = state["phrase"]
    if not phrase or state["mode"] == "choose":
        return None
    drilling = state["mode"] == "drill"
    indices = state["targets"] if drilling else list(range(len(phrase["words"])))
    words = align([phrase["words"][i] for i in indices], heard)
    if not is_attempt(state, heard, words):
        return None
    for i, word in zip(indices, words):
        word["index"] = i
    score = overall(words)
    attempt = {"kind": state["mode"], "overall": score, "words": words}
    state["attempts"] = [*state["attempts"], attempt][-4:]
    result = {
        "attempt": attempt,
        "phrase": phrase["text"],
        "focus": phrase["focus"],
        "tip": phrase["tip"],
    }

    if state["mode"] == "phrase":
        state["first"] = score
        weak = sorted((w for w in words if w["score"] < PASS), key=lambda w: w["score"])
        if not weak:
            result.update(outcome="pass", change=_close(state, score))
            result["next"] = next_phrase(state)["text"]
            return result
        state.update(mode="drill", targets=sorted(w["index"] for w in weak[:MAX_TARGETS]), drills=0)
        result.update(outcome="coach", targets=[_target(w) for w in weak[:MAX_TARGETS]])
        return result

    if drilling:
        state["drills"] += 1
        before = _first_scores(state)
        result["targets"] = [_target(w, before.get(w["index"])) for w in words]
        if all(w["score"] >= PASS for w in words):
            state["mode"] = "retry"
            result["outcome"] = "drill_pass"
        elif state["drills"] >= MAX_DRILLS:
            state["mode"] = "retry"
            result["outcome"] = "drill_move_on"
        else:
            state["targets"] = [w["index"] for w in words if w["score"] < PASS]
            result["outcome"] = "drill_again"
        return result

    # A full re-read after the drill closes the phrase, better or not.
    before = _first_scores(state)
    result.update(
        outcome="retry_done",
        first=state["first"],
        gains=[
            _target(w, before[w["index"]])
            for w in words
            if w["index"] in before and before[w["index"]] < PASS
        ],
        change=_close(state, score),
    )
    result["next"] = next_phrase(state)["text"]
    return result


def _first_scores(state: dict) -> dict[int, float]:
    first = next((a for a in state["attempts"] if a["kind"] == "phrase"), None)
    return {w["index"]: w["score"] for w in first["words"]} if first else {}


def _target(word: dict, before: float | None = None) -> dict:
    target = {"word": word["word"], "score": round(word["score"] * 100), "status": word["status"]}
    if word.get("heard"):
        target["heard"] = word["heard"]
    if before is not None:
        target["before"] = round(before * 100)
    return target


def _close(state: dict, final: float | None) -> str:
    """Record the finished line and adapt the level from the first read."""
    first = state["first"]
    level = state["level"]
    change = "same"
    if first is not None and first >= UP and level < len(LEVELS) - 1:
        level, change = level + 1, "up"
    elif first is not None and first < DOWN and (final is None or final < UP) and level > 0:
        level, change = level - 1, "down"
    entry = {
        "text": state["phrase"]["text"],
        "first": None if first is None else round(first * 100),
        "final": None if final is None else round(final * 100),
        "level": LEVELS[state["level"]],
        "change": change if first is not None else "skipped",
    }
    state["history"] = [*state["history"], entry][-5:]
    state["level"] = level
    if change == "up":
        state["note"] = f"First read scored {entry['first']}: moved up to {LEVELS[level]}."
    elif change == "down":
        state["note"] = f"First read scored {entry['first']}: easier lines at {LEVELS[level]}."
    else:
        state["note"] = ""
    return change


def snapshot(state: dict) -> dict:
    """The panel's view of the lesson. Scores are 0 to 100."""
    phrase = state["phrase"]
    return {
        "app": APP,
        "language": state["language"],
        "level": state["level"],
        "levels": list(LEVELS),
        "mode": state["mode"],
        "phrase": (
            {
                "text": phrase["text"],
                "focus": phrase["focus"],
                "tip": phrase["tip"],
                "words": phrase["words"],
            }
            if phrase
            else None
        ),
        "targets": list(state["targets"]),
        "attempts": [
            {
                "kind": a["kind"],
                "overall": round(a["overall"] * 100),
                "words": [
                    {
                        "word": w["word"],
                        "index": w.get("index", 0),
                        "score": round(w["score"] * 100),
                        "status": w["status"],
                        **({"heard": w["heard"]} if w.get("heard") else {}),
                    }
                    for w in a["words"]
                ],
            }
            for a in state["attempts"][-3:]
        ],
        "history": list(state["history"]),
        "note": state["note"],
    }


def _list(targets: list[dict]) -> str:
    parts = []
    for t in targets:
        if t["status"] == "missed":
            how = "not heard"
        elif t.get("heard"):
            how = f"sounded like '{t['heard']}'"
        else:
            how = f"score {t['score']}"
        if "before" in t:
            how += f", was {t['before']}"
        parts.append(f"'{t['word']}' ({how})")
    return ", ".join(parts)


def brief(state: dict, result: dict) -> str:
    """The one system line the LLM gets after a scored turn."""
    lang = LANGUAGES[state["language"]]["name"]
    score = round(result["attempt"]["overall"] * 100)
    note = f" {state['note']}" if state["note"] else ""
    outcome = result["outcome"]
    if outcome == "pass":
        return (
            f"Scored read: {score}/100, every word clear.{note} Praise it in a few words, "
            f"then say the next line slowly once, '{result['next']}', and ask them to read it."
        )
    if outcome == "coach":
        words = ", ".join(t["word"] for t in result["targets"])
        return (
            f"Scored read: {score}/100. Weak words: {_list(result['targets'])}. "
            f"This line trains {result['focus']}. Tip: {result['tip']} "
            "Coach only those words and nothing else: say each one slowly, give one short "
            f"mouth-position tip, then ask them to say just: {words}."
        )
    if outcome == "drill_pass":
        return (
            f"Drill scored: {_list(result['targets'])}. That is fixed: say so in a few words, "
            f"then ask them to read the whole line once more: '{result['phrase']}'."
        )
    if outcome == "drill_again":
        words = ", ".join(t["word"] for t in result["targets"] if t["score"] < PASS * 100)
        return (
            f"Drill scored: {_list(result['targets'])}. Not there yet. Give one different tip, "
            f"model it once more slowly, and ask them to say just: {words}."
        )
    if outcome == "drill_move_on":
        return (
            f"Drill scored: {_list(result['targets'])}. Say it is getting closer, then ask them "
            f"to read the whole line once more: '{result['phrase']}'."
        )
    gains = f" Drilled words: {_list(result['gains'])}." if result.get("gains") else ""
    return (
        f"Whole line re-read: {score}/100, first read was {round(result['first'] * 100)}.{gains}"
        f"{note} Tell them how the drilled words changed in one sentence, then say the next "
        f"line slowly once, '{result['next']}', and ask them to read it. Speak {lang}."
    )
