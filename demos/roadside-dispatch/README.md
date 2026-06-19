# roadside-dispatch

Voice dispatcher that takes a breakdown call, scores the caller's audio
with Tyto in real time, adapts when the line degrades, and gates field
accuracy so a detail captured over a bad connection is re-confirmed
before help is sent.

Category: automotive.

## What it does

- Collects the four dispatch fields: location, vehicle, license plate,
  callback number.
- Scores the caller's audio every second with the Tyto local model across
  six dimensions: noise, reverb, loudness, interfering speech, background
  media, and packet loss.
- Mirrors a live audio-health HUD onto the playground: a risk Stat, a
  six-bar Meters panel, a status Card, and a captured-details List.
- Fires per-dimension interventions (debounced to two consecutive windows)
  and suppresses barge-in when other voices are detected.
- Flags any field captured under bad audio as "unconfirmed" and reads it
  back; blocks dispatch until every critical field is confirmed.

## Run it

Copy the env example and fill all seven keys. `AIC_SDK_LICENSE` comes
from [developers.ai-coustics.com](https://developers.ai-coustics.com).

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/roadside-dispatch
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Then open
[playground.mahimai.ca/demos/roadside-dispatch](https://playground.mahimai.ca/demos/roadside-dispatch),
paste your three LiveKit values, and start talking.

## Required keys

| Key | Where to get it |
| --- | --- |
| `LIVEKIT_URL` | LiveKit Cloud project settings |
| `LIVEKIT_API_KEY` | LiveKit Cloud project settings |
| `LIVEKIT_API_SECRET` | LiveKit Cloud project settings |
| `OPENAI_API_KEY` | platform.openai.com |
| `DEEPGRAM_API_KEY` | console.deepgram.com |
| `CARTESIA_API_KEY` | play.cartesia.ai |
| `AIC_SDK_LICENSE` | developers.ai-coustics.com |

## Acoustic soundboard

The six Tyto dimensions each respond to a different audio condition. You
can trigger most of them from a second device played near your laptop mic
while you speak the test call in headphones. Packet loss is the one
exception (see below).

**Setup:** put the agent audio in headphones so it does not feed back.
Play each noise source from a phone speaker held close to your laptop
mic. Lean away from the mic and speak from across the room to raise
reverb.

| Dimension | What to play | Where to search |
| --- | --- | --- |
| noise | highway or road traffic | freesound.org "highway traffic" or Pixabay "road noise" |
| background media | in-car radio or TV chatter | freesound.org "car interior radio" or Pixabay "radio talk" |
| interfering speech | crowd or multi-person conversation | freesound.org "crowd chatter" or Pixabay "office crowd" |
| reverb | speak from across the room or into an open space | no file needed: distance and hard surfaces do it |
| loudness | speak louder or quieter than normal | no file needed: it is a level meter, not a degradation |

Packet loss is a transport artifact that no acoustic source can reproduce.
It requires genuinely poor connectivity (throttled Wi-Fi, a mobile hotspot
at the edge of coverage, or a lossy tunnel). The overall risk score rises
whenever any other dimension spikes, so the field-accuracy gate still
triggers during a soundboard test even without packet loss.

## Recording

Coming after the demo is recorded.
