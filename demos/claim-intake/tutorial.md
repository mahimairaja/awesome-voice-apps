---
title: How to build an auto claim intake voice agent
summary: Build a first-notice-of-loss voice agent that validates every field before it lands, from an empty folder to a running worker, on AssemblyAI, Gemini 3 Flash, and Inworld.
---

## What you will build

A voice agent that takes an auto insurance first notice of loss (FNOL). It
collects eight fields one at a time, validates each before it lands, mirrors the
claim on screen as it fills, reads the whole thing back, and files it with a
reference number. The finished demo is `claim-intake` in the cookbook.

You need four sets of keys: AssemblyAI (STT), Google AI (Gemini LLM), Inworld
(TTS), and three LiveKit values (URL, API key, API secret). And `uv`.

> [!NOTE]
> The Inworld key is Base64-encoded. Paste it exactly as Inworld gives it to
> you, do not decode it first.

## 1. Scaffold

Three files set up the runtime. Pin Python:

```
3.11
```

`pyproject.toml` declares the three provider plugins. The eval judge (`openai`)
lives in a dev group so it never ships in the demo's stack:

```toml
[project]
name = "claim-intake"
version = "0.1.0"
description = "Takes an auto insurance claim by voice, validates each field, and files it."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[assemblyai,google,inworld]>=1.6,<2.0",
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = ["livekit-agents[openai]>=1.6,<2.0"]
```

`.env.example` lists exactly the keys this demo uses:

```
# Auto FNOL intake. Copy to .env and fill:  cp .env.example .env
ASSEMBLYAI_API_KEY=
GOOGLE_API_KEY=
# Inworld requires a Base64-encoded API key.
INWORLD_API_KEY=
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

## 2. The claim model and validators

The claim is eight fields. A `Literal` names them once and everything else
derives from it, so adding a field is a single edit:

```python
FieldName = Literal[
    "claimant_name",
    "policy_number",
    "date_of_loss",
    "location",
    "vehicle",
    "description",
    "injuries",
    "drivable",
]

FIELDS: list[str] = list(FieldName.__args__)
```

Each validator takes the raw spoken value and returns `(ok, value_or_reason)`:
on success the cleaned value, on failure a plain-English reason the model can
read out. Two fields carry real rules. A policy number normalizes then matches a
regex:

```python
POLICY_RE = re.compile(r"^[A-Z]{2}\d{6,8}$")


def validate_policy_number(value: str) -> tuple[bool, str]:
    v = value.strip().upper().replace(" ", "").replace("-", "")
    if POLICY_RE.match(v):
        return True, v
    return False, "a policy number is two letters then six to eight digits, like AB123456"
```

The date of loss parses to `YYYY-MM-DD` and cannot be in the future:

```python
TODAY = date.today()


def validate_date_of_loss(value: str, today: date | None = None) -> tuple[bool, str]:
    today = today or TODAY
    try:
        d = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return False, "give the date of loss as year-month-day, like 2026-06-30"
    if d > today:
        return False, "the date of loss cannot be in the future"
    return True, d.isoformat()
```

> [!WARNING]
> `TODAY` is captured once at startup from `date.today()`, not written as a
> literal. Hard-code a date and "cannot be in the future" starts lying the day
> after you ship.

The yes/no fields normalize spoken variants, and the free-text fields just check
non-empty. One map wires field names to validators:

```python
VALIDATORS: dict[str, Callable[[str], tuple[bool, str]]] = {
    "claimant_name": _nonempty,
    "policy_number": validate_policy_number,
    "date_of_loss": validate_date_of_loss,
    "location": _nonempty,
    "vehicle": _nonempty,
    "description": _nonempty,
    "injuries": normalize_yesno,
    "drivable": normalize_yesno,
}
```

## 3. The agent and its two tools

The claim lives in `context.userdata` as a plain dict. `record_field` is the
only way in, and it runs the validator before anything lands:

```python
@function_tool()
async def record_field(self, context: RunContext[dict], field: FieldName, value: str) -> str:
    """Record one field of the auto claim.

    Use the exact field name. Pass date_of_loss as YYYY-MM-DD.
    """
    ok, result = VALIDATORS[field](value)
    if not ok:
        return f"rejected: {result}. Ask the caller again."
    claim = context.userdata["claim"]
    claim[field] = result
    _publish_claim(self.room, claim)
    return f"recorded {field}"
```

`file_claim` is the invariant: it refuses until every field is present, so the
model can never file a half-claim no matter how the conversation wanders:

```python
@function_tool()
async def file_claim(self, context: RunContext[dict]) -> str:
    """File the claim. Refuses until every field has been recorded."""
    claim = context.userdata["claim"]
    missing = [f for f in FIELDS if not claim.get(f)]
    if missing:
        labels = ", ".join(FIELD_LABELS[f] for f in missing)
        return f"cannot file yet, still missing: {labels}"
    ref = context.userdata.get("claim_ref")
    if not ref:
        ref = _make_claim_ref()
        context.userdata["claim_ref"] = ref
    _publish_filed(self.room, claim, ref)
    return f"Filed. The claim number is {ref}."
```

The instructions string tells the model to collect one field at a time, convert
spoken dates before recording, and read the claim back before filing. Keep it
plain text: the TTS reads it, so markdown and emojis bleed through.

## 4. Mirror the claim on screen

Every `record_field` re-publishes the form. `_publish_claim` sends a `KeyValue`
of the current fields plus a `Stat` progress meter, using a small helper that
mounts a component once then updates it:

```python
def _publish_claim(room: rtc.Room, claim: dict) -> None:
    publish_ui_event(
        room,
        "KeyValue",
        _ui_action(room, "claim"),
        component_id="claim",
        props={"title": "auto claim", "items": _claim_rows(claim)},
    )
    filled = sum(1 for f in FIELDS if claim.get(f))
    publish_ui_event(
        room,
        "Stat",
        _ui_action(room, "progress"),
        component_id="progress",
        props={"label": "captured", "value": filled, "of": len(FIELDS)},
    )
```

> [!TIP]
> Publish an empty form at startup (before anyone speaks) so the panel renders
> immediately. The demo calls `_publish_claim` once right after `ctx.connect()`.

Filing swaps the meter for a `Card` with the claim number. The rule is that the
UI mirrors state: re-publish after every mutation, not just on read.

## 5. The eval

The behavioral test runs the agent in text mode against the real Gemini and
scores the conversation with LiveKit's `evals` judges. It feeds a full claim,
lets the model do the speech-to-`YYYY-MM-DD` conversion, and asserts the
task-completion, tool-use, and relevancy judges pass:

```python
await session.run(user_input="My policy number is A B 1 2 3 4 5 6.")
await session.run(user_input="The accident was on March 3rd, 2026.")
...
evaluation = await judges.evaluate(session.history)
assert evaluation.all_passed
```

The date is a fixed past date so the test is stable; the part worth testing is
that the model converts the spoken date correctly.

## 6. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/claim-intake, paste your three LiveKit
values, and report a claim.
