# Shorts editor + AI focus: plan

Two features:

1. **Clip editor**: click a preview → a page with the full 16:9 clip, the window that becomes the short drawn on
   top of it, and a live 9:16 preview. Drag the window sideways, make it wider (13:16, 1:1, ... up to the whole
   16:9 frame, shown over a blurred copy of the frame), set keyframes, edit the hook, save → that clip re-renders.
2. **AI focus**: an optional note when starting a project ("the moments where they argue about money"). Empty →
   the current flow. Filled → the picker looks for those moments first and marks the clips that match.

## How the framing override works

The output is always 1080×1920. What changes is the *window* cut out of the source:

- `x`: centre of the window, fraction of the source width (as today)
- `a`: window shape, width/height. `0.5625` (9:16) fills the screen like today; anything wider is scaled to the
  short's full width and placed over the blurred background, at the same height as the existing "fit" mode
  (`reframe.fit_y`). `16/9` = the whole frame = today's fit mode.

Overrides live in `framing/<clip_id>.json`, apart from the analysis (`reframe/<clip_id>.json` stays untouched, so
"Reset to auto" is just deleting keys):

```json
{"keys": [{"f": 0, "x": 0.41, "a": 0.8125}, {"f": 96, "x": 0.55, "a": 0.8125}]}
```

- `f` = source frame inside the clip (the same frame numbers the plan and the renderer use).
- A shot (between two camera cuts) **with** keys is manual: `x` and `a` ease between its keys and hold before the
  first and after the last. A shot **without** keys stays automatic (face tracking / split / fit as today).
- At a camera cut the window jumps, as it does now.
- The renderer and the captions both read `framing.load_plan()` (analysis → smart layout → overrides), and the
  preview cache key includes the override file, so a save re-renders exactly that clip.

## Steps

### 1. Backend: framing overrides + renderer  ✔ when tests pass
- `models.Shot`: optional per-frame `a` (window aspect).
- `stages/framing.py`: load/save/validate overrides, apply them to a plan, `load_plan()` used by render + captions.
- `render.compose()`: a window wider than the output → scaled to full width over `blurred_bg()`.
- `reframe.visible_text_boxes()`: map burned-in text through a wide window (captions keep avoiding it).
- `render.clip_file()`: cache key includes the override file.
- Tests: apply/interpolate, compose sizes, text-box mapping, cache key changes.

### 2. Backend: editor API
- `GET  /jobs/{id}/clips/{cid}/editor`: fps, size, frame count, shots (mode, why, auto x path), face samples,
  pacing cuts, overrides, output + layout constants, neighbours (prev/next clip).
- `GET  /jobs/{id}/clips/{cid}/proxy`: small 16:9 H.264 of the clip range (built once, cached in `editor/`;
  browsers can't play every `.mkv`, and a short keyframe interval makes scrubbing smooth).
- `PUT  /jobs/{id}/clips/{cid}/framing`: validate + save keys, rebuild captions, queue that clip's render.
- `DELETE /jobs/{id}/clips/{cid}/framing`: back to automatic.

### 3. Web: editor page `/jobs/{id}/clips/{cid}`
- Clicking a clip's preview (or an Edit framing button) opens it.
- Left: 16:9 video, the window drawn over it (outside dimmed). Drag the middle = move, drag an edge = wider/narrower.
  Every drag sets a keyframe at the current frame.
- Right: live 9:16 preview drawn from the same video (canvas): blurred background, the window, the hook banner
  and the Shorts safe zones (toggle).
- Timeline: shots (cut marks, auto/manual), keyframes (click to jump, delete), pauses that pacing removes (hatched).
- Per shot: Reset to auto, "same window for the whole shot", apply the shape to the whole clip.
- Shape presets: 9:16, 3:4, 13:16, 4:5, 1:1, 16:9 (whole frame), plus a free drag.
- Side panel: hook text, banner on/off, title (same rules as the current editor), approve / reject.
- Keyboard: Space play/pause, ←/→ one frame (Shift: 1 s), K keyframe, Del remove keyframe, [ / ] previous/next
  shot, Ctrl+Z / Ctrl+Shift+Z undo/redo, Ctrl+S save.
- Unsaved-changes guard, prev/next clip.

### 4. AI focus prompt
- Home: "Tell the AI what to look for (optional)" under the link box; when it's open, pasting a link doesn't
  auto-start (so the note isn't skipped). Mode: *Prefer* (default: these first, plus the other strong moments) or
  *Only these*.
- Stored as `focus.json` in the project; `shorts make --focus "..."` for the CLI.
- Both Gemini passes get the note; the schema gains `matches_focus`; matches are ranked first and never dropped
  by `max_clips`; clips show a "Focus" badge.
- Project page: the note can be edited later → **Re-pick moments** (the transcript is reused).
- The prompt cache is keyed on the full request, so a different note never reuses old picks.

### 5. Wrap up
- `pytest`, `npm run build`, run it against a local project, README section, PR into `main`.

## Later (not in this pass)
- Up/down position and zoom (the key format has room: `y`, `z`).
- Switching a shot's layout (split / pip) from the editor.
- Trimming the clip's start/end and editing which pauses are cut.
