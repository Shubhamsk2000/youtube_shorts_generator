import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  ArrowLeft, Check, ChevronLeft, ChevronRight, Diamond, Eye, Keyboard, LoaderCircle, Lock, Pause, Play, Redo2,
  RotateCcw, ScanFace, SkipBack, SkipForward, Undo2, X,
} from "lucide-react";
import { useConfirm, useToast, ErrorBoundary } from "@kit/feedback";
import { api, editorHref, jobHref, type CamBox, type EditorData, type FrameKey, type Review, type SplitSize } from "../api";
import { Button, Card, CardHeader, Input, Segmented, Toggle } from "@kit/ui";
import { cx, errorText, Link, navigate, useStored, usePoll } from "../lib";
import {
  FILL, PRESETS, aspectLabel, clamp, clampX, keysInShot, modeLabel, presetValue, putKey, shotIndexAt, shotRange,
  SPLIT_LIMITS, skipTarget, windowAt, withCamBox, withSizes,
} from "../editor/frame";
import { Preview, drawFrame } from "../editor/Preview";
import { Stage } from "../editor/Stage";
import { Timeline } from "../editor/Timeline";
import { TrimPanel } from "../editor/TrimPanel";

/** What the editor changes: the window keys, the facecam frame and the layout sizes (undone together). */
type Edit = { keys: FrameKey[]; cam: CamBox | null; split: SplitSize | null };
type Hist = { past: Edit[]; present: Edit; future: Edit[] };
type Act =
  | { type: "init"; edit: Edit }
  | { type: "checkpoint" }                 // before a drag: one undo step for the whole drag
  | { type: "live"; edit: Partial<Edit> }  // during a drag
  | { type: "commit"; edit: Partial<Edit> } // a one-off change
  | { type: "undo" }
  | { type: "redo" };

