import { useRef, type PointerEvent as RPointerEvent, type RefObject } from "react";
import type { CamBox, EditorData } from "../api";
import { cx } from "../lib";
import { FILL, aspectLabel, camAspect, camBoxOf, camBoxSrc, clamp, clampA, clampX, snapA, winWidth, type Win } from "./frame";

export type DragMode = "move" | "l" | "r";

/**
 * The full 16:9 clip with the window that becomes the short drawn on top. Drag inside it to move it, drag an
 * edge to make it wider or narrower (snaps to the presets; hold Shift for a free shape), click outside it to
 * centre it there.
 */
type CamDrag = { mode: "move" | "tl" | "tr" | "bl" | "br"; px: number; py: number; box: number[]; aspect: number };

/**
 * The facecam frame: the part of the video that goes into the cam slot (the panel on top, or the corner cam).
 * Drag it to move it, drag a corner to resize it; its shape stays the slot's shape.
 */
function CamFrames({ d, win, custom, stage, onBegin, onCam }: {
  d: EditorData;
  win: Win;
  custom: boolean;
  stage: RefObject<HTMLDivElement | null>;
  onBegin: () => void;
  onCam: (b: CamBox) => void;
}) {
  const drag = useRef<CamDrag | null>(null);
  const cv = win.cam!;
  const aspect = camAspect(cv, d);

  function at(e: RPointerEvent) {
    const r = stage.current!.getBoundingClientRect();
    return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height] as const;
  }

  function start(e: RPointerEvent, mode: CamDrag["mode"], src: number[]) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    const [px, py] = at(e);
    // a 2-cam layout or a corner cam's own shape: the first touch gives the frame the slot's shape
    const box = camBoxSrc(camBoxOf(src), aspect, d);
    drag.current = { mode, px, py, box, aspect };
    onBegin();
  }

  function move(e: RPointerEvent) {
    const g = drag.current;
    if (!g) return;
    const [px, py] = at(e);
    const [x0, y0, x1, y1] = g.box;
    if (g.mode === "move") {
      const b = camBoxOf(g.box);
      onCam(camBoxOf(camBoxSrc({ x: b.x + px - g.px, y: b.y + py - g.py, h: b.h }, g.aspect, d)));
      return;
    }
    // a corner: the opposite corner stays put, the shape stays the slot's
    const sx = g.mode.endsWith("r") ? 1 : -1;
    const sy = g.mode.startsWith("b") ? 1 : -1;
    const ax = sx > 0 ? x0 : x1;
    const ay = sy > 0 ? y0 : y1;
    const k = d.src_w / (d.src_h * g.aspect); // height per unit of width (fractions)
    const roomH = sy > 0 ? 1 - ay : ay;
    const roomW = sx > 0 ? 1 - ax : ax;
    const h = clamp(Math.max(Math.abs(py - ay), Math.abs(px - ax) * k), 0.04, Math.min(roomH, roomW * k));
    const w = h / k;
    onCam({ x: ax + (sx * w) / 2, y: ay + (sy * h) / 2, h });
  }

  function end() {
    drag.current = null;
  }

  return (
    <>
      {cv.tiles.map(({ src }, i) => (
        <div key={i}
          role="group"
          aria-label="Facecam frame"
          title="Facecam: drag to move, drag a corner to resize"
          onPointerDown={(e) => start(e, "move", src)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
          className={cx("absolute cursor-move border-2 border-fuchsia-400 shadow-[0_0_0_1px_rgba(0,0,0,0.5)]",
            custom ? "bg-fuchsia-400/10" : "bg-fuchsia-400/5")}
          style={{ left: `${src[0] * 100}%`, top: `${src[1] * 100}%`, width: `${(src[2] - src[0]) * 100}%`, height: `${(src[3] - src[1]) * 100}%` }}>
          <span className="pointer-events-none absolute top-1 left-1 rounded bg-fuchsia-500 px-1.5 py-0.5 font-mono text-[10px] font-semibold whitespace-nowrap text-white">
            facecam{custom ? "" : " · auto"}
          </span>
          {(["tl", "tr", "bl", "br"] as const).map((m) => (
            <div key={m}
              onPointerDown={(e) => start(e, m, src)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
              className={cx("absolute size-4", m[0] === "t" ? "-top-2" : "-bottom-2", m[1] === "l" ? "-left-2" : "-right-2",
                m === "tl" || m === "br" ? "cursor-nwse-resize" : "cursor-nesw-resize")}>
              <div className="absolute top-1/2 left-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-fuchsia-500 bg-white shadow" />
            </div>
          ))}
        </div>
      ))}
    </>
  );
}

export function Stage({ d, video, win, faces, cams, keepCam, camCustom, onBegin, onChange, onEnd, onCam, src }: {
  d: EditorData;
  cams: number[][];                             // the current shot's facecam boxes
  keepCam: boolean;                             // facecam shots: the window is only the gameplay part
  camCustom: boolean;                           // the facecam frame was set in the editor
  onCam: (b: CamBox) => void;                   // the facecam frame moved / resized (after onBegin)
  video: RefObject<HTMLVideoElement | null>;
  win: Win;
  faces: number[][] | null;
  src: string;
  onBegin: () => void;                          // a drag starts (pause, one undo step)
  onChange: (x: number | null, a: number) => void;  // x null = keep following the automatic framing
  onEnd: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ mode: DragMode; x0: number; w: number; left: number; cw: number; x: number; a: number; follow: boolean } | null>(null);

  const split = win.kind === "split";           // the facecam layout is drawn (automatic, or kept with your window)
  const autoCam = split && !win.manual;
  // the shape a first touch gives the window: the gameplay part (cam kept), or a 9:16 crop of the whole short
  const touchA = autoCam && !keepCam ? FILL : win.a;
  const cw = winWidth(touchA, d);
  const left = clamp(win.x - cw / 2, 0, 1 - cw);

  function start(e: RPointerEvent, mode: DragMode) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = box.current!.getBoundingClientRect();
    (e.target as Element).setPointerCapture(e.pointerId);
    const a = touchA;
    // an automatic facecam shot: a resize keeps the automatic position (with the cam kept)
    const follow = (autoCam ? keepCam : win.follow) && mode !== "move";
    drag.current = { mode, x0: e.clientX, w: rect.width, left, cw, x: win.x, a, follow };
    onBegin();
    if (autoCam) onChange(follow ? null : clampX(win.x, a, d), a);  // the first touch makes it a window
  }

  function move(e: RPointerEvent) {
    const g = drag.current;
    if (!g) return;
    const dx = (e.clientX - g.x0) / g.w;
    let x = g.x;
    let a = g.a;
    if (g.mode === "move") {
      x = g.x + dx;
    } else {
      // an edge: the opposite edge stays put (unless the window follows the faces: then it grows evenly)
      const grow = g.mode === "r" ? dx : -dx;
      const ncw = clamp(g.cw + (g.follow ? 2 * grow : grow), winWidth(FILL, d), 1);
      a = clampA((ncw * d.src_w) / d.src_h, d);
      if (!e.shiftKey) a = snapA(a, d);
      const real = winWidth(a, d);
      x = g.follow ? g.x : g.mode === "r" ? g.left + real / 2 : g.left + g.cw - real / 2;
    }
    onChange(g.follow ? null : clampX(x, a, d), a);
  }

  function end() {
    if (!drag.current) return;
    drag.current = null;
    onEnd();
  }

  function clickOutside(e: RPointerEvent) {
    if (e.button !== 0 || drag.current) return;
    const rect = box.current!.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const a = touchA;
    onBegin();
    onChange(clampX(x, a, d), a);
    onEnd();
  }

  return (
    <div ref={box} className="relative w-full touch-none overflow-hidden rounded-xl bg-black select-none" style={{ aspectRatio: `${d.src_w} / ${d.src_h}` }}>
      <video ref={video} src={src} playsInline preload="auto" className="absolute inset-0 size-full" />

      {/* outside the window: dimmed, click to move the window there */}
      <div className="absolute inset-0 cursor-crosshair" onPointerDown={clickOutside} />
      {!(autoCam && !keepCam) && (
        <>
          <div className="pointer-events-none absolute inset-y-0 left-0 bg-black/60" style={{ width: `${left * 100}%` }} />
          <div className="pointer-events-none absolute inset-y-0 right-0 bg-black/60" style={{ width: `${(1 - left - cw) * 100}%` }} />
        </>
      )}

      {split && (
        <div className="pointer-events-none absolute inset-0">
          {cams.map((c, i) => (
            <div key={i} className="absolute rounded-sm border border-dashed border-fuchsia-300/60"
              style={{ left: `${c[0] * 100}%`, top: `${c[1] * 100}%`, width: `${(c[2] - c[0]) * 100}%`, height: `${(c[3] - c[1]) * 100}%` }} />
          ))}
          <span className="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-md bg-black/70 px-2 py-1 text-[11px] font-medium whitespace-nowrap text-white backdrop-blur">
            {keepCam
              ? "Pink frame: the facecam · white frame: the gameplay"
              : autoCam ? "Facecam layout (automatic) · drag to replace it with your own window" : "Facecam layout"}
          </span>
        </div>
      )}

      {faces?.map((f, i) => (
        <div key={i} className="pointer-events-none absolute rounded-sm border border-white/70"
          style={{ left: `${(f[0] - f[2] / 2) * 100}%`, top: `${(f[1] - f[3] / 2) * 100}%`, width: `${f[2] * 100}%`, height: `${f[3] * 100}%` }} />
      ))}

      <div
        role="slider"
        aria-label="Short window position"
        aria-valuenow={Math.round(win.x * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        onPointerDown={(e) => start(e, "move")}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        className={cx(
          "absolute inset-y-0 cursor-grab active:cursor-grabbing",
          autoCam && !keepCam ? "border-2 border-dashed border-white/40" : win.manual ? "border-2 border-accent shadow-[0_0_0_1px_rgba(0,0,0,0.4)]" : "border-2 border-white/85",
        )}
        style={{ left: `${left * 100}%`, width: `${cw * 100}%` }}
      >
        {!(autoCam && !keepCam) && (
          <span className={cx("pointer-events-none absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold text-white",
            win.manual ? "bg-accent" : "bg-black/60")}>
            {split && "gameplay "}{aspectLabel(win.a, d)}{win.manual ? (win.follow ? (split ? " · auto position" : " · follows faces") : "") : " · auto"}
          </span>
        )}
        {(["l", "r"] as const).map((m) => (
          <div key={m}
            onPointerDown={(e) => start(e, m)} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
            title="Drag to make the window wider or narrower (Shift: no snapping)"
            className={cx("absolute inset-y-0 w-4 cursor-ew-resize", m === "l" ? "-left-2" : "-right-2")}>
            <div className="absolute top-1/2 left-1/2 h-12 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white shadow" />
          </div>
        ))}
      </div>

      {split && win.cam && (
        <CamFrames d={d} win={win} custom={camCustom} stage={box} onBegin={onBegin} onCam={onCam} />
      )}
    </div>
  );
}
