import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ArrowLeft, Check, ChevronLeft, ChevronRight, Diamond, Eye, Keyboard, LoaderCircle, Lock, Pause, Play, Redo2,
  RotateCcw, ScanFace, SkipBack, SkipForward, Undo2, X,
} from "lucide-react";
import { useConfirm, useToast, ErrorBoundary } from "@kit/feedback";
import { api, editorHref, jobHref, type EditorData, type FrameKey, type Review } from "../api";
import { Button, Card, CardHeader, Input, Toggle } from "@kit/ui";
import { cx, errorText, Link, navigate, useStored, usePoll } from "../lib";
import {
  FILL, PRESETS, aspectLabel, clamp, clampX, keysInShot, modeLabel, presetValue, putKey, shotIndexAt, shotRange,
  skipTarget, windowAt,
} from "../editor/frame";
import { Preview, drawFrame } from "../editor/Preview";
import { Stage } from "../editor/Stage";
import { Timeline } from "../editor/Timeline";

type Hist = { past: FrameKey[][]; present: FrameKey[]; future: FrameKey[][] };
type Act =
  | { type: "init"; keys: FrameKey[] }
  | { type: "checkpoint" }               // before a drag: one undo step for the whole drag
  | { type: "live"; keys: FrameKey[] }   // during a drag
  | { type: "commit"; keys: FrameKey[] } // a one-off change
  | { type: "undo" }
  | { type: "redo" };

function hist(h: Hist, a: Act): Hist {
  switch (a.type) {
    case "init": return { past: [], present: a.keys, future: [] };
    case "checkpoint": return { past: [...h.past.slice(-99), h.present], present: h.present, future: [] };
    case "live": return { ...h, present: a.keys };
    case "commit": return { past: [...h.past.slice(-99), h.present], present: a.keys, future: [] };
    case "undo": return h.past.length ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h;
    case "redo": return h.future.length ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h;
  }
}

const TEXT_LIMITS = { title: 100, hook_text: 120 };

function fmtClock(sec: number) {
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

const isTyping = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

export function EditorPage({ jobId, clipId }: { jobId: string; clipId: string }) {
  const { data: d, error, reload, setData } = usePoll(
    () => api.editor(jobId, clipId),
    (v) => (v && !v.clip.ready ? 2500 : null),
    `${jobId}/${clipId}`,
  );
  if (error && !d) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="text-sm font-medium">{error}</p>
        <Link to={jobHref(jobId)} className="mt-3 inline-block text-sm text-accent hover:underline">Back to the project</Link>
      </div>
    );
  }
  if (!d) {
    return (
      <div className="mx-auto max-w-7xl px-4 pt-6 sm:px-6">
        <div className="shimmer h-4 w-24 rounded" />
        <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="shimmer aspect-video rounded-xl" />
          <div className="shimmer aspect-[9/16] rounded-xl" />
        </div>
      </div>
    );
  }
  return <Editor key={clipId} jobId={jobId} d={d} reload={reload} setData={setData} />;
}

