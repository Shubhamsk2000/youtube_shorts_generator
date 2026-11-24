import { useEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import type { EditorCaptions, EditorData } from "../api";
import { FILL, clamp, type Win } from "./frame";

export type PreviewOpts = {
  safe: boolean;
  hookText: string;
  showHook: boolean;
  hookY: number;        // banner's vertical centre, fraction of the height
  hookAlways?: boolean; // show it past its first seconds (while it's being placed)
  captions?: boolean;   // draw the captions (default on)
};

/** Where the hook banner was drawn (fractions of the height), for dragging it. */
export type HookBox = { top: number; bottom: number };

/** Shorts UI that covers the video (approximate, 1080x1920 fractions): top bar, bottom text, right buttons. */
const SAFE = [
  { x: 0, y: 0, w: 1, h: 0.075 },
  { x: 0, y: 0.8, w: 1, h: 0.2 },
  { x: 0.86, y: 0.42, w: 0.14, h: 0.38 },
];

let small: HTMLCanvasElement | null = null;

function wrap(text: string, max: number): string[] {
  const lines: string[] = [];
  let cur = "";
  for (const w of text.split(/\s+/).filter(Boolean)) {
    if (cur && (cur + " " + w).length > max) {
      lines.push(cur);
      cur = w;
    } else cur = cur ? `${cur} ${w}` : w;
  }
  if (cur) lines.push(cur);
  return lines;
}

/** Blurred, dimmed fill of the rectangle: the centre of the frame in its shape, drawn tiny and scaled up. */
function blurFill(ctx: CanvasRenderingContext2D, video: HTMLVideoElement, d: EditorData, x: number, y: number, w: number, h: number) {
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  small ??= document.createElement("canvas");
  small.width = Math.max(1, Math.round(w / 14));
  small.height = Math.max(1, Math.round(h / 14));
  const sctx = small.getContext("2d")!;
  const bw = Math.min(vw, vh * (w / h));
  const bh = Math.min(vh, bw / (w / h));
  sctx.drawImage(video, (vw - bw) / 2, (vh - bh) / 2, bw, bh, 0, 0, small.width, small.height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(small, x, y, w, h);
  ctx.fillStyle = `rgba(0,0,0,${1 - d.layout.fit_dim})`;
  ctx.fillRect(x, y, w, h);
}

/** Facecam layout (mirrors render.compose_split / compose_pip): the gameplay window fills its area (or sits
 * full width over blur when wider), then the cam panel on top or the corner cam(s). */
function drawCamLayout(ctx: CanvasRenderingContext2D, video: HTMLVideoElement, d: EditorData, win: Win, W: number, H: number) {
  const cv = win.cam!;
  const vw = video.videoWidth;
  const vh = video.videoHeight;
  if (cv.sized && cv.game) {  // window sizes from the editor: each window centred over a blurred fill
    blurFill(ctx, video, d, 0, 0, W, H);
    for (const { src, dst } of cv.tiles) {
      ctx.drawImage(video, src[0] * vw, src[1] * vh, (src[2] - src[0]) * vw, (src[3] - src[1]) * vh,
        dst[0] * W, dst[1] * H, dst[2] * W, dst[3] * H);
    }
    const [gx, gy, gw, gh] = [cv.game[0] * W, cv.game[1] * H, cv.game[2] * W, cv.game[3] * H];
    const e = gw / Math.max(1, gh);
    const cw = Math.min(vw, vh * e);
    const ch = Math.min(vh, cw / e);
    const left = clamp(win.x * vw - cw / 2, 0, vw - cw);
    ctx.drawImage(video, left, (vh - ch) / 2, cw, ch, gx, gy, gw, gh);
    return;
  }
  const top = cv.area[0] * H;
  const ah = cv.area[1] * H;
  const e = Math.max(win.a, W / ah);
  const cw = Math.min(vw, vh * e);
  const ch = Math.min(vh, cw / e);
  const left = clamp(win.x * vw - cw / 2, 0, vw - cw);
  const sy = (vh - ch) / 2;
  const bandH = Math.min(ah, W / (cw / ch));
  if (bandH >= ah - 0.5) ctx.drawImage(video, left, sy, cw, ch, 0, top, W, ah);
  else {
    blurFill(ctx, video, d, 0, top, W, ah);
    ctx.drawImage(video, left, sy, cw, ch, 0, top + (ah - bandH) / 2, W, bandH);
  }
  for (const { src, dst } of cv.tiles) {
    const [x, y, w, h] = [dst[0] * W, dst[1] * H, dst[2] * W, dst[3] * H];
    const args = [src[0] * vw, src[1] * vh, (src[2] - src[0]) * vw, (src[3] - src[1]) * vh, x, y, w, h] as const;
    if (!cv.pip) {
      ctx.drawImage(video, ...args);
      continue;
    }
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, w * 0.08);
    ctx.clip();
    ctx.drawImage(video, ...args);
    ctx.restore();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = Math.max(1, W / 270);
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, w * 0.08);
    ctx.stroke();
  }
}

