import { useRef, type PointerEvent as RPointerEvent, type RefObject } from "react";
import type { EditorData } from "../api";
import { cx } from "../lib";
import { FILL, aspectLabel, clamp, clampA, clampX, snapA, winWidth, type Win } from "./frame";

export type DragMode = "move" | "l" | "r";

/**
 * The full 16:9 clip with the window that becomes the short drawn on top. Drag inside it to move it, drag an
 * edge to make it wider or narrower (snaps to the presets; hold Shift for a free shape), click outside it to
 * centre it there.
 */
export function Stage({ d, video, win, faces, cams, onBegin, onChange, onEnd, src }: {
  d: EditorData;
  cams: number[][];                             // the current shot's facecam boxes
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

  const split = win.kind === "split";
  const cw = winWidth(split ? FILL : win.a, d);
  const left = clamp(win.x - cw / 2, 0, 1 - cw);

  function start(e: RPointerEvent, mode: DragMode) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = box.current!.getBoundingClientRect();
    (e.target as Element).setPointerCapture(e.pointerId);
    const a = split ? FILL : win.a;
    drag.current = { mode, x0: e.clientX, w: rect.width, left, cw, x: win.x, a, follow: win.follow && mode !== "move" };
    onBegin();
    if (split) onChange(win.x, a);  // the facecam layout becomes a window on the first touch
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
    const a = split ? FILL : win.a;
    onBegin();
    onChange(clampX(x, a, d), a);
    onEnd();
  }

  return (
    <div ref={box} className="relative w-full touch-none overflow-hidden rounded-xl bg-black select-none" style={{ aspectRatio: `${d.src_w} / ${d.src_h}` }}>
      <video ref={video} src={src} playsInline preload="auto" className="absolute inset-0 size-full" />

      {/* outside the window: dimmed, click to move the window there */}
      <div className="absolute inset-0 cursor-crosshair" onPointerDown={clickOutside} />
      {!split && (
        <>
          <div className="pointer-events-none absolute inset-y-0 left-0 bg-black/60" style={{ width: `${left * 100}%` }} />
          <div className="pointer-events-none absolute inset-y-0 right-0 bg-black/60" style={{ width: `${(1 - left - cw) * 100}%` }} />
        </>
      )}

      {split && (
        <div className="pointer-events-none absolute inset-0">
          {cams.map((c, i) => (
            <div key={i} className="absolute rounded-sm border-2 border-dashed border-fuchsia-400/90"
              style={{ left: `${c[0] * 100}%`, top: `${c[1] * 100}%`, width: `${(c[2] - c[0]) * 100}%`, height: `${(c[3] - c[1]) * 100}%` }} />
          ))}
          <span className="absolute top-2 left-1/2 -translate-x-1/2 rounded-md bg-black/70 px-2 py-1 text-[11px] font-medium text-white backdrop-blur">
            Facecam layout (automatic) · click or drag to set your own window
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
          split ? "border-2 border-dashed border-white/40" : win.manual ? "border-2 border-accent shadow-[0_0_0_1px_rgba(0,0,0,0.4)]" : "border-2 border-white/85",
        )}
        style={{ left: `${left * 100}%`, width: `${cw * 100}%` }}
      >
        {!split && (
          <span className={cx("pointer-events-none absolute top-1.5 left-1.5 rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold text-white",
            win.manual ? "bg-accent" : "bg-black/60")}>
            {aspectLabel(win.a, d)}{win.manual ? (win.follow ? " · follows faces" : "") : " · auto"}
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
    </div>
  );
}