function Editor({ jobId, d, reload, setData }: { jobId: string; d: EditorData; reload: () => void; setData: (d: EditorData) => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const video = useRef<HTMLVideoElement>(null);
  const [h, dispatch] = useReducer(hist, { past: [], present: d.keys, future: [] });
  const [saved, setSaved] = useState(() => JSON.stringify(d.keys));
  const keys = h.present;
  const dirty = JSON.stringify(keys) !== saved;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [videoState, setVideoState] = useState<"loading" | "ready" | "error">("loading");
  const [saving, setSaving] = useState(false);
  const [skipPauses, setSkipPauses] = useStored("editor.skipPauses", true);
  const [showFaces, setShowFaces] = useStored("editor.faces", false);
  const [safe, setSafe] = useStored("editor.safe", false);
  const [tab, setTab] = useState<"live" | "rendered">("live");

  const fps = d.fps;
  const n = d.n_frames;
  const dur = d.clip.duration;
  const step = 1 / (fps > 35 ? fps / 2 : fps); // the proxy has every 2nd frame of 50/60 fps sources
  const f = clamp(Math.round(t * fps), 0, n - 1);
  const win = windowAt(d, keys, f);
  const si = shotIndexAt(d.shots, f);
  const shot = d.shots[si];
  const [s0, s1] = shotRange(d, si);
  const shotKeys = keysInShot(d, keys, si);
  const tracked = shot.mode === "track";

  // the clock: follow the video, and jump over pauses that pacing removes (they aren't in the short)
  useEffect(() => {
    let id = 0;
    const tick = () => {
      const v = video.current;
      if (v) {
        if (!v.paused && skipPauses) {
          const to = skipTarget(d.keep, v.currentTime);
          if (to != null) v.currentTime = to;
        }
        setT((old) => (Math.abs(old - v.currentTime) > 1e-4 ? v.currentTime : old));
        setPlaying(!v.paused);
      }
      id = requestAnimationFrame(tick);
    };
    id = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(id);
  }, [d.keep, skipPauses]);

  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const ok = () => setVideoState("ready");
    const bad = () => setVideoState("error");
    v.addEventListener("loadeddata", ok);
    v.addEventListener("error", bad);
    if (v.readyState >= 2) ok();
    return () => {
      v.removeEventListener("loadeddata", ok);
      v.removeEventListener("error", bad);
    };
  }, []);

  // once this clip's video is in, prepare the next one's in the background
  useEffect(() => {
    if (videoState === "ready" && d.next) api.warmProxy(jobId, d.next).catch(() => {});
  }, [videoState, d.next, jobId]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const seek = useCallback((sec: number) => {
    const v = video.current;
    if (v) v.currentTime = clamp(sec, 0, Math.max(0, dur - 0.01));
    setT(clamp(sec, 0, dur));
  }, [dur]);

  const togglePlay = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) {
      if (v.currentTime >= dur - 0.05) v.currentTime = 0;
      v.play().catch(() => {});
    } else v.pause();
  }, [dur]);

  const pause = () => video.current?.pause();

  // ---------------------------------------------------------------- edits
  const commit = (next: FrameKey[]) => dispatch({ type: "commit", keys: next });

  function setShape(a: number) {
    const keepFollowing = win.manual ? win.follow : true; // auto framing: keep tracking, just widen
    commit(putKey(keys, { f, x: keepFollowing ? null : clampX(win.x, a, d), a }));
  }

  function shapeToClip(a: number) {
    let next = keys.map((k) => ({ ...k, a, x: k.x == null ? null : clampX(k.x, a, d) }));
    d.shots.forEach((_, i) => {
      if (!keysInShot(d, next, i).length) next = putKey(next, { f: shotRange(d, i)[0], x: null, a });
    });
    commit(next);
    toast.ok(`Window ${aspectLabel(a, d)} for the whole clip`, "Face tracking keeps going where it was on");
  }

  function lockShot() {
    const rest = keys.filter((k) => k.f < s0 || k.f >= s1);
    commit(putKey(rest, { f: s0, x: clampX(win.x, win.a, d), a: win.a }));
  }

  function resetShot() {
    commit(keys.filter((k) => k.f < s0 || k.f >= s1));
  }

  function followFaces(on: boolean) {
    if (!shotKeys.length) return;
    commit(keys.map((k) => (k.f >= s0 && k.f < s1 ? { ...k, x: on ? null : clampX(windowAt(d, keys, k.f).x, k.a, d) } : k)));
  }

  function addKey() {
    commit(putKey(keys, { f, x: win.manual && win.follow ? null : clampX(win.x, win.a, d), a: win.a }));
  }

  function nearestKey(): FrameKey | null {
    const tol = Math.max(2, Math.round(fps / 4));
    let best: FrameKey | null = null;
    for (const k of keys) if (Math.abs(k.f - f) <= tol && (!best || Math.abs(k.f - f) < Math.abs(best.f - f))) best = k;
    return best;
  }

  function deleteKey() {
    const k = nearestKey();
    if (k) commit(keys.filter((q) => q !== k));
  }

  function jumpKey(dir: 1 | -1) {
    const sorted = [...keys].sort((p, q) => p.f - q.f);
    const k = dir > 0 ? sorted.find((q) => q.f > f) : [...sorted].reverse().find((q) => q.f < f);
    if (k) {
      pause();
      seek(k.f / fps + 0.001);
    }
  }

  function jumpShot(dir: 1 | -1) {
    const i = clamp(si + dir, 0, d.shots.length - 1);
    pause();
    seek(shotRange(d, i)[0] / fps + 0.001);
  }

  async function save() {
    if (saving) return;
    setSaving(true);
    try {
      const res = await api.setFraming(jobId, d.clip.id, keys);
      dispatch({ type: "init", keys: res.keys });
      setSaved(JSON.stringify(res.keys));
      toast.ok(res.rerender ? "Framing saved · re-rendering this clip" : "Framing saved", res.rerender ? "The rendered preview updates when it's done" : undefined);
      reload();
    } catch (e) {
      toast.err("Couldn't save the framing", errorText(e));
    } finally {
      setSaving(false);
    }
  }

  async function go(to: string) {
    if (dirty) {
      const ok = await confirm({
        title: "Leave without saving?",
        body: "Your framing changes on this clip haven't been saved.",
        confirm: "Discard changes",
        danger: true,
      });
      if (!ok) return;
    }
    navigate(to);
  }

  // ---------------------------------------------------------------- keyboard
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  keyRef.current = (e: KeyboardEvent) => {
    if (isTyping(e.target)) return;
    const mod = e.ctrlKey || e.metaKey;
    const k = e.key;
    if (mod && k.toLowerCase() === "s") { e.preventDefault(); if (dirty) save(); return; }
    if (mod && k.toLowerCase() === "z") { e.preventDefault(); dispatch({ type: e.shiftKey ? "redo" : "undo" }); return; }
    if (mod && k.toLowerCase() === "y") { e.preventDefault(); dispatch({ type: "redo" }); return; }
    if (mod || e.altKey) return;
    if (k === " ") { e.preventDefault(); togglePlay(); }
    else if (k === "ArrowLeft") { e.preventDefault(); pause(); seek(t - (e.shiftKey ? 1 : step)); }
    else if (k === "ArrowRight") { e.preventDefault(); pause(); seek(t + (e.shiftKey ? 1 : step)); }
    else if (k === "k" || k === "K") addKey();
    else if (k === "Delete" || k === "Backspace") { e.preventDefault(); deleteKey(); }
    else if (k === "[") jumpShot(-1);
    else if (k === "]") jumpShot(1);
    else if (k === ",") jumpKey(-1);
    else if (k === ".") jumpKey(1);
    else if (k === "Home") { pause(); seek(0); }
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keyRef.current(e);
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);

  // ---------------------------------------------------------------- text + review
  const [title, setTitle] = useState(d.clip.title);
  const [hookText, setHookText] = useState(d.clip.hook_text);
  const [showHook, setShowHook] = useState(d.clip.show_hook !== false);
  const [savingText, setSavingText] = useState(false);
  const textChanges = useMemo(() => {
    const c: Record<string, unknown> = {};
    if (title.trim() !== d.clip.title) c.title = title.trim();
    if (hookText.trim() !== d.clip.hook_text) c.hook_text = hookText.trim();
    if (showHook !== (d.clip.show_hook !== false)) c.show_hook = showHook;
    return c;
  }, [title, hookText, showHook, d.clip]);
  const textInvalid = !title.trim() ? "The title can't be empty"
    : title.length > TEXT_LIMITS.title ? `Title: max ${TEXT_LIMITS.title} characters`
    : hookText.length > TEXT_LIMITS.hook_text ? `Hook: max ${TEXT_LIMITS.hook_text} characters` : null;

  async function saveText() {
    setSavingText(true);
    try {
      const res = await api.editClip(jobId, d.clip.id, textChanges);
      setData({ ...d, clip: { ...d.clip, ...res.clip, ready: res.rerender ? false : d.clip.ready } });
      toast.ok(res.rerender ? "Saved · re-rendering this clip" : "Saved");
      reload();
    } catch (e) {
      toast.err("Couldn't save", errorText(e));
    } finally {
      setSavingText(false);
    }
  }

  async function review(r: Review) {
    const next = d.clip.review === r ? null : r;
    try {
      await api.review(jobId, d.clip.id, next);
      setData({ ...d, clip: { ...d.clip, review: next } });
    } catch (e) {
      toast.err("Couldn't save the review", errorText(e));
    }
  }

  // ---------------------------------------------------------------- view
  const hookShown = d.hook.enabled && (showHook || !d.hook.auto);
  const previewText = (hookText.trim() || title.trim());
  const sample = useMemo(() => {
    if (!showFaces || !d.faces.length) return null;
    let best = d.faces[0];
    for (const s of d.faces) if (Math.abs(s.f - f) < Math.abs(best.f - f)) best = s;
    return Math.abs(best.f - f) <= fps ? best.faces : null;
  }, [showFaces, d.faces, f, fps]);
  const autoShape = shot.mode === "fit" ? "whole frame" : shot.mode === "split" ? "facecam layout" : "9:16";

  return (
    <div className="mx-auto max-w-7xl px-4 pt-5 pb-24 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button onClick={() => go(jobHref(jobId))} className="inline-flex items-center gap-1.5 text-sm text-muted transition hover:text-fg">
          <ArrowLeft className="size-4" /> Project
        </button>
        <div className="flex items-center gap-1.5">
          <Button variant="ghost" disabled={!d.prev} onClick={() => d.prev && go(editorHref(jobId, d.prev))} title="Previous clip">
            <ChevronLeft className="size-4" />
          </Button>
          <span className="font-mono text-xs text-muted">Clip {d.index} of {d.count}</span>
          <Button variant="ghost" disabled={!d.next} onClick={() => d.next && go(editorHref(jobId, d.next))} title="Next clip">
            <ChevronRight className="size-4" />
          </Button>
        </div>
      </div>

      <header className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight sm:text-2xl" title={d.clip.title}>{d.clip.title}</h1>
          <p className="mt-0.5 text-xs text-muted">
            {dur.toFixed(1)} s · {d.shots.length} shot{d.shots.length === 1 ? "" : "s"} · {keys.length} keyframe{keys.length === 1 ? "" : "s"}
            {!d.analysed && " · not face-tracked (centre crop)"}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="ghost" onClick={() => dispatch({ type: "undo" })} disabled={!h.past.length} title="Undo (Ctrl+Z)" aria-label="Undo"><Undo2 className="size-4" /></Button>
          <Button variant="ghost" onClick={() => dispatch({ type: "redo" })} disabled={!h.future.length} title="Redo (Ctrl+Shift+Z)" aria-label="Redo"><Redo2 className="size-4" /></Button>
          <Button variant="outline" disabled={!keys.length} onClick={() => commit([])} title="Remove every keyframe: back to the automatic framing">
            <RotateCcw className="size-4" /> <span className="hidden sm:inline">All auto</span>
          </Button>
          {dirty && <Button variant="ghost" onClick={() => dispatch({ type: "init", keys: JSON.parse(saved) })}>Discard</Button>}
          <Button onClick={save} disabled={!dirty} loading={saving} title="Save and re-render this clip (Ctrl+S)">
            <Check className="size-4" /> Save framing
          </Button>
        </div>
      </header>

      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-4">
          <div className="relative">
            <ErrorBoundary label="Video">
              <Stage d={d} video={video} win={win} faces={sample} cams={shot.cams} src={d.proxy_url}
                onBegin={() => { pause(); dispatch({ type: "checkpoint" }); }}
                onChange={(x, a) => dispatch({ type: "live", keys: putKey(keys, { f, x, a }) })}
                onEnd={() => {}} />
            </ErrorBoundary>
            {videoState !== "ready" && (
              <div className="absolute inset-0 grid place-items-center rounded-xl bg-black/70 p-6 text-center text-sm text-white">
                {videoState === "error" ? (
                  <span>Couldn't load the clip's video.<br />
                    <button className="mt-2 underline" onClick={() => { setVideoState("loading"); video.current?.load(); }}>Try again</button>
                  </span>
                ) : (
                  <span className="flex flex-col items-center gap-2">
                    <LoaderCircle className="size-5 animate-spin" />
                    Preparing the full-frame video…
                    <span className="text-xs text-white/60">First open of this clip only (about half the clip's length)</span>
                  </span>
                )}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={togglePlay} aria-label={playing ? "Pause" : "Play"} title="Play / pause (Space)" className="w-10 px-0">
              {playing ? <Pause className="size-4" /> : <Play className="size-4" />}
            </Button>
            <Button variant="ghost" onClick={() => { pause(); seek(t - step); }} title="One frame back (←, Shift: 1 s)" aria-label="Frame back"><SkipBack className="size-4" /></Button>
            <Button variant="ghost" onClick={() => { pause(); seek(t + step); }} title="One frame forward (→, Shift: 1 s)" aria-label="Frame forward"><SkipForward className="size-4" /></Button>
            <span className="font-mono text-xs text-muted tabular-nums">{fmtClock(t)} / {fmtClock(dur)}</span>
            <div className="ml-auto flex flex-wrap items-center gap-4">
              <Toggle checked={skipPauses} onChange={setSkipPauses} label={<span className="text-xs">Skip removed pauses</span>} />
              {d.faces.length > 0 && <Toggle checked={showFaces} onChange={setShowFaces} label={<span className="inline-flex items-center gap-1 text-xs"><ScanFace className="size-3.5" /> Faces</span>} />}
            </div>
          </div>

          <Timeline d={d} keys={keys} t={t} dur={dur} current={si} onSeek={(s) => { pause(); seek(s); }} />

          <Card>
            <CardHeader
              title={<span>Shot {si + 1} of {d.shots.length} <span className="ml-1 font-normal text-muted">{fmtClock(s0 / fps)}–{fmtClock(Math.min(s1, n) / fps)}</span></span>}
              aside={shotKeys.length ? <span className="font-medium text-accent">Manual · {shotKeys.length} key{shotKeys.length === 1 ? "" : "s"}</span> : <span>Auto · {modeLabel[shot.mode]}</span>}
            />
            <div className="space-y-4 p-4">
              <div>
                <p className="mb-2 text-xs font-medium text-muted">
                  Window shape <span className="font-normal">· now {win.kind === "split" ? "facecam layout" : aspectLabel(win.a, d)}{!win.manual && ` (auto: ${autoShape})`}</span>
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((p) => {
                    const v = presetValue(p, d);
                    const on = win.kind !== "split" && Math.abs(win.a - v) < 0.006;
                    return (
                      <button key={p.label} onClick={() => setShape(v)} title={`${p.hint} · at this frame`}
                        className={cx("h-8 rounded-lg border px-2.5 font-mono text-xs font-semibold transition",
                          on ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:border-line-strong hover:text-fg")}>
                        {p.label}
                      </button>
                    );
                  })}
                </div>
                <p className="mt-2 text-xs text-faint">
                  Wider than 9:16 shows more of the frame: it fills the short's width over a blurred copy of the video. Drag the window's edges for any shape.
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => shapeToClip(win.kind === "split" ? FILL : win.a)} title="Every shot gets this window shape; tracked shots keep following faces">
                  Use {win.kind === "split" ? "9:16" : aspectLabel(win.a, d)} for the whole clip
                </Button>
                <Button variant="outline" onClick={lockShot} title="One fixed window for this whole shot (removes its other keyframes)">
                  <Lock className="size-3.5" /> Hold this window for the shot
                </Button>
                <Button variant="outline" onClick={addKey} title="Keyframe at this frame (K): the window moves smoothly between keyframes">
                  <Diamond className="size-3.5" /> Keyframe
                </Button>
                <Button variant="ghost" onClick={deleteKey} disabled={!nearestKey()} title="Delete the keyframe here (Del)">
                  <X className="size-3.5" /> Delete keyframe
                </Button>
                <Button variant="ghost" onClick={resetShot} disabled={!shotKeys.length} title="Back to the automatic framing for this shot">
                  <RotateCcw className="size-3.5" /> Reset shot
                </Button>
              </div>
              {tracked && shotKeys.length > 0 && (
                <Toggle checked={shotKeys.every((k) => k.x == null)} onChange={followFaces}
                  label={<span className="text-sm">Follow faces in this shot <span className="text-xs text-muted">(only the shape is yours)</span></span>} />
              )}
            </div>
          </Card>

          <details className="rounded-xl border border-line bg-surface px-4 py-3 text-sm">
            <summary className="flex cursor-pointer items-center gap-2 text-muted"><Keyboard className="size-4" /> Keyboard shortcuts</summary>
            <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-1.5 text-xs text-muted sm:grid-cols-2">
              {[
                ["Space", "Play / pause"], ["← / →", "One frame (Shift: 1 second)"], ["K", "Keyframe here"],
                ["Del", "Delete the keyframe here"], [", / .", "Previous / next keyframe"], ["[ / ]", "Previous / next shot"],
                ["Ctrl+Z / Ctrl+Shift+Z", "Undo / redo"], ["Ctrl+S", "Save"], ["Shift while dragging an edge", "No snapping to presets"],
              ].map(([k, v]) => (
                <p key={k} className="flex justify-between gap-3"><kbd className="font-mono text-fg">{k}</kbd><span>{v}</span></p>
              ))}
            </div>
          </details>
        </div>

        <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start">
          <div>
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="inline-flex rounded-lg border border-line bg-surface-2 p-0.5 text-xs font-medium">
                {(["live", "rendered"] as const).map((k) => (
                  <button key={k} onClick={() => setTab(k)}
                    className={cx("rounded-md px-2.5 py-1 transition", tab === k ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg")}>
                    {k === "live" ? "Live" : "Rendered"}
                  </button>
                ))}
              </div>
              {tab === "live" && (
                <button onClick={() => setSafe(!safe)} className={cx("inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs transition", safe ? "text-accent" : "text-muted hover:text-fg")}
                  title="Where the Shorts app draws its buttons and text (approximate), and the caption line">
                  <Eye className="size-3.5" /> Safe zones
                </button>
              )}
            </div>
            <div className="mx-auto max-w-[300px]">
              {tab === "live" ? (
                <Preview draw={(ctx) => drawFrame(ctx, video.current, d, win, t, { safe, hookText: previewText, showHook: hookShown })} />
              ) : d.clip.ready && d.clip.url ? (
                <video key={d.clip.url} src={d.clip.url} controls playsInline className="block aspect-[9/16] w-full rounded-xl bg-black" />
              ) : (
                <div className="shimmer grid aspect-[9/16] w-full place-items-center rounded-xl">
                  <span className="flex items-center gap-1.5 text-xs text-white/70"><LoaderCircle className="size-3.5 animate-spin" /> Rendering</span>
                </div>
              )}
            </div>
            <p className="mt-2 text-center text-[11px] text-faint">
              {tab === "live" ? "Approximate: captions and exact layouts show in the rendered preview" : dirty ? "Shows the last saved framing" : "The real render"}
            </p>
          </div>

          <Card>
            <CardHeader title="On-screen text" />
            <div className="space-y-3 p-4">
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-muted">Title</span>
                <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={TEXT_LIMITS.title + 20} />
              </label>
              <label className="block">
                <span className="mb-1 flex justify-between text-xs font-medium text-muted">
                  Hook text <span className="font-normal text-faint">{hookText.length}/{TEXT_LIMITS.hook_text}</span>
                </span>
                <Input value={hookText} onChange={(e) => setHookText(e.target.value)} placeholder="Empty: the title is shown" />
              </label>
              <Toggle checked={showHook} onChange={setShowHook} label={<span className="text-sm">Show the hook banner</span>} />
              {!d.hook.enabled && <p className="text-xs text-faint">The hook banner is off in Settings (or the tweet card carries the text).</p>}
              {textInvalid && <p className="text-xs text-err">{textInvalid}</p>}
              <Button className="w-full" onClick={saveText} disabled={!Object.keys(textChanges).length || !!textInvalid} loading={savingText}>Save text</Button>
            </div>
          </Card>

          <div className="grid grid-cols-2 gap-2">
            <Button variant="outline" onClick={() => review("rejected")} className={cx(d.clip.review === "rejected" && "border-err/60 text-err")}>
              <X className="size-4" /> {d.clip.review === "rejected" ? "Rejected" : "Reject"}
            </Button>
            <Button variant="outline" onClick={() => review("approved")} className={cx(d.clip.review === "approved" && "border-ok/60 text-ok")}>
              <Check className="size-4" /> {d.clip.review === "approved" ? "Approved" : "Approve"}
            </Button>
          </div>
        </aside>
      </div>
    </div>
  );
}
