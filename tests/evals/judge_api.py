"""Fetch the calls for the judge and post its scores to mahimai.ca.

The judge reads transcripts written by the calls it scores, so it never holds
the eval secret: this script runs before and after it in the workflow.

    python3 tests/evals/judge_api.py pending > pending.json
    python3 tests/evals/judge_api.py post judged.json

judged.json is Claude Code's `--output-format json` result, whose `result` text
is a JSON list of {"id": ..., "scores": [...]} (see JUDGE.md).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

URL = "https://mahimai.ca/api/playground/evals"


def send(method: str, url: str, body: dict | None = None) -> dict:
    headers = {
        "authorization": f"Bearer {os.environ['PLAYGROUND_EVAL_SECRET']}",
        # Cloudflare blocks requests without a user agent.
        "user-agent": "mahimai-eval-judge/1",
    }
    data = None
    if body is not None:
        headers["content-type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.load(res)


def verdicts(path: str) -> list[dict]:
    """The judged calls from Claude Code's JSON output."""
    with open(path) as f:
        output = json.load(f)
    if output.get("is_error"):
        sys.exit(f"The judge failed: {output.get('result')}")
    text = output.get("result") or ""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        sys.exit(f"The judge returned no JSON list: {text[:500]}")
    judged = json.loads(text[start : end + 1])
    if not all(
        isinstance(j, dict) and j.get("id") and isinstance(j.get("scores"), list) for j in judged
    ):
        sys.exit("Each judged call needs an id and a list of scores.")
    return judged


def main(args: list[str]) -> None:
    if args == ["pending"]:
        print(json.dumps(send("GET", URL + "?pending=1"), indent=1))
    elif len(args) == 2 and args[0] == "post":
        judged = verdicts(args[1])
        for call in judged:
            send("POST", URL, {"action": "judge", "id": call["id"], "scores": call["scores"]})
        print(f"Posted scores for {len(judged)} call(s).")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