function hist(h: Hist, a: Act): Hist {
  switch (a.type) {
    case "init": return { past: [], present: a.edit, future: [] };
    case "checkpoint": return { past: [...h.past.slice(-99), h.present], present: h.present, future: [] };
    case "live": return { ...h, present: { ...h.present, ...a.edit } };
    case "commit": return { past: [...h.past.slice(-99), h.present], present: { ...h.present, ...a.edit }, future: [] };
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
  // a saved trim changes the clip's frames: start the editor over on the new range
  return <Editor key={`${clipId}:${d.clip.start}:${d.clip.end}`} jobId={jobId} d={d} reload={reload} setData={setData} />;
}

function Editor({ jobId, d, reload, setData }: { jobId: string; d: EditorData; reload: () => void; setData: (d: EditorData) => void }) {
  const toast = useToast();
  const confirm = useConfirm();
  const video = useRef<HTMLVideoElement>(null);
  const [h, dispatch] = useReducer(hist, { past: [], present: { keys: d.keys, cam: d.cam ?? null, split: d.split ?? null }, future: [] });
  const [saved, setSaved] = useState(() => JSON.stringify({ keys: d.keys, cam: d.cam ?? null, split: d.split ?? null }));
  const keys = h.present.keys;
  const camBox = h.present.cam;
  const sizes = h.present.split;
  const stacked = d.layout.screen_layout === "split" && d.shots.some((s) => s.cam_view && !s.cam_view.pip);
  const [keepCam, setKeepCam] = useState(d.keep_cam !== false);
  const [savedCam, setSavedCam] = useState(d.keep_cam !== false);
  const hasCam = d.layout.screen_layout !== "track" && d.shots.some((s) => s.cam_view);
  const dirty = JSON.stringify(h.present) !== saved || (keys.length > 0 && keepCam !== savedCam);
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
  const win0 = windowAt(d, keys, f, keepCam);
  const si = shotIndexAt(d.shots, f);
  const shot = d.shots[si];
  // with your facecam frame, and (cam on top) your window sizes: the gameplay crop then takes its window's shape
  const sizedCam = win0.cam && !win0.cam.pip && sizes ? withSizes(win0.cam, shot, camBox, sizes, d) : null;
  // (no keys in the shot: the automatic position for that shape, off the cam, as the render does)
  const win = sizedCam ? { ...win0, cam: sizedCam, a: sizedCam.screen_a, x: win0.manual && !win0.follow ? win0.x : sizedCam.screen_x }
    : win0.cam ? { ...win0, cam: withCamBox(win0.cam, camBox, d) } : win0;
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
  const commit = (next: FrameKey[]) => dispatch({ type: "commit", edit: { keys: next } });

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
    commit(keys.map((k) => (k.f >= s0 && k.f < s1 ? { ...k, x: on ? null : clampX(windowAt(d, keys, k.f, keepCam).x, k.a, d) } : k)));
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
      const res = await api.setFraming(jobId, d.clip.id, keys, keepCam, camBox, sizes);
      const edit = { keys: res.keys, cam: res.cam ?? null, split: res.split ?? null };
      dispatch({ type: "init", edit });
      setSaved(JSON.stringify(edit));
      setKeepCam(res.keep_cam);
      setSavedCam(res.keep_cam);
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
  const [hookY, setHookY] = useState<number | null>(d.clip.hook_y ?? null); // null = the settings' position
  const [placing, setPlacing] = useState(false); // show the banner past its seconds while it's being moved
  const placingTimer = useRef(0);
  const placeHook = (y: number | null, done = false) => {
    if (y !== undefined) setHookY(y);
    setPlacing(true);
    window.clearTimeout(placingTimer.current);
    if (done) placingTimer.current = window.setTimeout(() => setPlacing(false), 1500);
  };
  const [savingText, setSavingText] = useState(false);
  const textChanges = useMemo(() => {
    const c: Record<string, unknown> = {};
    if (title.trim() !== d.clip.title) c.title = title.trim();
    if (hookText.trim() !== d.clip.hook_text) c.hook_text = hookText.trim();
    if (showHook !== (d.clip.show_hook !== false)) c.show_hook = showHook;
    if (hookY !== (d.clip.hook_y ?? null)) c.hook_y = hookY;
    return c;
  }, [title, hookText, showHook, hookY, d.clip]);
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
  const fromAutoCam = win.kind === "split" && !win.manual; // its window shape is the layout's, not a preset
  const shapeLabel = win.kind === "split"
    ? (win.manual ? `facecam + ${aspectLabel(win.a, d)} gameplay` : "facecam layout")
    : aspectLabel(win.a, d);

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
          <Button variant="outline" disabled={!keys.length && !camBox && !sizes} onClick={() => dispatch({ type: "commit", edit: { keys: [], cam: null, split: null } })}
            title="Remove every keyframe, the facecam frame and the layout sizes: back to the automatic framing">
            <RotateCcw className="size-4" /> <span className="hidden sm:inline">All auto</span>
          </Button>
          {dirty && <Button variant="ghost" onClick={() => { dispatch({ type: "init", edit: JSON.parse(saved) }); setKeepCam(savedCam); }}>Discard</Button>}
          <Button onClick={save} disabled={!dirty} loading={saving} title="Save and re-render this clip (Ctrl+S)">
            <Check className="size-4" /> Save framing
          </Button>
        </div>
      </header>

      <div className="mt-5 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-4">
          <div className="relative">
            <ErrorBoundary label="Video">
              <Stage d={d} video={video} win={win} faces={sample} cams={shot.cams} keepCam={keepCam} src={d.proxy_url}
                camCustom={camBox != null}
                onBegin={() => { pause(); dispatch({ type: "checkpoint" }); }}
                onChange={(x, a) => dispatch({ type: "live", edit: { keys: putKey(keys, { f, x, a }) } })}
                onCam={(cam) => dispatch({ type: "live", edit: { cam } })}
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
              {hasCam && (
                <div>
                  <p className="mb-2 text-xs font-medium text-muted">Facecam shots <span className="font-normal">· whole clip</span></p>
                  <Segmented
                    value={keepCam ? "keep" : "whole"}
                    onChange={(v) => setKeepCam(v === "keep")}
                    options={[
                      { value: "keep", label: "Keep facecam", hint: "The facecam stays; your window and its shape only change the gameplay part" },
                      { value: "whole", label: "Edit whole short", hint: "Your window frames the entire short; the facecam layout is replaced" },
                    ]}
                  />
                  <p className="mt-1.5 text-xs text-faint">
                    {keepCam
                      ? "Moving or reshaping the window only changes the gameplay part. The facecam stays where the layout puts it."
                      : "In shots you edit, your window is the whole short and the facecam layout is dropped."}
                  </p>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs text-faint">
                      <span className="font-medium text-muted">Facecam frame {camBox ? "· yours" : "· auto"}</span>
                      {" · "}Cam crop looks off? Drag the pink frame on the video to move it, or a corner to resize it.
                      Its shape follows the cam slot, so it always fills it. Applies to every facecam shot in this clip.
                    </p>
                    <Button variant="ghost" disabled={!camBox} onClick={() => dispatch({ type: "commit", edit: { cam: null } })}
                      title="Back to the automatically detected facecam crop">
                      <RotateCcw className="size-3.5" /> Auto facecam
                    </Button>
                  </div>
                  {stacked && keepCam && (
                    <LayoutSizes sizes={sizes} autoCamH={(shot.cam_view && !shot.cam_view.pip ? shot.cam_view : d.shots.find((x) => x.cam_view && !x.cam_view.pip)?.cam_view)?.area[0] ?? 0.3}
                      onBegin={() => { pause(); dispatch({ type: "checkpoint" }); }}
                      onLive={(split) => dispatch({ type: "live", edit: { split } })}
                      onSet={(split) => dispatch({ type: "commit", edit: { split } })} />
                  )}
                </div>
              )}
              <div>
                <p className="mb-2 text-xs font-medium text-muted">
                  Window shape <span className="font-normal">· now {shapeLabel}{!win.manual && ` (auto: ${autoShape})`}</span>
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((p) => {
                    const v = presetValue(p, d);
                    const on = !fromAutoCam && Math.abs(win.a - v) < 0.006;
                    return (
                      <button key={p.label} onClick={() => setShape(v)} title={sizedCam ? "Set by Layout sizes in this shot" : `${p.hint} · at this frame`}
                        disabled={!!sizedCam}
                        className={cx("h-8 rounded-lg border px-2.5 font-mono text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40",
                          on ? "border-accent bg-accent-soft text-accent" : "border-line text-muted hover:border-line-strong hover:text-fg")}>
                        {p.label}
                      </button>
                    );
                  })}
                </div>
                <p className="mt-2 text-xs text-faint">
                  {sizedCam ? "In this shot the gameplay window's shape comes from Layout sizes above; drag it on the video to move what it shows. " : ""}
                  Wider than 9:16 shows more of the frame: it fills the short's width over a blurred copy of the video. Drag the window's edges for any shape.
                </p>
              </div>

              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => shapeToClip(fromAutoCam ? FILL : win.a)} title="Every shot gets this window shape; tracked shots keep following faces">
                  Use {fromAutoCam ? "9:16" : aspectLabel(win.a, d)} for the whole clip
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

          <TrimPanel jobId={jobId} clipId={d.clip.id} onSaved={reload}
            blocked={dirty ? "Save or discard your framing changes first"
              : Object.keys(textChanges).length ? "Save or discard your text changes first" : null} />

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
                <Preview
                  draw={(ctx) => drawFrame(ctx, video.current, d, win, t, {
                    safe, hookText: previewText, showHook: hookShown, hookY: hookY ?? d.hook.position, hookAlways: placing,
                  })}
                  onHookDrag={hookShown ? (y, phase) => {
                    if (phase === "start") pause();
                    placeHook(y < 0 ? hookY : Math.round(y * 1000) / 1000, phase === "end");
                  } : undefined} />
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
              {hookShown && (
                <div>
                  <span className="mb-1 flex items-center justify-between text-xs font-medium text-muted">
                    Banner position
                    <span className="inline-flex items-center gap-2 font-normal">
                      <span className="font-mono text-faint">{hookY == null ? "default" : `${Math.round(hookY * 100)}% down`}</span>
                      <button className="text-accent hover:underline disabled:text-faint disabled:no-underline" disabled={hookY == null}
                        onClick={() => placeHook(null, true)} title="Back to the position from Settings">Reset</button>
                    </span>
                  </span>
                  <input type="range" min={0.04} max={0.96} step={0.005} value={hookY ?? d.hook.position}
                    onChange={(e) => placeHook(Number(e.target.value))}
                    onPointerUp={() => placeHook(hookY, true)} onKeyUp={() => placeHook(hookY, true)} onBlur={() => setPlacing(false)}
                    className="w-full accent-[var(--accent)]" aria-label="Hook banner position" />
                  <p className="text-xs text-faint">Or drag the banner on the live preview. Keep it clear of the top bar (Safe zones shows it).</p>
                </div>
              )}
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

