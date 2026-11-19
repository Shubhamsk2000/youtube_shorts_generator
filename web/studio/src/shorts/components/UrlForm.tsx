import { useEffect, useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent } from "react";
import { ArrowRight, FileVideo, Link2, Sparkles, Upload, X } from "lucide-react";
import { Segmented, TextArea } from "@kit/ui";
import { api, jobHref, uploadVideo, type Focus, type FocusMode } from "../api";
import { cx, errorText, fmtBytes, fmtEta, navigate } from "../lib";
import { Button, ProgressBar } from "./ui";

const FOCUS_MAX = 500;
const FOCUS_EXAMPLES = ["the funniest fails", "every time they talk about money", "the plane landing and the crash", "his advice on investing"];

const looksLikeVideoUrl = (s: string) =>
  /^https?:\/\/(www\.|m\.)?(youtube\.com\/(watch|shorts|live)|youtu\.be\/)/i.test(s.trim());

export function UrlForm({ blocked }: { blocked?: string | null }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upload, setUpload] = useState<{ name: string; size: number; loaded: number; t0: number; abort: () => void } | null>(null);
  const [drag, setDrag] = useState(false);
  const [focusOpen, setFocusOpen] = useState(false);
  const [focusText, setFocusText] = useState("");
  const [focusMode, setFocusMode] = useState<FocusMode>("prefer");
  const fileInput = useRef<HTMLInputElement>(null);
  const uploading = !!upload;
  const focus: Focus | null = focusOpen && focusText.trim() ? { text: focusText.trim(), mode: focusMode } : null;

  useEffect(() => {  // closing the tab mid-upload loses it: ask first
    if (!uploading) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  async function sendFile(file: File) {
    if (busy || upload || blocked) return;
    if (!file.type.startsWith("video/") && !/\.(mkv|mp4|mov|webm|avi|m4v)$/i.test(file.name)) {
      setError("That isn't a video file (mp4, mkv, mov, webm…)");
      return;
    }
    if (file.size === 0) {
      setError("That file is empty");
      return;
    }
    setError(null);
    const { done, abort } = uploadVideo(file, (loaded) => setUpload((u) => (u ? { ...u, loaded } : u)), focus);
    setUpload({ name: file.name, size: file.size, loaded: 0, t0: Date.now(), abort });
    try {
      const job = await done;
      navigate(jobHref(job.id));
    } catch (e) {
      setError(errorText(e));
      setUpload(null);
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f) sendFile(f);
  }

  async function submit(value = url) {
    const v = value.trim();
    if (!v || busy) return;
    let parsed: URL | null = null;
    try {
      parsed = new URL(/^https?:\/\//i.test(v) ? v : `https://${v}`);
    } catch {
      /* not a URL */
    }
    if (!parsed || !parsed.hostname.includes(".")) {
      setError("That doesn't look like a link. Paste one like https://youtube.com/watch?v=…");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const job = await api.create(parsed.toString(), focus);
      navigate(jobHref(job.id));
    } catch (e) {
      setError(errorText(e));
      setBusy(false);
    }
  }

  // Fully automatic: pasting a YouTube link starts the job immediately (not while a focus note is being written).
  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text");
    if (looksLikeVideoUrl(pasted) && !blocked && !focusOpen) {
      e.preventDefault();
      setUrl(pasted.trim());
      submit(pasted);
    }
  }

  if (upload) {
    const p = upload.size ? upload.loaded / upload.size : 0;
    const pct = Math.floor(p * 100);
    const secs = (Date.now() - upload.t0) / 1000;
    const rate = secs > 1 ? upload.loaded / secs : 0;
    const left = rate > 0 ? (upload.size - upload.loaded) / rate : null;
    return (
      <div className="w-full">
        <div className="rounded-2xl border border-line-strong bg-surface p-4 shadow-card">
          <div className="mb-3 flex items-center gap-3">
            <FileVideo className="size-[18px] shrink-0 text-accent" />
            <span className="min-w-0 flex-1 truncate text-left text-[15px] font-medium">{upload.name}</span>
            <span className="font-mono text-xs text-muted">
              {p < 1 ? `${pct}% · ${fmtBytes(upload.loaded)} of ${fmtBytes(upload.size)}` : "Checking the file…"}
            </span>
            {p < 1 && (
              <button onClick={upload.abort} aria-label="Cancel upload" className="rounded-lg p-1 text-muted hover:bg-surface-2 hover:text-fg">
                <X className="size-4" />
              </button>
            )}
          </div>
          <ProgressBar value={p} active />
        </div>
        <p className="mt-2.5 min-h-5 px-1 text-sm text-faint">
          {rate > 0 && p < 1 ? `${fmtBytes(rate)}/s · ${fmtEta(left) ?? ""} · ` : ""}Processing starts as soon as it's in. Keep this tab open.
        </p>
      </div>
    );
  }

  return (
    <div className="w-full" onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDrag(false); }} onDrop={onDrop}>
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          submit();
        }}
        className={cx(
          "group flex items-center gap-2 rounded-2xl border bg-surface p-1.5 pl-4 shadow-card transition",
          drag ? "border-accent ring-4 ring-accent-soft" :
          error ? "border-err/50" : "border-line-strong focus-within:border-accent focus-within:ring-4 focus-within:ring-accent-soft",
        )}
      >
        <Link2 className="size-[18px] shrink-0 text-faint transition group-focus-within:text-accent" />
        <input
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setError(null);
          }}
          onPaste={onPaste}
          placeholder="Paste a YouTube link"
          aria-label="YouTube URL"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          disabled={busy}
          className="h-11 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint"
        />
        <Button type="submit" loading={busy} disabled={!url.trim() || !!blocked} className="h-11 rounded-xl px-5">
          {busy ? "Fetching" : "Generate"}
          {!busy && <ArrowRight className="size-4" />}
        </Button>
      </form>
      <div className="mt-2.5 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 px-1 text-sm">
        <p className={cx("min-h-5 text-left", error ? "text-err" : "text-faint")}>
          {error ?? blocked ?? (drag ? "Drop the video to upload it" : focusOpen ? "Press Generate when your note is ready." : "Pasting a link starts processing right away.")}
        </p>
        <button
          type="button"
          onClick={() => setFocusOpen(!focusOpen)}
          aria-expanded={focusOpen}
          className={cx("inline-flex items-center gap-1.5 rounded-lg px-1.5 py-0.5 font-medium transition hover:text-fg", focusOpen ? "text-accent" : "text-muted")}
        >
          <Sparkles className="size-3.5" /> {focusOpen ? "Hide focus note" : "Tell the AI what to look for"}
        </button>
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={busy || !!blocked}
          className="inline-flex items-center gap-1.5 rounded-lg px-1.5 py-0.5 font-medium text-muted transition hover:text-fg disabled:opacity-50"
        >
          <Upload className="size-3.5" /> or upload a video file
        </button>
        {focusOpen && (
          <div className="fade-in w-full basis-full rounded-xl border border-line bg-surface p-3 text-left shadow-card">
            <label htmlFor="focus-note" className="mb-1.5 block text-xs font-medium text-muted">
              What should the shorts be about? <span className="font-normal text-faint">Optional · the AI looks for these moments first</span>
            </label>
            <TextArea id="focus-note" value={focusText} onChange={(e) => setFocusText(e.target.value.slice(0, FOCUS_MAX))} rows={2}
              placeholder={`e.g. ${FOCUS_EXAMPLES.join(", ")}`} />
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <Segmented<FocusMode> value={focusMode} onChange={setFocusMode} options={[
                { value: "prefer", label: "These first", hint: "These moments first, plus the video's other strong moments" },
                { value: "only", label: "Only these", hint: "Just these moments (if nothing matches, you still get the best ones)" },
              ]} />
              <span className="font-mono text-[11px] text-faint">{focusText.length}/{FOCUS_MAX}</span>
            </div>
          </div>
        )}
        <input
          ref={fileInput}
          type="file"
          accept="video/*,.mkv"
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) sendFile(f); }}
        />
      </div>
    </div>
  );
}
