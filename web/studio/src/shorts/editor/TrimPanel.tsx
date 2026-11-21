import { useEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { Check, ChevronDown, LoaderCircle, Pause, Play, RotateCcw, Scissors } from "lucide-react";
import { api, type TrimData } from "../api";
import { Button, Card } from "@kit/ui";
import { useToast } from "@kit/feedback";
import { cx, errorText } from "../lib";
import { clamp } from "./frame";

const signed = (s: number) => `${s > 0 ? "+" : s < 0 ? "−" : "±"}${Math.abs(s).toFixed(1)} s`;

/** Move the clip's start and end: plays the clip with 5 s before and after it, and the transcript there, so you
 * can decide whether the lead-in or the follow-up belongs in the short. */
export function TrimPanel({ jobId, clipId, blocked, onSaved }: {
  jobId: string;
  clipId: string;
  blocked: string | null;   // why saving isn't possible right now (unsaved framing, job busy)
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Card>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left">
        <span className="inline-flex items-center gap-2 text-sm font-medium"><Scissors className="size-4" /> Trim start / end</span>
        <span className="inline-flex items-center gap-1 text-xs text-muted">
          {open ? "" : "See 5 s before and after the clip"}
          <ChevronDown className={cx("size-4 transition", open && "rotate-180")} />
        </span>
      </button>
      {open && <Trim jobId={jobId} clipId={clipId} blocked={blocked} onSaved={onSaved} />}
    </Card>
  );
}

function Trim({ jobId, clipId, blocked, onSaved }: { jobId: string; clipId: string; blocked: string | null; onSaved: () => void }) {
  const toast = useToast();
  const [d, setD] = useState<TrimData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.trim(jobId, clipId).then(setD, (e) => setErr(errorText(e)));
  }, [jobId, clipId]);
  if (err) return <p className="px-4 pb-4 text-sm text-err">{err}</p>;
  if (!d) return <div className="px-4 pb-4"><div className="shimmer aspect-video rounded-lg" /></div>;
  return <TrimBody d={d} jobId={jobId} clipId={clipId} blocked={blocked} toast={toast} onSaved={onSaved} />;
}

