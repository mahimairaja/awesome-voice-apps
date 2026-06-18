# Playground components

The voice playground (playground.mahimai.ca) renders a fixed set of
generative-UI components. A demo's agent drives them by name over the
LiveKit `ui` data channel using the `publish_ui_event` helper.

Use only the components below. The `ui_components` array in a demo's
`playground.json` must contain only names from this list. A name outside
the set renders nothing (the playground skips it), so do not invent
components.

## The envelope

`publish_ui_event` sends:

```json
{
  "type": "ui_event",
  "component": "<Name>",
  "action": "mount | update | unmount",
  "id": "<stable id, e.g. 'order'>",
  "props": { }
}
```

Send `mount` once per component instance, then `update` with new props as
state changes (reuse the same `id`). The playground coalesces updates.

## The reverse envelope

Interactive components (today, `EditableTable`) send data back to the agent on
the LiveKit `ui_action` topic, the mirror of `ui_event`:

```json
{
  "type": "ui_action",
  "component": "EditableTable",
  "id": "<the actionId the component was given>",
  "action": "submit",
  "payload": { "rows": [["Q1", "A1"], ["Q2", "A2"]] }
}
```

The agent subscribes with `room.on("data_received")`, filters
`packet.topic == "ui_action"`, decodes the JSON, and matches on `id` and
`action`. `payload` is component-specific. Update state, then re-publish the
component over the forward channel so the UI reflects what the agent accepted.

## Primitives

The content-agnostic building blocks. Compose a demo's UI from these.

### List

A list of rows, each with a title and optional subtitle and right value.

```
props: { title?: string, items: [{ title: string, subtitle?: string, right?: string, image_url?: string, href?: string }] }
```

### KeyValue

Label and value rows. Mark a summary row with `accent: true`.

```
props: { title?: string, items: [{ label: string, value: string, accent?: boolean }] }
```

### Stat

A big number with an optional `/ of` denominator, a progress bar (when
`of` is set), and a caption.

```
props: { label?: string, value: string | number, of?: number, caption?: string }
```

### Meters

Labeled 0-to-1 bars for live gauges (audio health, confidence, sentiment). Each
item colors its bar by `band`; a `neutral` item renders as an uncolored level
meter; a `driver` item is marked as the largest contributor.

```
props: { title?: string, items: [{ label: string, value: number /*0..1*/, band?: "good"|"warn"|"bad", neutral?: boolean, driver?: boolean }] }
```

### Card

A title and body card, with optional subtitle, image, footer, and accent
border. A long `body` clamps to a few lines on the card and gains a "Read full
passage" popup, so you can send a whole source passage without flooding the
canvas.

```
props: { title?: string, subtitle?: string, body?: string, image_url?: string, footer?: string, accent?: boolean }
```

### Buttons

A row of buttons. A button with `action` dispatches a CTA event the page
can listen for; a button with `href` opens a link.

```
props: { title?: string, buttons: [{ label: string, primary?: boolean, action?: string, href?: string }] }
```

### Table

Columns and rows of plain strings.

```
props: { title?: string, columns: [string], rows: [[string]] }
```

### EditableTable

The editable twin of `Table`. Columns plus a grid of text cells the visitor can
type into, with a Save button that publishes the edited grid back to the agent
over the `ui_action` channel (see below). Re-send `rows` to overwrite what the
visitor sees, for example to confirm an edit or apply a voice change. Save is
disabled until a live session exists, so the panel is inert off-call.

```
props: { title?: string, columns: [string], rows: [[string]], submitLabel?: string, actionId?: string }
```

`actionId` is echoed as the `id` in the `ui_action` the panel publishes, so the
agent knows which control fired. Send the same value you used for the component's
`id`.

### Captions

A live transcript feed. Each entry shows the rendered line with the original
muted beneath it; the newest entry is emphasized and the feed auto-scrolls.
Send a short rolling window (the panel defensively caps at 20 entries).

```
props: { title?: string, items: [{ text: string, original?: string }] }
```

## Semantic aliases

Domain names that render through a primitive. Each carries a default
title you can override by sending `title` in props. Prefer a primitive
unless one of these reads more naturally for the demo.

| Name | Renders as | Props |
| --- | --- | --- |
| `Order` | List | same as `List` (default title "your order") |
| `Total` | KeyValue | same as `KeyValue` (default title "running total") |
| `Checkout` | Buttons | same as `Buttons` (default title "ready when you are") |
| `Cost` | cost summary card | `{ total_usd: number, lines: [{ label, value, sublabel? }] }` |
| `Score` | trivia scorecard | `{ correct: number, total: number, outOf: number }` |

## If a demo needs UI outside this set

Do not invent a component name. Adding a component is a deliberate change
to the playground (`components/demos/_vocabulary` in the voice-playground
repo), not something a demo declares. Comment on the issue noting the gap
and scaffold without that piece of UI, or stop and ask.
