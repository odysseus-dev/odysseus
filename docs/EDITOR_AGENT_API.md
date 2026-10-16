# Editor Agent API

How an agent (LLM or script) drives the PhotoCraft editor. There are three layers:

1. **Control API** — `window.geEditorControl`, a stable in-page command surface.
2. **NL planner** — `window.geEditorAI`, turns a prompt into control-API steps (LLM or basic mode).
3. **Bridge** — server-mediated SSE channel so a *server-side* agent can drive an **open** editor.

All three are owner-scoped by the app session; nothing is exposed to unauthenticated users.

---

## 1. Control API (`window.geEditorControl`)

Installed when the editor opens. Version `EDITOR_CONTROL_VERSION`.

```js
await window.geEditorControl.invoke(op, params)   // { ok, op, result } | { ok:false, op, error }
await window.geEditorControl.run([{op, params}])  // sequential, stops on first failure
window.geEditorControl.list()                     // [{op, description}]
```

`invoke` is async. Every op that mutates the document pushes **one** undo entry via
`_saveState`, so a single op is one undo step.

### Op catalog

| Op | Params | Notes |
| --- | --- | --- |
| `document.info` | – | size, layers, mode |
| `document.importImage` | `{dataUrl,name,raster}` | load a raster image as a new layer |
| `layers.list` | – | top-most first |
| `layers.select` | `{id\|index\|name}` | index is top-most-first |
| `layers.addRaster` | – | new empty pixel layer |
| `layers.addAdjustment` | `{type,params}` | adjustment layer |
| `layers.duplicate` / `layers.delete` | – | on the active/selected layer(s) |
| `layers.setOpacity` | `{id?,opacity 0-1}` | |
| `layers.setBlend` | `{id?,blendMode\|mode}` | |
| `layers.group` / `layers.ungroup` | – | |
| `selection.selectAll` / `deselect` / `invert` | – | |
| `selection.rectangle` / `selection.ellipse` | `{x,y,w,h}` | document space |
| `selection.subject` | `{label}` | SAM text grounding (falls back to a centred rect) |
| `selection.face` | – | SAM "face" (falls back to an upper-centre ellipse) |
| `selection.sky` | – | SAM "sky" (falls back to a top-band colour range) |
| `selection.background` | `{fuzziness}` | border colour union |
| `selection.samText` | `{text}` | SAM text grounding only (throws without SAM) |
| `selection.colorRange` | `{color,fuzziness}` | |
| `selection.hasMask` | – | `{active, space}` |
| `adjustments.apply` | `{type,params}` | adjustment layer; camelCase types accepted |
| `adjustments.applyMasked` | `{brightness,contrast,saturation,warmth,invert}` | pixel pass, masked if a selection exists else whole layer |
| `filters.apply` | `{id}` | opens the slider dialog and applies |
| `filters.applyMasked` | `{id,radius,amount,invert}` | `id`: blur-gaussian, sharpen, unsharp-mask |
| `transform.start` / `transform.apply` / `transform.cancel` | – | |
| `text.add` | `{text,x,y,color,size,font,align}` | |
| `history.undo` / `history.redo` | – | |
| `history.mark` | – | returns current undo depth (for grouping) |
| `history.collapse` | `{mark,label}` | collapses entries above `mark` into one |
| `export.png` | – | `{dataUrl}` of the flattened document |
| `export.gallery` | – | saves a copy back to the gallery |
| `screenshot` | – | `{dataUrl,width,height}` of the composited canvas |

Param conventions the planner must respect:

- `adjustments.applyMasked`: `brightness`/`contrast`/`warmth` are **-100..100** (0 neutral);
  `saturation` is a **multiplier** (1 neutral; values >3 are treated as percentages).
- `adjustments.apply` `type` aliases are normalised (`blackAndWhite` -> `black-white`).
- `color-balance` params are `{shadows:{r,g,b}, midtones:{r,g,b}, highlights:{r,g,b}}`;
  a bare number is coerced to `{r:n,g:n,b:n}`.
- A masked op with **no** active selection degrades to a whole-layer edit (does not throw).

---

## 2. NL planner (`window.geEditorAI`)

```js
const res = await window.geEditorAI.execute('blur the background behind the person', { onStep });
// exec: window.geEditorAI.execute('blur the background behind the person', { onStep });
// res = { ok, prompt, reply, planner, basicMode, steps, trace, failed }
```

Flow (`static/js/editor/nl-edit.js`):

1. Read document state + a **before** screenshot.
2. Ask the **server planner** (`POST /api/editor/plan`) with the prompt, state and
   screenshot. The client passes its strong guidance via `state.planner_guidance`.
   If the route is unavailable it falls back to `localPlan` (regex — labelled
   **"basic mode"** in the UI).
3. `sanitizeSteps()`: drop no-op/export steps and, if a masked op has no preceding
   `selection.*`, insert the region implied by the prompt (face/sky/background/subject).
4. `history.mark`, then run each step, screenshotting after each one.
5. **Self-check** (server plans only, max 3 rounds): send the after screenshot back in
   `mode:"check"` with the original intent. If not satisfied, **revert** the previous
   attempt (undo its history entries) and apply the model's **full corrected** plan.
