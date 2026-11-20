import { useEffect, useRef } from "react";
import type { EditorData } from "../api";
import { FILL, clamp, type Win } from "./frame";

export type PreviewOpts = { safe: boolean; hookText: string; showHook: boolean };

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

/** One frame of the short: the window (full screen, or full width over blur), hook banner, guides. */
export function drawFrame(ctx: CanvasRenderingContext2D, video: HTMLVideoElement | null, d: EditorData, win: Win, t: number, o: PreviewOpts) {
  const W = ctx.canvas.width;
  const H = ctx.canvas.height;
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, W, H);
  if (!video || video.readyState < 2 || !video.videoWidth) return;
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

  const hook = d.hook;
  if (hook.enabled && o.showHook && o.hookText && t < hook.duration_s) {
    const size = (hook.size / 1920) * H;
    ctx.font = `900 ${size}px Inter, "Geist", system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    const lines = wrap(o.hookText, hook.max_chars_per_line);
    const lh = size * 1.18;
    const boxW = Math.min(W * 0.92, Math.max(...lines.map((l) => ctx.measureText(l).width)) + size * 0.9);
    const boxH = lh * lines.length + size * 0.5;
    const cy = hook.position * H;
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.roundRect(W / 2 - boxW / 2, cy - boxH / 2, boxW, boxH, size * 0.25);
    ctx.fill();
    ctx.fillStyle = "#000";
    lines.forEach((l, i) => ctx.fillText(l, W / 2, cy - ((lines.length - 1) * lh) / 2 + i * lh));
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
}

/** The 9:16 canvas, redrawn by the page (it owns the clock). */
export function Preview({ draw }: { draw: (ctx: CanvasRenderingContext2D) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const drawRef = useRef(draw);
  drawRef.current = draw;
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
        if (ctx && w && h) drawRef.current(ctx);
      }
      id = requestAnimationFrame(loop);
    };
    id = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(id);
  }, []);
  return <canvas ref={ref} className="block aspect-[9/16] w-full rounded-xl bg-black" aria-label="Live 9:16 preview" />;
}
