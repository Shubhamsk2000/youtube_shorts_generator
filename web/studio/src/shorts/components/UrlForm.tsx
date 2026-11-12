import { useRef, useState, type ClipboardEvent, type DragEvent, type FormEvent } from "react";
import { ArrowRight, FileVideo, Link2, Upload, X } from "lucide-react";
import { api, uploadVideo } from "../api";
import { cx, navigate } from "../lib";
import { Button, ProgressBar } from "./ui";

const looksLikeVideoUrl = (s: string) =>
  /^https?:\/\/(www\.|m\.)?(youtube\.com\/(watch|shorts|live)|youtu\.be\/)/i.test(s.trim());

export function UrlForm({ blocked }: { blocked?: string | null }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [upload, setUpload] = useState<{ name: string; size: number; p: number; abort: () => void } | null>(null);
  const [drag, setDrag] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  async function sendFile(file: File) {
    if (busy || upload || blocked) return;
    if (!file.type.startsWith("video/") && !/\.(mkv|mp4|mov|webm|avi|m4v)$/i.test(file.name)) {
      setError("That isn't a video file (mp4, mkv, mov, webm…)");
      return;
    }
    setError(null);
    const { done, abort } = uploadVideo(file, (p) => setUpload((u) => (u ? { ...u, p } : u)));
    setUpload({ name: file.name, size: file.size, p: 0, abort });
    try {
      const job = await done;
      navigate(`/shorts/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
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
    if (!/^https?:\/\//i.test(v)) {
      setError("Paste a full link, like https://youtube.com/watch?v=…");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const job = await api.create(v);
      navigate(`/shorts/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  // Fully automatic: pasting a YouTube link starts the job immediately.
  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text");
    if (looksLikeVideoUrl(pasted) && !blocked) {
      e.preventDefault();
      setUrl(pasted.trim());
      submit(pasted);
    }
  }

  if (upload) {
    const pct = Math.round(upload.p * 100);
    return (
      <div className="w-full">
        <div className="rounded-2xl border border-line-strong bg-surface p-4 shadow-card">
          <div className="mb-3 flex items-center gap-3">
            <FileVideo className="size-[18px] shrink-0 text-accent" />
            <span className="min-w-0 flex-1 truncate text-left text-[15px] font-medium">{upload.name}</span>
            <span className="font-mono text-xs text-muted">
              {upload.p < 1 ? `${pct}% of ${(upload.size / 1e6).toFixed(0)} MB` : "Starting…"}
            </span>
            {upload.p < 1 && (
              <button onClick={upload.abort} aria-label="Cancel upload" className="rounded-lg p-1 text-muted hover:bg-surface-2 hover:text-fg">
                <X className="size-4" />
              </button>
            )}
          </div>
          <ProgressBar value={upload.p} active />
        </div>
        <p className="mt-2.5 min-h-5 px-1 text-sm text-faint">Uploading to this machine: processing starts as soon as it's in.</p>
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
          {error ?? blocked ?? (drag ? "Drop the video to upload it" : "Pasting a link starts processing right away.")}
        </p>
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          disabled={busy || !!blocked}
          className="inline-flex items-center gap-1.5 rounded-lg px-1.5 py-0.5 font-medium text-muted transition hover:text-fg disabled:opacity-50"
        >
          <Upload className="size-3.5" /> or upload a video file
        </button>
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
