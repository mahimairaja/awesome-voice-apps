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

### Card

A title and body card, with optional subtitle, image, footer, and accent
border.

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
