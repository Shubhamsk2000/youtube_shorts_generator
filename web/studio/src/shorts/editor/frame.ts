/** Window math for the clip editor. Mirrors src/shorts/stages/framing.py so the live preview matches the render. */
import type { CamBox, CamView, EditorData, EditorShot, FrameKey } from "../api";

export const FILL = 9 / 16;

export type Win = {
  x: number;          // centre, fraction of the source width
  a: number;          // width / height
  kind: "crop" | "wide" | "fit" | "split";
  manual: boolean;    // set in the editor (else the automatic framing)
  follow: boolean;    // x comes from the automatic path (a key without x)
  cam?: CamView;      // kind "split": the facecam layout is drawn, the window is its gameplay part
};

/** Shape of the gameplay area of a facecam layout (the narrowest gameplay window there is). */
export const areaAspect = (cv: CamView) => FILL / Math.max(0.05, cv.area[1]);

export const PRESETS: { label: string; a: number | "max"; hint: string }[] = [
  { label: "9:16", a: FILL, hint: "Fills the screen (like the automatic crop)" },
  { label: "3:4", a: 3 / 4, hint: "A bit wider, thin blurred bands" },
  { label: "4:5", a: 4 / 5, hint: "Wider" },
  { label: "13:16", a: 13 / 16, hint: "Wider" },
  { label: "1:1", a: 1, hint: "Square window over blur" },
  { label: "4:3", a: 4 / 3, hint: "Most of the frame" },
  { label: "16:9", a: "max", hint: "The whole frame over blur" },
];

export const presetValue = (p: (typeof PRESETS)[number], d: EditorData) => (p.a === "max" ? d.layout.max_aspect : p.a);

/** Window width as a fraction of the source width. */
export const winWidth = (a: number, d: Pick<EditorData, "src_w" | "src_h">) => Math.min(1, (a * d.src_h) / d.src_w);

export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function clampA(a: number, d: EditorData) {
  return clamp(a, FILL, Math.max(FILL, d.layout.max_aspect));
}

export function clampX(x: number, a: number, d: EditorData) {
  const half = winWidth(a, d) / 2;
  return clamp(x, half, 1 - half);
}

export const shotIndexAt = (shots: EditorShot[], f: number) => {
  for (let i = 0; i < shots.length; i++) if (shots[i].start <= f && f < shots[i].end) return i;
  return shots.length - 1;
};

/** The shot's range for keys: the last shot also takes keys past the analysed frames. */
export function shotRange(d: EditorData, i: number): [number, number] {
  const s = d.shots[i];
  return [s.start, i === d.shots.length - 1 ? Math.max(s.end, d.n_frames) : s.end];
}

export function keysInShot(d: EditorData, keys: FrameKey[], i: number) {
  const [a, b] = shotRange(d, i);
  return keys.filter((k) => a <= k.f && k.f < b);
}

const ease = (t: number) => t * t * (3 - 2 * t);

function autoX(s: EditorShot, f: number) {
  const usable = s.x && s.x.length && (s.mode === "track" || s.mode === "center" || s.mode === "manual");
  return usable ? s.x![clamp(f - s.start, 0, s.x!.length - 1)] : 0.5;
}

/** The automatic window at frame f (what the render does without keys). */
export function autoWin(d: EditorData, f: number): Win {
  const s = d.shots[shotIndexAt(d.shots, f)];
  if (s.mode === "fit") return { x: 0.5, a: d.layout.max_aspect, kind: "fit", manual: false, follow: false };
  if (s.mode === "split" && s.cams.length && d.layout.screen_layout !== "track") {
    const cv = s.cam_view;
    return cv
      ? { x: cv.screen_x, a: cv.screen_a, kind: "split", manual: false, follow: false, cam: cv }
      : { x: 0.5, a: FILL, kind: "split", manual: false, follow: false };
  }
  return { x: autoX(s, f), a: FILL, kind: "crop", manual: false, follow: false };
}

/** The window drawn at frame f: manual keys in its shot, else the automatic framing. keepCam: in a facecam
 * shot the keys set only the gameplay window and the cam stays (else they frame the whole short). */