const capFont = (px: number) => `900 ${px}px Inter, "Geist", system-ui, sans-serif`;

/** The caption line on screen at clip time t, as captions.to_ass burns it in: the spoken word in the highlight
 * colour and a little bigger, AI-flagged words in the emphasis colour, black outline + soft shadow. */
export function drawCaptions(ctx: CanvasRenderingContext2D, c: EditorCaptions, t: number, W: number, H: number) {
  const line = c.lines?.find((l) => t >= l.start && t < l.end); // lines: missing from a server older than this page
  if (!line || !line.words.length) return;
  const k = H / 1920;
  const size = c.size * k;
  let active = 0;
  for (let i = 1; i < line.words.length; i++) if (line.words[i].start <= t) active = i;
  const pop = c.pop_scale * (line.words[active].emphasis ? 1.06 : 1);
  ctx.font = capFont(size);
  const space = ctx.measureText(" ").width;
  const words = line.words.map((w, i) => ({ ...w, i, s: i === active ? pop : 1, w: ctx.measureText(w.text).width * (i === active ? pop : 1) }));
  // wrap like libass inside the style's 70 px side margins
  const maxW = W - 140 * k;
  const rows: (typeof words)[] = [];
  let cur: typeof words = [];
  let curW = 0;
  for (const w of words) {
    const add = (cur.length ? space : 0) + w.w;
    if (cur.length && curW + add > maxW) {
      rows.push(cur);
      cur = [];
      curW = 0;
    }
    cur.push(w);
    curW += (cur.length > 1 ? space : 0) + w.w;
  }
  if (cur.length) rows.push(cur);
  const lh = size * 1.12;
  const blockH = lh * rows.length;
  const cy = line.y != null ? line.y * H : c.position * H - blockH / 2; // default: bottom edge at `position`
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  ctx.miterLimit = 2;
  rows.forEach((row, r) => {
    const rowW = row.reduce((a, w) => a + w.w, 0) + space * (row.length - 1);
    let x = W / 2 - rowW / 2;
    const y = cy - blockH / 2 + lh * (r + 0.5);
    for (const w of row) {
      const cx = x + w.w / 2;
      const color = w.i === active ? c.highlight_color : w.emphasis ? c.emphasis_color : c.text_color;
      ctx.save();
      ctx.translate(cx, y);
      ctx.scale(w.s, w.s);
      ctx.font = capFont(size);
      if (c.shadow > 0) {
        const off = c.shadow * k / w.s;
        ctx.fillStyle = "rgba(0,0,0,0.6)";
        ctx.strokeStyle = "rgba(0,0,0,0.6)";
        ctx.lineWidth = (2 * c.outline * k) / w.s;
        ctx.strokeText(w.text, off, off);
        ctx.fillText(w.text, off, off);
      }
      if (c.outline > 0) {
        ctx.strokeStyle = "#000";
        ctx.lineWidth = (2 * c.outline * k) / w.s;
        ctx.strokeText(w.text, 0, 0);
      }
      ctx.fillStyle = color;
      ctx.fillText(w.text, 0, 0);
      ctx.restore();
      x += w.w + space;
    }
  });
}