function TrimBody({ d, jobId, clipId, blocked, toast, onSaved }: {
  d: TrimData; jobId: string; clipId: string; blocked: string | null;
  toast: ReturnType<typeof useToast>; onSaved: () => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const [r0, r1] = d.review;
  const span = Math.max(0.1, r1 - r0);
  const [inn, setInn] = useState(d.start);
  const [out, setOut] = useState(d.end);
  const [t, setT] = useState(d.start);           // source seconds
  const [playing, setPlaying] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);
  const stopAt = useRef<number | null>(null);    // "play the short": pause at its end
  const drag = useRef<"in" | "out" | "seek" | null>(null);
  const dirty = Math.abs(inn - d.start) > 0.01 || Math.abs(out - d.end) > 0.01;
  const len = out - inn;

  useEffect(() => {
    let id = 0;
    const tick = () => {
      const v = video.current;
      if (v) {
        const now = r0 + v.currentTime;
        if (!v.paused && stopAt.current != null && now >= stopAt.current) {
          v.pause();
          stopAt.current = null;
        }
        setT((old) => (Math.abs(old - now) > 1e-3 ? now : old));
        setPlaying(!v.paused);
      }
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [r0]);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const ok = () => setState("ready");
    const bad = () => setState("error");
    v.addEventListener("loadeddata", ok);
    v.addEventListener("error", bad);
    if (v.readyState >= 2) ok();
    return () => { v.removeEventListener("loadeddata", ok); v.removeEventListener("error", bad); };
  }, []);

  function seek(src: number) {
    const s = clamp(src, r0, r1 - 0.01);
    if (video.current) video.current.currentTime = s - r0;
    setT(s);
  }

  function play(from: number, until: number | null = null) {
    const v = video.current;
    if (!v) return;
    seek(from);
    stopAt.current = until;
    v.play().catch(() => {});
  }

  function toggle() {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      stopAt.current = null;
      if (r0 + v.currentTime >= r1 - 0.05) seek(r0);
      v.play().catch(() => {});
    } else v.pause();
  }

  const setStart = (s: number) => setInn(clamp(Math.round(s * 100) / 100, r0, out - d.min_s));
  const setEnd = (s: number) => setOut(clamp(Math.round(s * 100) / 100, inn + d.min_s, Math.min(r1, inn + d.max_s)));

  const at = (e: RPointerEvent) => {
    const r = bar.current!.getBoundingClientRect();
    return r0 + clamp((e.clientX - r.left) / r.width, 0, 1) * span;
  };
  const pct = (s: number) => `${clamp((s - r0) / span, 0, 1) * 100}%`;

  async function save() {
    setSaving(true);
    try {
      video.current?.pause();
      const res = await api.setTrim(jobId, clipId, inn, out);
      toast.ok(res.rerender ? "Trim saved · re-framing and rendering this clip" : "Nothing changed",
        res.dropped_keys ? `${res.dropped_keys} keyframe${res.dropped_keys === 1 ? " was" : "s were"} outside the new range and removed` : undefined);
      if (res.rerender) onSaved();
    } catch (e) {
      toast.err("Couldn't save the trim", errorText(e));
    } finally {
      setSaving(false);
    }
  }

  const inside = t >= inn && t <= out;
  return (
    <div className="space-y-3 px-4 pb-4">
      <div className="relative overflow-hidden rounded-lg bg-black">
        <video ref={video} src={d.proxy_url} preload="auto" playsInline className="block aspect-video w-full" onClick={toggle} />
        {state === "ready" && (
          <span className={cx("pointer-events-none absolute top-2 left-2 rounded px-1.5 py-0.5 text-[11px] font-medium",
            inside ? "bg-ok/80 text-white" : "bg-black/70 text-white/80")}>
            {inside ? "In the short" : t < inn ? "Before the start" : "After the end"}
          </span>
        )}
        {state !== "ready" && (
          <div className="absolute inset-0 grid place-items-center bg-black/70 p-4 text-center text-sm text-white">
            {state === "error" ? (
              <span>Couldn't load the video.<br />
                <button className="mt-2 underline" onClick={() => { setState("loading"); video.current?.load(); }}>Try again</button>
              </span>
            ) : (
              <span className="flex flex-col items-center gap-2">
                <LoaderCircle className="size-5 animate-spin" /> Preparing the clip with 5 s on each side…
                <span className="text-xs text-white/60">The extra seconds may need downloading first</span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* the review range: padding greyed out, the two handles are the new start and end */}
      <div
        ref={bar}
        className="relative h-12 cursor-pointer touch-none select-none rounded-lg border border-line bg-surface-2"
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          const s = at(e);
          const px = bar.current!.getBoundingClientRect().width / span; // px per second
          const near = (x: number) => Math.abs(s - x) * px < 12;
          drag.current = near(inn) ? "in" : near(out) ? "out" : "seek";
          e.currentTarget.setPointerCapture(e.pointerId);
          video.current?.pause();
          if (drag.current === "seek") seek(s);
        }}
        onPointerMove={(e) => {
          if (!drag.current) return;
          const s = at(e);
          if (drag.current === "in") { setStart(s); seek(s); }
          else if (drag.current === "out") { setEnd(s); seek(s); }
          else seek(s);
        }}
        onPointerUp={() => (drag.current = null)}
        onPointerCancel={() => (drag.current = null)}
        role="slider" aria-label="Trim range" aria-valuemin={r0} aria-valuemax={r1} aria-valuenow={t}
      >
        {d.sentences.map((s) => (
          <div key={s.id} className="pointer-events-none absolute top-1 h-2 rounded-sm bg-fg/20"
            style={{ left: pct(s.start), width: `calc(${pct(s.end)} - ${pct(s.start)})` }} />
        ))}
        <div className="hatch pointer-events-none absolute inset-y-0 left-0 rounded-l-lg opacity-80" style={{ width: pct(inn) }} />
        <div className="hatch pointer-events-none absolute inset-y-0 right-0 rounded-r-lg opacity-80" style={{ left: pct(out) }} />
        <div className="pointer-events-none absolute inset-y-0 border-x-2 border-accent bg-accent/10" style={{ left: pct(inn), width: `calc(${pct(out)} - ${pct(inn)})` }} />
        {[d.start, d.end].map((s, i) => (
          <div key={i} className="pointer-events-none absolute inset-y-0 border-l border-dashed border-fg/50" style={{ left: pct(s) }} title="The AI's original cut" />
        ))}
        {[inn, out].map((s, i) => (
          <div key={i} className="pointer-events-none absolute top-1/2 h-7 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-bg bg-accent shadow" style={{ left: pct(s) }} />
        ))}
        <div className="pointer-events-none absolute inset-y-0 w-0.5 -translate-x-1/2 bg-fg" style={{ left: pct(t) }} />
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <Edge label="Start" value={inn} delta={inn - d.start} onNudge={(s) => setStart(inn + s)}
          onHere={() => setStart(t)} onHear={() => play(Math.max(r0, inn - 3), inn + 2)} hearLabel="Hear the cut-in" />
        <Edge label="End" value={out} delta={out - d.end} onNudge={(s) => setEnd(out + s)}
          onHere={() => setEnd(t)} onHear={() => play(Math.max(r0, out - 2), Math.min(r1, out + 3))} hearLabel="Hear the cut-out" />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={toggle} className="w-10 px-0" aria-label={playing ? "Pause" : "Play"}>
          {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
        </Button>
        <Button variant="outline" onClick={() => play(inn, out)} title="Play only what will be in the short">Play the short</Button>
        <span className="font-mono text-xs text-muted tabular-nums">{signed(t - d.start)} from the start</span>
        <span className={cx("ml-auto font-mono text-xs tabular-nums", len > 60 ? "text-warn" : "text-muted")}
          title={len > 60 ? "Over a minute: fine for Shorts (up to 3 min), but shorter usually keeps more viewers" : undefined}>
          {len.toFixed(1)} s long
        </span>
      </div>

      <div className="max-h-56 space-y-1 overflow-y-auto rounded-lg border border-line p-2 text-sm">
        {d.sentences.map((s) => {
          const outside = s.end <= inn || s.start >= out;
          return (
            <div key={s.id} className={cx("group flex items-start gap-2 rounded-md px-1.5 py-1", outside ? "text-faint" : "text-fg")}>
              <button className="shrink-0 font-mono text-[11px] text-muted hover:text-accent" onClick={() => play(s.start)} title="Play from here">
                {signed(s.start - d.start)}
              </button>
              <p className="min-w-0 flex-1 leading-snug">
                {s.words.map(([a, b, w], i) => (
                  <span key={i} onClick={() => seek(a)}
                    className={cx("cursor-pointer rounded hover:bg-surface-2", (b <= inn || a >= out) && "line-through decoration-fg/30", t >= a && t < b && "bg-accent-soft text-accent")}>
                    {w}{" "}
                  </span>
                ))}
              </p>
              <span className="hidden shrink-0 gap-1 group-hover:flex">
                <button className="rounded border border-line px-1.5 text-[11px] text-muted hover:text-fg" onClick={() => setStart(s.start - 0.15)} title="Start the short at this line">Start</button>
                <button className="rounded border border-line px-1.5 text-[11px] text-muted hover:text-fg" onClick={() => setEnd(s.end + 0.3)} title="End the short after this line">End</button>
              </span>
            </div>
          );
        })}
        {!d.sentences.length && <p className="text-xs text-muted">No speech around this clip.</p>}
      </div>
      <p className="text-[11px] text-faint">
        Greyed lines are outside the short. Hover a line to start or end the short there; click a word to jump to it.
        Need more than 5 s? Save, and the next open shows 5 s around the new range.
      </p>

      <div className="flex flex-wrap items-center justify-end gap-2">
        {blocked && <p className="mr-auto text-xs text-warn">{blocked}</p>}
        <Button variant="ghost" disabled={!dirty} onClick={() => { setInn(d.start); setOut(d.end); }}>
          <RotateCcw className="size-3.5" /> Reset
        </Button>
        <Button onClick={save} disabled={!dirty || !!blocked} loading={saving}
          title="Save the new range: this clip is framed, captioned and rendered again">
          <Check className="size-4" /> Save trim
        </Button>
      </div>
    </div>
  );
}

function Edge({ label, value, delta, onNudge, onHere, onHear, hearLabel }: {
  label: string; value: number; delta: number; onNudge: (s: number) => void; onHere: () => void; onHear: () => void; hearLabel: string;
}) {
  return (
    <div className="rounded-lg border border-line p-2">
      <p className="flex items-baseline justify-between">
        <span className="font-medium text-muted">{label}</span>
        <span className={cx("font-mono tabular-nums", Math.abs(delta) > 0.01 ? "text-accent" : "text-faint")}>{signed(delta)}</span>
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1">
        <button className="h-6 rounded border border-line px-1.5 font-mono text-[11px] text-muted hover:text-fg" onClick={() => onNudge(-0.5)}>−0.5</button>
        <button className="h-6 rounded border border-line px-1.5 font-mono text-[11px] text-muted hover:text-fg" onClick={() => onNudge(-0.1)}>−0.1</button>
        <button className="h-6 rounded border border-line px-1.5 font-mono text-[11px] text-muted hover:text-fg" onClick={() => onNudge(0.1)}>+0.1</button>
        <button className="h-6 rounded border border-line px-1.5 font-mono text-[11px] text-muted hover:text-fg" onClick={() => onNudge(0.5)}>+0.5</button>
      </div>
      <div className="mt-1.5 flex flex-wrap gap-1">
        <button className="h-6 rounded border border-line px-1.5 text-[11px] text-muted hover:text-fg" onClick={onHere} title={`${label} at the playhead`}>Set here</button>
        <button className="h-6 rounded border border-line px-1.5 text-[11px] text-muted hover:text-fg" onClick={onHear}>{hearLabel}</button>
      </div>
      <span className="sr-only">{value.toFixed(2)}</span>
    </div>
  );
}
