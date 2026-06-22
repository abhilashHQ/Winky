# Canvas System Design

Date: 2026-06-22

## Summary

Winky will gain a teaching-ready canvas system that lets it see the active monitor, plan visual explanations, and draw annotations or simple diagrams over the user's current screen. The user-facing model is: "Winky draws on whatever I am looking at."

Technically, Winky cannot draw inside another app's pixels. The implementation will use a transparent, always-on-top, click-through Electron overlay aligned to the active monitor. The underlying app remains visible and usable; the overlay contributes only drawings, labels, formulas, and fade animations.

## Goals

- Capture the active monitor when the user refers to visible content, including vague references such as "this," "that," or "here."
- Draw over the active monitor without stealing focus or blocking clicks.
- Support RT2-paced explanations: draw one step, speak one step, continue.
- Support both annotations on visible content and newly drawn teaching diagrams/formulas.
- Keep annotations persistent during an explanation, then fade and clear automatically after a short idle delay.
- Keep the harness as a bridge/lifecycle layer. RT2 remains the decision maker.

## Non-Goals

- No live tracking of moving video.
- No active-window-only capture.
- No all-monitor capture.
- No user-controlled next/previous buttons.
- No overlay interaction controls.
- No browser DOM injection.
- No native Win32/Direct2D renderer.
- No annotation persistence after auto-clear.

## Chosen Approach

Use a transparent fullscreen Electron overlay window plus Python screen capture.

Flow:

```text
User asks: "Explain this"
        ↓
RT2 decides screen context is needed
        ↓
get_screen_context()
        ↓
Harness captures active monitor screenshot + monitor metadata
        ↓
RT2 spawns canvas_vision agent with screenshot
        ↓
canvas_vision returns ordered visual explanation plan
        ↓
RT2 speaks and calls draw_annotation step by step
        ↓
Harness forwards drawing events to Electron
        ↓
Electron fullscreen transparent overlay draws over active monitor
        ↓
After final step, overlay stays briefly, then fades/clears
```

This approach works over YouTube, browsers, PDFs, desktop apps, and any visible screen content. It does require a transparent overlay BrowserWindow internally, but that window is not a user-facing browser or second content surface.

## User Experience Decisions

- Overlay mode: click-through by default.
- Capture target: active monitor only.
- Active monitor detection: monitor containing the cursor, falling back to primary monitor.
- Screen assumption: static snapshot while explaining.
- Annotation behavior: step-by-step persistent; prior steps stay visible until the explanation ends.
- Explanation scope: Winky may annotate visible content and create new diagrams/formulas in open areas of the overlay.
- Pacing: RT2 controls every step.
- Screen capture trigger: allowed for vague visible references such as "this," "that," "here," or "what is this," but no background periodic screenshots.
- Clear behavior: fade and clear after 8 seconds of no new annotation; clear immediately on explicit clear.
- Visual style: minimal teaching overlay with thin bright strokes, translucent fills, compact labels, and clean formula text.

## Components

### `harness/screen_context.py`

New focused module for screen capture.

Responsibilities:

- Identify the active monitor.
- Capture only that monitor.
- Return screenshot data and monitor metadata.
- Define the coordinate-space contract.

Output shape:

```json
{
  "status": "ok",
  "target": "active_monitor",
  "coordinate_space": "monitor_relative",
  "monitor": {
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080,
    "scale_factor": 1.25
  },
  "image": {
    "mime_type": "image/png",
    "base64": "..."
  }
}
```

The top-left of the captured monitor is `(0, 0)`. All annotation coordinates are relative to the captured monitor, not global desktop coordinates.

### Canvas Annotation Schema

Define a small predictable primitive schema. This can live in a dedicated module such as `harness/canvas_types.py` or locally near the interceptor if a separate module is unnecessary.

Supported MVP primitives:

- `line`
- `arrow`
- `rect`
- `circle`
- `text`
- `formula`
- `triangle`
- `clear`

Example annotation:

```json
{
  "id": "step-1-triangle",
  "kind": "triangle",
  "points": [
    { "x": 420, "y": 680 },
    { "x": 780, "y": 680 },
    { "x": 780, "y": 360 }
  ],
  "style": {
    "stroke": "cyan",
    "fill": "rgba(255,255,255,0.08)",
    "strokeWidth": 2
  }
}
```