const SIZE_ROWS: { k: keyof SplitSize; label: string; hint: string }[] = [
  { k: "cam_h", label: "Cam height", hint: "How tall the cam panel on top is; the gameplay gets the rest" },
  { k: "cam_w", label: "Cam width", hint: "The cam window's width, centred in its panel" },
  { k: "game_w", label: "Gameplay width", hint: "The gameplay window's width, centred" },
  { k: "game_h", label: "Gameplay height", hint: "The gameplay window's height, of the space under the cam, centred" },
];

/** The cam-on-top layout's window sizes (whole clip): each window keeps its centre; what it doesn't cover shows
 * the blurred background. */
function LayoutSizes({ sizes, autoCamH, onBegin, onLive, onSet }: {
  sizes: SplitSize | null;
  autoCamH: number;   // the automatic cam panel height (the starting point)
  onBegin: () => void;
  onLive: (s: SplitSize) => void;
  onSet: (s: SplitSize | null) => void;
}) {
  const base: SplitSize = sizes ?? { cam_h: Math.round(autoCamH * 1000) / 1000, cam_w: 1, game_w: 1, game_h: 1 };
  return (
    <div className="mt-4 rounded-xl border border-line bg-surface-2/50 p-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted">Layout sizes <span className="font-normal">{sizes ? "· yours" : "· auto"}</span></p>
        <Button variant="ghost" className="h-7 px-2 text-xs" disabled={!sizes} onClick={() => onSet(null)} title="Back to the automatic layout">
          <RotateCcw className="size-3.5" /> Auto sizes
        </Button>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-x-5 gap-y-2.5 sm:grid-cols-2">
        {SIZE_ROWS.map(({ k, label, hint }) => {
          const [lo, hi] = SPLIT_LIMITS[k];
          const v = base[k];
          return (
            <label key={k} className="block" title={hint}>
              <span className="flex items-baseline justify-between text-xs">
                <span className="text-fg">{label}</span>
                <span className={cx("font-mono tabular-nums", sizes ? "text-accent" : "text-faint")}>{Math.round(v * 100)}%</span>
              </span>
              <input type="range" min={lo} max={hi} step={0.005} value={v} aria-label={label}
                onPointerDown={onBegin}
                onKeyDown={(e) => { if (e.key.startsWith("Arrow") || e.key === "PageUp" || e.key === "PageDown") onBegin(); }}
                onChange={(e) => onLive({ ...base, [k]: Number(e.target.value) })}
                className="mt-1 w-full accent-[var(--accent)]" />
            </label>
          );
        })}
      </div>
      <p className="mt-2 text-xs text-faint">
        Each window stays centred in its place; uncovered space shows the blurred video. The gameplay crop takes the
        window's shape, so nothing is stretched. Applies to every cam-on-top shot in this clip.
      </p>
    </div>
  );
}
