# Voice checkout

A voice agent that fills a store's checkout page for the shopper. It does not
own the form: each tool call is forwarded to the page over LiveKit RPC, and the
page validates the value and computes the price. When the page rejects a value
("M5V is in Ontario, not British Columbia"), the error comes back as the tool
result and the agent asks again. Fields the shopper types on the page are sent
back to the agent over RPC too.

The store, Ridgeline Outfitters, is fictional. Nothing is charged or shipped.

## Run

Copy `.env.example` to `.env` and fill in the LiveKit, OpenAI, Deepgram and
Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

The agent needs a page in the room that answers its RPC calls. Without one,
every tool reports that no checkout page is connected and the agent says so.
The page's participant token needs `canPublishData`, because RPC replies travel
over the data channel.

## RPC contract

The agent calls these methods on the shopper's participant. Payloads and
replies are JSON strings.

| Method | Payload | Reply |
| --- | --- | --- |
| `checkout.set_fields` | `{"fields": {"postal_code": "M5V 2T6", ...}}` | form state |
| `checkout.get_state` | `{}` | form state |
| `checkout.place_order` | `{"total": "$213.57"}` | `{"order": "RDG-..."}` |

Form state is `{"fields": {...}, "missing": [...], "total": "$213.57"}`. To
reject a value, the page throws an `RpcError` with code `2001` (invalid
fields) or `2002` (not ready to place the order), a short message the agent can
read aloud, and the form state as `data`. Accepted fields stay filled.

The page calls `checkout.page_edited` on the agent with `{"field", "value"}`
when the shopper types into the form.

Field names: `item`, `size`, `colour`, `quantity`, `full_name`, `street`,
`city`, `region`, `postal_code`, `country`, `shipping`.