`draw_annotation` should accept either a single annotation object or a step object with `annotations: []`; the interceptor normalizes both to the step form.

### `harness/interceptor.py`

Replace the current mocks.

`get_screen_context`:

- Calls `screen_context.capture_active_monitor()`.
- Sends the screen context as a tool result.
- Returns a structured error if capture fails.

`draw_annotation`:

- Normalizes the provided step.
- Validates/clamps safe values where possible.
- Broadcasts the draw event over the existing UI WebSocket:

```json
{
  "type": "draw_annotation",
  "step": {
    "annotations": []
  }
}
```

- Sends a structured tool result back to RT2.

For MVP, clearing uses `kind: "clear"`; no separate `clear_canvas` RT2 tool is required.

### `canvas_vision` Agent Handling

Add `hint="canvas_vision"` handling to `AgentRunner`.

Responsibilities:

- Receive the user's question and screen context.
- Produce an ordered visual explanation plan.
- Use only supported primitives.
- Use monitor-relative coordinates.
- Include short spoken text for RT2 to say for each step.

Output shape:

```json
{
  "success": true,
  "steps": [
    {
      "say": "Look at the right triangle in the video.",
      "draw": [
        {
          "kind": "triangle",
          "points": [
            { "x": 420, "y": 680 },
            { "x": 780, "y": 680 },
            { "x": 780, "y": 360 }
          ]
        }
      ]
    }
  ]
}
```

The result continues through the existing FIFO result queue and is injected only after `response.done`, preserving existing harness invariants.

### Electron Main Process

Add a second transparent fullscreen overlay BrowserWindow.

Responsibilities:

- Create the overlay on demand over the active monitor bounds.
- Keep it transparent, hidden until needed, skip-taskbar, always-on-top, and click-through by default.
- Use `setIgnoreMouseEvents(true, { forward: true })` where supported.
- Forward canvas events to the canvas renderer.
- Show the overlay on first annotation.
- Clear/hide the overlay after the renderer finishes fade-out.
- Keep the notch window and canvas overlay isolated.

### Separate Canvas Renderer Entry

Use a separate lightweight renderer entry, for example:

- `ui/renderer/canvas.html`
- `ui/renderer/canvas.jsx`
- `ui/renderer/CanvasOverlay.jsx`

This keeps the notch UI and the fullscreen canvas overlay from sharing accidental layout or state.

Responsibilities:

- Render annotations as SVG over a transparent root.
- Use `viewBox="0 0 width height"` so monitor-relative coordinates map directly to SVG coordinates.
- Accumulate persistent annotations by step.
- Animate new annotations subtly.
- Fade all annotations after 8 seconds of inactivity, then clear.
- Support immediate clear on `kind: "clear"`.

Default style:

- stroke: white or soft cyan
- stroke width: `2`
- fill: transparent or `rgba(255,255,255,0.08)`
- text: white, 14-18px, subtle dark shadow
- formulas: white/cyan, 22-28px, semi-bold
- no heavy glow by default

## Tool Contracts

### `get_screen_context`

Input: none for MVP.

Output:

```json
{
  "status": "ok",
  "target": "active_monitor",
  "coordinate_space": "monitor_relative",
  "monitor": {
    "x": 0,
    "y": 0,
    "width": 1920,
    "height": 1080,
    "scale_factor": 1.25
  },
  "image": {
    "mime_type": "image/png",
    "base64": "..."
  }
}
```

If capture fails:

```json
{
  "status": "error",
  "message": "I couldn't capture the screen."
}
```

### `spawn_agent(..., hint="canvas_vision")`

Input context should include:

- user's visual question
- screen context result
- coordinate-space instruction
- supported primitive list

Output:

```json
{
  "success": true,
  "steps": [
    {
      "say": "The two shorter sides are a and b.",
      "draw": [
        { "kind": "text", "x": 510, "y": 420, "text": "a" },
        { "kind": "text", "x": 610, "y": 350, "text": "b" }
      ]
    }
  ]
}
```

### `draw_annotation`

Input:

```json
{
  "step": {
    "annotations": [
      { "kind": "arrow" },
      { "kind": "formula", "text": "a² + b² = c²" }
    ]
  }
}
```