6. `history.collapse` — the whole request becomes a single undoable step.

UI: the Quick Edit box routes multi-clause / "and ..." prompts to this executor and
streams `Step i/n: <op>` into its status line; a `(basic mode)` suffix marks the offline planner.

### Server route

`POST /api/editor/plan` (HARNESS, `routes/editor_agent_routes.py`)

```
{ prompt, state, mode?: "plan"|"check", screenshot?: "data:image/..." }
-> { reply, planner, steps: [{op, params}], done? }
```

Owner-scoped, authenticated. Uses the configured default/utility model with vision.
If the model call is unavailable it returns a deterministic `planner:"fallback"` plan.

---

## 3. Bridge (server agent drives an open editor)

`static/js/editor/editor-bridge.js` subscribes the open editor to the server queue.
Enabled automatically when the editor opens (`window.__geEditorBridge`).

- Subscribe: `GET /api/editor/bridge/events?session=<id>` — SSE, `data: {id,op,params}` (keepalive every 15s).
- The page runs the op via the control API and replies:
  `POST /api/editor/bridge/result {id,ok,result,error,screenshot,ms}`.
- Enqueue a command (server side): `POST /api/editor/bridge/command {session, op|steps, wait, timeout}`.
  With `wait:true` the call blocks and returns the editor's result.

Round-trip example (authenticated; `<session>` = `window.__geEditorBridge.session`):

```bash
curl -s -X POST http://127.0.0.1:7011/api/editor/bridge/command \
  -H 'Content-Type: application/json' -b "odysseus_session=$TOKEN" \
  -d '{"session":"default","op":"layers.addRaster","wait":true,"timeout":8000}'
# -> {"id":"...","ok":true,"result":{...},"screenshot":"data:image/png;base64,..."}
```

Session scoping is `owner:session`, so an agent can only drive editors owned by the
same user. The bridge is a **browser-only** flow: it requires an editor page to be open.

---

## 4. SAM dependency

`selection.subject` / `selection.face` / `selection.sky` / `selection.samText` use the
server SAM mask tool (`POST /api/image/mask`). When the SAM tools are **not installed**
the editor raises *"Object mask tools are not installed"* and the control op falls back
to a **coarse approximation** (centred rect / upper-centre ellipse / top-band colour range).
On a plain background the approximation is acceptable; on real photos it leaks, producing
visible rectangular/elliptical artifacts. Install the Cookbook SAM mask dependencies for
true subject/face/sky segmentation.

`selection.colorRange` and `selection.background` never need SAM (pure colour matching),
but they are also coarse on busy scenes.

---

## 5. Two ways for an agent to use this — pick one

### Option A — built-in tool (in-process)

Register an editor-control tool inside the app (same process as the model) that calls the
bridge/control API directly. Uses the app's normal login; nothing new is exposed. This is
the rejected stdio-MCP design's replacement: a server-side tool that talks to the bridge
needs either an internal token (auth-bypass) or to run in-process. **Decision pending (user).**

### Option B — browser-only flow (works today)

No new server tool. An external agent drives an open editor purely through the bridge
endpoints using the logged-in owner's session. Restricted to the editor the user has open.
This is the flow demonstrated above and requires **no** auth-bypass.

A stdio `photo_editor` MCP server was prototyped and **rejected** because the built-in MCP
subprocess has no internal token; wiring it would require expanding an auth bypass.
The two viable options above are left for the user to choose.

---

## 6. Known limitations / honest grading

Live run against real 7011 with the server LLM planner (7 prompts): all 7 executed
(`planner:"llm"`). Graded by eye:

- PASS: black & white portrait; warm cinematic; sharper + more vivid; text title.
- FAIL (region-masked, SAM-bound): "dramatic sky" (global over-saturation instead of a
  dramatic, masked sky), "blur the background" (coarse mask leaks; visible rectangle),
  "brighten the face" (coarse ellipse/rect artifact).

Whole-image edits are reliable; subject/face/sky region edits need SAM. The self-check
loop works (revert-and-replan) but the region cases spend all 3 iterations.

## 7. Theming (control kit)

The editor's interactive controls (ranges, number fields, selects, checkboxes) share a
single themed kit: `static/js/editor/control-kit.js` adds `.ge-ctl-*` classes scoped to
the editor roots (`#gallery-editor-container`, `.ge-adj-popup`, `.ge-filter-overlay`,
`.ge-canvas-size-overlay`, `.ge-float-window`, `.ge-transform-popup`), and
`static/css/13-image-editor.css` (section "Editor control kit") styles them from the app
design tokens (`static/css/00-tokens.css`) to match the app sliders/selects. It keeps
range fills in sync and attaches a **scrub-on-label** affordance to numeric fields
(drag the field's label to change the value; arrow keys work, Shift = x10).

**The PhotoCraft editor is dark-only.** `:root.light` flips the global tokens, but
`13-image-editor.css` has no `.light` handling and the editor relies on dark surfaces,
so light mode renders a garbled mix. Unlike the rest of Odysseus, the editor follows
Photoshop and is not offered in a light theme.