/** One frame of the short: the window (full screen, or full width over blur), captions, hook banner, guides. */
export function drawFrame(ctx: CanvasRenderingContext2D, video: HTMLVideoElement | null, d: EditorData, win: Win, t: number, o: PreviewOpts): HookBox | null {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  if (!video || video.readyState < 2 || !video.videoWidth) return null;
  const vw = video.videoWidth;
  const vh = video.videoHeight;

  if (win.cam) {
    drawCamLayout(ctx, video, d, win, W, H);
  } else if (win.kind === "crop") {
    const cw = Math.min(vw, vh * FILL);
    const left = clamp(win.x * vw - cw / 2, 0, vw - cw);
    ctx.drawImage(video, left, 0, cw, vh, 0, 0, W, H);
  } else {
    blurFill(ctx, video, d, 0, 0, W, H);
    const a = win.kind === "split" ? d.layout.max_aspect : win.a;
    const cw = Math.min(vw, vh * a);
    const left = clamp(win.x * vw - cw / 2, 0, vw - cw);
    const bandH = Math.min(H, W / (cw / vh));
    const y = clamp(d.layout.fit_y * H - bandH / 2, 0, H - bandH);
    ctx.drawImage(video, left, 0, cw, vh, 0, y, W, bandH);
  }

  if (d.captions.enabled && o.captions !== false) drawCaptions(ctx, d.captions, t, W, H);

  const hook = d.hook;
  let box: HookBox | null = null;
  if (hook.enabled && o.showHook && o.hookText && (t < hook.duration_s || o.hookAlways)) {
    const size = (hook.size / 1920) * H;
    ctx.font = `900 ${size}px Inter, "Geist", system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const lines = wrap(o.hookText, hook.max_chars_per_line);
    const lh = size * 1.18;
    const boxW = Math.min(W * 0.92, Math.max(...lines.map((l) => ctx.measureText(l).width)) + size * 0.9);
    const boxH = lh * lines.length + size * 0.5;
    const cy = clamp(o.hookY * H, boxH / 2, H - boxH / 2); // the render keeps the box inside the frame too
    box = { top: (cy - boxH / 2) / H, bottom: (cy + boxH / 2) / H };
    ctx.globalAlpha = t < hook.duration_s ? 1 : 0.75; // past its seconds: shown only to place it
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.roundRect(W / 2 - boxW / 2, cy - boxH / 2, boxW, boxH, size * 0.25);
    ctx.fill();
    ctx.fillStyle = "#000";
    lines.forEach((l, i) => ctx.fillText(l, W / 2, cy - ((lines.length - 1) * lh) / 2 + i * lh));
    ctx.globalAlpha = 1;
  }

  if (o.safe) {
    ctx.fillStyle = "rgba(255, 64, 64, 0.22)";
    for (const r of SAFE) ctx.fillRect(r.x * W, r.y * H, r.w * W, r.h * H);
    if (d.captions.enabled) {
      ctx.strokeStyle = "rgba(255,255,255,0.8)";
      ctx.setLineDash([6, 5]);
      ctx.lineWidth = 1.5;
      const y = d.captions.position * H;
      ctx.beginPath();
      ctx.moveTo(W * 0.08, y);
      ctx.lineTo(W * 0.92, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.font = `${Math.round(H * 0.016)}px system-ui, sans-serif`;
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText("captions", W * 0.08, y - 3);
    }
  }
  return box;
}

/** The 9:16 canvas, redrawn by the page (it owns the clock). With onHookDrag, the hook banner can be
 * dragged up or down (y = the banner's new centre, fraction of the height). */
export function Preview({ draw, onHookDrag }: {
  draw: (ctx: CanvasRenderingContext2D) => HookBox | null | void;
  onHookDrag?: (y: number, phase: "start" | "move" | "end") => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  const box = useRef<HookBox | null>(null);
  const grab = useRef<number | null>(null); // pointer offset from the banner's centre while dragging
  const [over, setOver] = useState(false);
  const fracY = (e: RPointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return (e.clientY - r.top) / r.height;
  };
  const onBox = (y: number) => !!box.current && y >= box.current.top - 0.01 && y <= box.current.bottom + 0.01;
  useEffect(() => {
    let id = 0;
    const loop = () => {
      const c = ref.current;
      if (c) {
        const dpr = Math.min(2, window.devicePixelRatio || 1);
        const w = Math.round(c.clientWidth * dpr);
        const h = Math.round(c.clientHeight * dpr);
        if (w && h && (c.width !== w || c.height !== h)) {
          c.width = w;
          c.height = h;
        }
        const ctx = c.getContext("2d");
        if (ctx && w && h) box.current = drawRef.current(ctx) || null;
      }
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, []);
  return (
    <canvas ref={ref} className="block aspect-[9/16] w-full touch-none rounded-xl bg-black" aria-label="Live 9:16 preview"
      style={{ cursor: onHookDrag && (over || grab.current != null) ? "ns-resize" : undefined }}
      title={onHookDrag && over ? "Drag the hook banner up or down" : undefined}
      onPointerDown={(e) => {
        if (!onHookDrag || e.button !== 0) return;
        const y = fracY(e);
        if (!onBox(y)) return;
        const b = box.current!;
        grab.current = y - (b.top + b.bottom) / 2;
        e.currentTarget.setPointerCapture(e.pointerId);
        onHookDrag((b.top + b.bottom) / 2, "start");
      }}
      onPointerMove={(e) => {
        const y = fracY(e);
        if (grab.current == null) return setOver(!!onHookDrag && onBox(y));
        onHookDrag?.(clamp(y - grab.current, 0.04, 0.96), "move");
      }}
      onPointerUp={(e) => {
        if (grab.current == null) return;
        onHookDrag?.(clamp(fracY(e) - grab.current, 0.04, 0.96), "end");
        grab.current = null;
      }}
      onPointerCancel={() => { if (grab.current != null) { grab.current = null; onHookDrag?.(-1, "end"); } }}
      onPointerLeave={() => setOver(false)} />
  );
}