export function windowAt(d: EditorData, keys: FrameKey[], f: number, keepCam = true): Win {
  const i = shotIndexAt(d.shots, f);
  const mine = keysInShot(d, keys, i).sort((p, q) => p.f - q.f);
  if (!mine.length) return autoWin(d, f);
  const s = d.shots[i];
  const cv = keepCam && d.layout.screen_layout !== "track" ? s.cam_view : null;
  const autoAt = (g: number) => (cv ? cv.screen_x : autoX(s, g));
  let j = 0;
  while (j + 1 < mine.length && mine[j + 1].f <= f) j++;
  const k0 = mine[j];
  const x0 = k0.x ?? autoAt(f);
  let x = x0;
  let a = k0.a;
  let follow = k0.x == null;
  if (!(f <= k0.f || j + 1 >= mine.length)) {
    const k1 = mine[j + 1];
    const t = ease((f - k0.f) / (k1.f - k0.f));
    const x1 = k1.x ?? autoAt(f);
    x = x0 + (x1 - x0) * t;
    a = k0.a + (k1.a - k0.a) * t;
    follow = k0.x == null && k1.x == null;
  }
  a = clampA(a, d);
  if (cv) {
    a = Math.min(Math.max(a, areaAspect(cv)), Math.max(FILL, d.layout.max_aspect)); // never narrower than its area
    return { x: clampX(x, a, d), a, kind: "split", manual: true, follow, cam: cv };
  }
  return { x: clampX(x, a, d), a, kind: a > FILL * 1.01 ? "wide" : "crop", manual: true, follow };
}

/** Shape (width / height, in pixels) of the slot the facecam fills: the whole panel on top (one cam once
 * you set the frame), or the corner cam. Mirrors render.split_geometry / reframe.pip_geometry. */
export function camAspect(cv: CamView, d: Pick<EditorData, "layout">) {
  const t = cv.tiles[0];
  if (!t) return 16 / 9;
  const W = d.layout.out_w;
  const H = d.layout.out_h;
  return cv.pip ? (t.dst[2] * W) / Math.max(1, t.dst[3] * H) : W / Math.max(1, t.dst[3] * H);
}

/** The facecam frame as a source box [x0, y0, x1, y1] (fractions), in the slot's shape, inside the frame.
 * Mirrors reframe.cam_box_crop. */
export function camBoxSrc(b: CamBox, aspect: number, d: Pick<EditorData, "src_w" | "src_h">): number[] {
  let h = clamp(b.h, 0.04, 1);
  let w = (h * d.src_h * aspect) / d.src_w;
  if (w > 1) {
    w = 1;
    h = d.src_w / aspect / d.src_h;
  }
  const x0 = clamp(b.x - w / 2, 0, 1 - w);
  const y0 = clamp(b.y - h / 2, 0, 1 - h);
  return [x0, y0, x0 + w, y0 + h];
}

/** A source box -> the frame that draws it (centre + height). */
export const camBoxOf = (src: number[]): CamBox => ({ x: (src[0] + src[2]) / 2, y: (src[1] + src[3]) / 2, h: src[3] - src[1] });

/** The layout with your facecam frame: one cam across the panel (or in the corner), from that box. */
export function withCamBox(cv: CamView, b: CamBox | null, d: EditorData): CamView {
  if (!b || !cv.tiles.length) return cv;
  const src = camBoxSrc(b, camAspect(cv, d), d);
  const dst = cv.pip ? cv.tiles[0].dst : [0, 0, 1, cv.tiles[0].dst[3]];
  return { ...cv, tiles: [{ src, dst }] };
}

/** Insert or replace the key on frame f. */
export function putKey(keys: FrameKey[], k: FrameKey): FrameKey[] {
  return [...keys.filter((q) => q.f !== k.f), k].sort((p, q) => p.f - q.f);
}

export function aspectLabel(a: number, d: EditorData) {
  for (const p of PRESETS) if (Math.abs(presetValue(p, d) - a) < 0.006) return p.label;
  return `${(a * 16).toFixed(1).replace(/\.0$/, "")}:16`;
}

/** Snap to a preset shape when close (dragging an edge). */
export function snapA(a: number, d: EditorData) {
  for (const p of PRESETS) {
    const v = presetValue(p, d);
    if (Math.abs(v - a) < 0.025) return v;
  }
  return a;
}

/** Clip-relative seconds -> is it in a pause that pacing removes? Returns the next kept start if so. */
export function skipTarget(keep: [number, number][] | null, t: number): number | null {
  if (!keep || !keep.length) return null;
  for (const [a, b] of keep) {
    if (t < a - 0.02) return a;
    if (t < b) return null;
  }
  return null;
}

/** The gaps pacing cuts out, for the timeline. */
export function removedSpans(keep: [number, number][] | null, dur: number): [number, number][] {
  if (!keep || !keep.length) return [];
  const out: [number, number][] = [];
  let t = 0;
  for (const [a, b] of keep) {
    if (a - t > 0.03) out.push([t, a]);
    t = b;
  }
  if (dur - t > 0.03) out.push([t, dur]);
  return out;
}

export const modeLabel: Record<EditorShot["mode"], string> = {
  track: "Face tracking",
  center: "Centre crop",
  fit: "Whole frame",
  split: "Facecam layout",
  manual: "Manual",
};
