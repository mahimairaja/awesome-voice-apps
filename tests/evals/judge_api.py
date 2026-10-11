"""The only way the judge reaches mahimai.ca.

The judge reads transcripts written by the calls it scores, so it may only run
this script, never curl. The eval secret therefore only ever goes to the site.

    python3 tests/evals/judge_api.py pending
    python3 tests/evals/judge_api.py judge <call id> '<scores JSON list>'
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


def main(args: list[str]) -> None:
    if args == ["pending"]:
        print(json.dumps(send("GET", URL + "?pending=1"), indent=1))
    elif len(args) == 3 and args[0] == "judge":
        scores = json.loads(args[2])
        if not isinstance(scores, list):
            sys.exit("scores must be a JSON list")
        print(json.dumps(send("POST", URL, {"action": "judge", "id": args[1], "scores": scores})))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
