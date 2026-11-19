import { useRef, type PointerEvent as RPointerEvent } from "react";
import type { EditorData, FrameKey } from "../api";
import { cx, fmtTime } from "../lib";
import { keysInShot, modeLabel, removedSpans, shotRange } from "./frame";

/** Shots (cut to cut), keyframes, the pauses pacing removes, and the playhead. Click or drag to scrub. */
export function Timeline({ d, keys, t, dur, current, onSeek }: {
  d: EditorData;
  keys: FrameKey[];
  t: number;
  dur: number;
  current: number; // current shot index
  onSeek: (t: number) => void;
}) {
  const bar = useRef<HTMLDivElement>(null);
  const scrubbing = useRef(false);
  const fps = d.fps;
  const pct = (sec: number) => `${Math.min(100, Math.max(0, (sec / dur) * 100))}%`;

  function seekAt(e: RPointerEvent) {
    const r = bar.current!.getBoundingClientRect();
    onSeek(Math.min(dur, Math.max(0, ((e.clientX - r.left) / r.width) * dur)));
  }

  return (
    <div className="select-none">
      <div
        ref={bar}
        className="relative h-14 cursor-pointer touch-none overflow-hidden rounded-lg border border-line bg-surface-2"
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          scrubbing.current = true;
          (e.target as Element).setPointerCapture(e.pointerId);
          seekAt(e);
        }}
        onPointerMove={(e) => scrubbing.current && seekAt(e)}
        onPointerUp={() => (scrubbing.current = false)}
        onPointerCancel={() => (scrubbing.current = false)}
        role="slider"
        aria-label="Clip timeline"
        aria-valuemin={0}
        aria-valuemax={Math.round(dur)}
        aria-valuenow={Math.round(t)}
      >
        {d.shots.map((s, i) => {
          const [a, b] = shotRange(d, i);
          const manual = keysInShot(d, keys, i).length > 0;
          return (
            <div key={i} title={`Shot ${i + 1} · ${fmtTime(a / fps)}–${fmtTime(b / fps)} · ${manual ? "manual" : modeLabel[s.mode]}`}
              className={cx("absolute top-0 h-7 border-r border-bg/70",
                manual ? "bg-accent/35" : s.mode === "split" ? "bg-fuchsia-500/20" : s.mode === "fit" ? "bg-warn/20" : "bg-fg/10",
                i === current && "ring-1 ring-fg/40 ring-inset")}
              style={{ left: pct(a / fps), width: pct((b - a) / fps) }} />
          );
        })}
        {removedSpans(d.keep, dur).map(([a, b], i) => (
          <div key={i} className="hatch absolute inset-y-0 opacity-70" title="Pause removed by pacing (not in the short)"
            style={{ left: pct(a), width: pct(b - a) }} />
        ))}
        {keys.map((k) => (
          <div key={k.f} className="pointer-events-none absolute top-[34px] size-2.5 -translate-x-1/2 rotate-45 rounded-[2px] border border-bg bg-accent"
            style={{ left: pct(k.f / fps) }} title={`Keyframe ${fmtTime(k.f / fps)}`} />
        ))}
        <div className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-fg" style={{ left: pct(t) }} />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-muted">
        <Legend className="bg-fg/10" label="Auto" />
        <Legend className="bg-accent/35" label="Manual" />
        <Legend className="bg-warn/20" label="Whole frame" />
        <Legend className="bg-fuchsia-500/20" label="Facecam" />
        <Legend className="hatch" label="Removed pause" />
        <span className="inline-flex items-center gap-1.5"><span className="size-2 rotate-45 rounded-[1px] bg-accent" /> Keyframe</span>
      </div>
    </div>
  );
}

function Legend({ className, label }: { className: string; label: string }) {
  return <span className="inline-flex items-center gap-1.5"><span className={cx("h-2.5 w-4 rounded-sm border border-line", className)} />{label}</span>;
}