Normalized UI message:

```json
{
  "type": "draw_annotation",
  "step": {
    "annotations": []
  }
}
```

Clear command:

```json
{
  "step": {
    "annotations": [{ "kind": "clear" }]
  }
}
```

## Example: Pythagoras on YouTube

```text
User: "Explain Pythagoras theorem on this YouTube video."
RT2: get_screen_context()
Harness: returns active-monitor screenshot
RT2: spawn_agent(hint="canvas_vision", context=screenshot + request)
Harness: queues visual plan
RT2: "Let's use the right triangle here."
RT2: draw_annotation(step 1)
Overlay: draws triangle outline over video
RT2: "The two shorter sides are a and b."
RT2: draw_annotation(step 2)
Overlay: adds labels a and b
RT2: "The longest side is c."
RT2: draw_annotation(step 3)
Overlay: labels hypotenuse c
RT2: "The theorem says a squared plus b squared equals c squared."
RT2: draw_annotation(step 4)
Overlay: draws formula in open screen area
Overlay: after 8s idle, fades and clears
```

## Coordinates and DPI

MVP coordinate rules:

1. `get_screen_context` captures one monitor.
2. It returns the monitor's logical size and scale metadata.
3. `canvas_vision` outputs coordinates in monitor-relative logical space.
4. Electron creates the overlay over the same monitor.
5. The renderer sets an SVG viewBox matching the captured width and height.

The intended mapping is:

```text
annotation x/y = screenshot x/y = SVG x/y
```

Because Windows DPI scaling can make Electron bounds and screenshot pixels disagree, implementation must include a calibration test:

- capture active monitor
- draw a rectangle at known coordinates
- verify it lands where expected
- adjust scale mapping if needed

## Failure Handling

Screen capture failure:

- Return `{ "status": "error", "message": "I couldn't capture the screen." }`.
- RT2 should respond briefly and not draw.

Overlay unavailable:

- `draw_annotation` returns a structured error.
- RT2 can continue verbally.

Invalid coordinates:

- Clamp to monitor bounds where safe.
- Drop invalid shapes when clamping is not safe.
- Log warnings.
- Only surface to RT2 if no drawable annotations remain.

Unsupported primitive:

- Ignore it.
- Log a warning.

Monitor layout changes mid-explanation:

- Clear the overlay safely.

## Testing Plan

### Manual Acceptance Tests

Core tests:

1. "Explain this screen."
   - captures active monitor
   - draws at least one annotation
   - clears after 8 seconds
2. "Explain Pythagoras theorem on this video."
   - draws a triangle
   - labels `a`, `b`, `c`
   - draws formula `a² + b² = c²`
   - voice and drawings progress step by step
3. "What is this button?"
   - captures active monitor because of vague "this"
   - highlights or points to the relevant area if identifiable
4. While overlay is visible, click YouTube/app underneath.
   - clicks pass through
5. Move cursor to another monitor and ask again.
   - overlay appears only on that monitor

Edge tests:

- invalid annotation kind is ignored and logged
- screen capture failure returns a brief verbal error
- overlay window closed/crashed makes draw return an error instead of hanging
- high-DPI monitor mapping is visually correct
- changing monitor layout during explanation clears overlay safely

## Rollout Plan

1. Implement `screen_context.py` and verify active-monitor capture independently.
2. Add the transparent click-through canvas BrowserWindow.
3. Add the separate canvas renderer entry and SVG primitives.
4. Replace the `draw_annotation` mock with real UI broadcast routing.
5. Replace the `get_screen_context` mock with real capture.
6. Add `canvas_vision` handling in `AgentRunner`.
7. Update RT2 prompt/tool guidance.
8. Run manual acceptance tests.

## Success Criteria

Phase 2 is complete when:

1. `get_screen_context` captures the active monitor and returns screenshot + metadata.
2. `draw_annotation` renders on a transparent click-through overlay over the active monitor.
3. Winky can perform RT2-paced visual explanations using persistent step annotations.
4. The overlay supports line, arrow, rect, circle, text, formula, triangle, and clear.
5. Annotations auto-fade/clear after 8 seconds of inactivity.
6. Basic failures return structured errors and do not hang RT2.
7. Manual acceptance tests pass on the current Windows machine.
