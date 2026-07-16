---
title: How to build an auto claim intake voice agent
summary: A first-notice-of-loss voice agent that validates every field before it lands, on AssemblyAI, Gemini 3 Flash, and Inworld.
---

First notice of loss is the highest-volume call an insurer takes: someone just
had an accident and reads a policy number and a date into a phone. The job is
narrow and rules-heavy: capture eight fields, hold the line on the two that have
to be well-formed, and file.

The stack is three new-generation providers, one per role. AssemblyAI
Universal-3 Pro transcribes the messy parts (a spelled-out policy number, a
plate), Gemini 3 Flash drives the turn-by-turn collection, and Inworld speaks
the read-back. Turn-taking is LiveKit's inference VAD and turn detector, so
there is no local model to download.

The whole agent is one claim dict and two tools. `record_field` is generic: one
`Literal` of field names and one `VALIDATORS` map, so adding a field is a line,
not a tool.

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

Each validator returns `(ok, value_or_reason)`. The policy check normalizes then
matches a regex, so "a b 1 2 3 4 5 6" becomes `AB123456` or bounces back with a
reason the model reads aloud.

```python
POLICY_RE = re.compile(r"^[A-Z]{2}\d{6,8}$")


def validate_policy_number(value: str) -> tuple[bool, str]:
    v = value.strip().upper().replace(" ", "").replace("-", "")
    if POLICY_RE.match(v):
        return True, v
    return False, "a policy number is two letters then six to eight digits, like AB123456"
```

`file_claim` refuses until every field is present, so the model can never file a
half-claim no matter how the conversation wanders.

```python
missing = [f for f in FIELDS if not claim.get(f)]
if missing:
    labels = ", ".join(FIELD_LABELS[f] for f in missing)
    return f"cannot file yet, still missing: {labels}"
```

> [!NOTE]
> The date validator compares against `date.today()` captured at startup, not a
> literal, so "the date of loss cannot be in the future" keeps meaning that a
> year from now.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/claim-intake.
