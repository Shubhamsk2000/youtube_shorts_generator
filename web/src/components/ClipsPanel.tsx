import { useState } from "react";
import { Check, CircleAlert, Clock, Download, Flame, FolderOpen, LoaderCircle, RefreshCw, Sparkles, X } from "lucide-react";
import { api, isActive, isExporting, type Clip, type ClipsData, type ExportState, type Job, type Review } from "../api";
import { cx, fmtTime, usePoll } from "../lib";
import { Card, CardHeader } from "./ui";

type Filter = "all" | "approved" | "pending";

export function ClipsPanel({ job, onSeek, onJob }: { job: Job; onSeek: (t: number) => void; onJob: (j: Job) => void }) {
  const select = job.stages.find((s) => s.key === "select");
  const render = job.stages.find((s) => s.key === "render");
  const hasSelection = select?.status === "done";
  const rendering = render?.status === "running";

  const { data, setData } = usePoll<ClipsData | null>(
    () => (hasSelection ? api.clips(job.id) : Promise.resolve(null)),
    () => (rendering ? 3000 : null),
    `${job.id}:${select?.status}:${render?.status}:${job.export?.status}:${job.export?.files.length}:${job.export?.current}`,
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [exportError, setExportError] = useState<string | null>(null);
  const [repicking, setRepicking] = useState(false);

  async function exportClips(ids: string[] | null) {
    setExportError(null);
    try {
      onJob(await api.exportClips(job.id, ids));
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    }
  }

  async function repick() {
    if (!confirm("Ask Gemini to pick moments again? Current clips and reviews will be replaced.")) return;
    setRepicking(true);
    try {
      onJob(await api.reselect(job.id));
    } catch (e) {
      setExportError(e instanceof Error ? e.message : String(e));
    } finally {
      setRepicking(false);
    }
  }

  if (!hasSelection || !data) {
    return (
      <Card>
        <CardHeader title="Clips" aside={select?.status === "running" ? "Finding moments…" : undefined} />
        {select?.status === "running" ? <Finding message={select.message} /> : <Empty planned={select?.status === "planned"} />}
      </Card>
    );
  }

  const counts = {
    all: data.clips.length,
    approved: data.clips.filter((c) => c.review === "approved").length,
    pending: data.clips.filter((c) => !c.review).length,
  };
  const shown = data.clips.filter(
    (c) => filter === "all" || (filter === "approved" ? c.review === "approved" : !c.review),
  );

  async function setReview(clip: Clip, review: Review) {
    const next = clip.review === review ? null : review;
    setData((d) => d && { ...d, clips: d.clips.map((c) => (c.id === clip.id ? { ...c, review: next } : c)) });
    try {
      await api.review(job.id, clip.id, next);
    } catch {
      setData((d) => d && { ...d, clips: d.clips.map((c) => (c.id === clip.id ? { ...c, review: clip.review } : c)) });
    }
  }

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Clips <span className="font-mono text-xs font-normal text-muted">{data.clips.length}</span>
          </span>
        }
        aside={
          data.clips.length > 0 && (
            <div className="flex flex-wrap items-center justify-end gap-2">
            {counts.approved > 0 && (
              <button
                onClick={() => exportClips(null)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-2.5 py-1 text-xs font-semibold text-white transition hover:opacity-90"
                title="Render every approved clip in full quality (1080×1920)"
              >
                <Download className="size-3.5" /> Export approved ({counts.approved})
              </button>
            )}
            <div className="flex rounded-lg bg-surface-2 p-0.5" role="tablist">
              {(["all", "pending", "approved"] as const).map((f) => (
                <button
                  key={f}
                  role="tab"
                  aria-selected={filter === f}
                  onClick={() => setFilter(f)}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-xs font-medium capitalize transition",
                    filter === f ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg",
                  )}
                >
                  {f === "pending" ? "To review" : f} <span className="font-mono text-faint">{counts[f]}</span>
                </button>
              ))}
            </div>
            </div>
          )
        }
      />

      <ExportBanner ex={job.export} error={exportError} />

      {data.note && (
        <div className="mx-5 mt-4 flex gap-2.5 rounded-xl border border-warn/30 bg-warn/5 p-3 text-xs leading-relaxed text-muted">
          <CircleAlert className="mt-px size-3.5 shrink-0 text-warn" />
          <span>{data.note}</span>
        </div>
      )}

      {data.clips.length === 0 ? (
        <p className="px-5 py-10 text-center text-sm text-muted">
          No moment passed the checks{data.candidates ? ` (${data.candidates} proposed)` : ""}. Try lowering{" "}
          <code className="font-mono text-xs">select.min_score</code> in config.yaml.
        </p>
      ) : shown.length === 0 ? (
        <p className="px-5 py-10 text-center text-sm text-muted">Nothing here yet.</p>
      ) : (
        <div className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((c) => (
            <ClipCard
              key={c.id}
              clip={c}
              ex={job.export}
              onSeek={onSeek}
              onReview={(r) => setReview(c, r)}
              onExport={() => exportClips([c.id])}
            />
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line px-5 py-3 text-xs text-faint">
        <span>
          {data.source === "gemini" ? `Picked by ${data.model} from ${data.candidates} proposals · ` : ""}
          Quick low-res previews · use Export HD for the upload-quality version
        </span>
        <button
          onClick={repick}
          disabled={repicking || isActive(job.status) || isExporting(job)}
          className="inline-flex items-center gap-1 rounded-md text-muted transition hover:text-fg disabled:opacity-40"
          title="Ask Gemini again (uses the latest prompt), then reframe, caption and preview"
        >
          <RefreshCw className={cx("size-3", repicking && "animate-spin")} /> Re-pick moments
        </button>
      </div>
    </Card>
  );
}

function ExportBanner({ ex, error }: { ex: ExportState | null; error: string | null }) {
  if (error)
    return (
      <div className="mx-5 mt-4 flex gap-2.5 rounded-xl border border-err/30 bg-err/5 p-3 text-xs text-muted">
        <CircleAlert className="mt-px size-3.5 shrink-0 text-err" /> {error}
      </div>
    );
  if (!ex) return null;
  const active = ex.status === "queued" || ex.status === "running";
  return (
    <div
      className={cx(
        "mx-5 mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-xs",
        ex.status === "failed" ? "border-err/30 bg-err/5" : "border-line bg-surface-2",
      )}
    >
      {active ? (
        <LoaderCircle className="size-3.5 animate-spin text-accent" />
      ) : ex.status === "failed" ? (
        <CircleAlert className="size-3.5 text-err" />
      ) : (
        <Check className="size-3.5 text-ok" />
      )}
      <span className="font-medium">
        {active ? "Exporting HD" : ex.status === "failed" ? "Export failed" : "HD export"}
        {active && ex.progress != null ? ` · ${Math.round(ex.progress * 100)}%` : ""}
      </span>
      {ex.message && <span className="text-muted">{ex.message}</span>}
      {ex.dir && !active && (
        <span className="flex min-w-0 items-center gap-1 font-mono text-[11px] text-faint" title="Exported files + upload text">
          <FolderOpen className="size-3 shrink-0" />
          <span className="truncate">{ex.dir}</span>
        </span>
      )}
    </div>
  );
}

function ClipCard({
  clip: c,
  ex,
  onSeek,
  onReview,
  onExport,
}: {
  clip: Clip;
  ex: ExportState | null;
  onSeek: (t: number) => void;
  onReview: (r: Review) => void;
  onExport: () => void;
}) {
  const exporting = ex?.status === "running" && ex.current === c.id;
  const queued = !!ex && (ex.status === "running" || ex.status === "queued") && ex.queue.includes(c.id);
  return (
    <article
      className={cx(
        "fade-in flex flex-col overflow-hidden rounded-xl border bg-surface transition",
        c.review === "approved" ? "border-ok/50 ring-1 ring-ok/30" : "border-line",
        c.review === "rejected" && "opacity-55",
      )}
    >
      <div className="relative aspect-[9/16] bg-black">
        {c.ready && c.url ? (
          <video src={`${c.url}#t=0.1`} controls preload="metadata" playsInline className="absolute inset-0 size-full" />
        ) : (
          <div className="shimmer absolute inset-0 grid place-items-center">
            <span className="flex items-center gap-1.5 text-xs text-white/60">
              <LoaderCircle className="size-3.5 animate-spin" /> Rendering
            </span>
          </div>
        )}
        <span className="pointer-events-none absolute top-2 left-2 rounded-md bg-black/60 px-1.5 py-0.5 font-mono text-[11px] text-white backdrop-blur">
          #{c.rank}
        </span>
        <span
          className={cx(
            "pointer-events-none absolute top-2 right-2 rounded-md px-1.5 py-0.5 font-mono text-[11px] font-semibold backdrop-blur",
            c.score >= 7 ? "bg-accent text-white" : "bg-black/60 text-white",
          )}
          title="Overall score (0-10)"
        >
          {c.score.toFixed(1)}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-2.5 p-3.5">
        <h3 className="text-sm leading-snug font-semibold text-balance">{c.title}</h3>
        {(c.hook_text || (c.heat != null && c.heat >= 0.6)) && (
          <div className="flex flex-wrap gap-1.5">
            {c.hook_text && (
              <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium" title="On-screen hook text">
                {c.hook_text}
              </span>
            )}
            {c.heat != null && c.heat >= 0.6 && (
              <span
                className="inline-flex items-center gap-0.5 rounded-md bg-warn/10 px-1.5 py-0.5 text-[11px] font-medium text-warn"
                title="Viewers rewatched this part a lot (YouTube Most replayed)"
              >
                <Flame className="size-3" /> Most replayed
              </span>
            )}
          </div>
        )}
        <p className="line-clamp-2 text-xs leading-relaxed text-muted italic">“{c.hook}”</p>
        <p className="line-clamp-3 text-xs leading-relaxed text-muted" title={c.reason}>
          {c.reason}
        </p>
        <Scores scores={c.scores} />

        <div className="mt-auto flex items-center justify-between gap-2 pt-1">
          <button
            onClick={() => onSeek(c.start)}
            className="inline-flex items-center gap-1 rounded-md font-mono text-[11px] text-muted transition hover:text-fg"
            title="Play this moment in the source video"
          >
            <Clock className="size-3" /> {fmtTime(c.start)} · {Math.round(c.duration)}s
          </button>
          <div className="flex items-center gap-1">
            {c.final_url && !exporting && !queued ? (
              <a
                href={c.final_url}
                download
                className="inline-flex h-7 items-center gap-1 rounded-lg border border-ok/50 px-2 text-[11px] font-semibold text-ok transition hover:bg-ok/10"
                title="Download the full-quality 1080×1920 version"
              >
                <Download className="size-3.5" /> HD
              </a>
            ) : exporting || queued ? (
              <span className="inline-flex h-7 items-center gap-1 rounded-lg px-2 font-mono text-[11px] text-muted">
                <LoaderCircle className="size-3.5 animate-spin" />
                {exporting ? `${Math.round((ex?.progress ?? 0) * 100)}%` : "queued"}
              </span>
            ) : (
              <button
                onClick={onExport}
                className="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2 text-[11px] font-semibold text-muted transition hover:border-line-strong hover:text-fg"
                title="Render this clip in full quality (1080×1920, loudness-normalised)"
              >
                <Download className="size-3.5" /> Export HD
              </button>
            )}
            <ReviewButton active={c.review === "rejected"} kind="rejected" onClick={() => onReview("rejected")} />
            <ReviewButton active={c.review === "approved"} kind="approved" onClick={() => onReview("approved")} />
          </div>
        </div>
      </div>
    </article>
  );
}

function ReviewButton({ kind, active, onClick }: { kind: "approved" | "rejected"; active: boolean; onClick: () => void }) {
  const ok = kind === "approved";
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      aria-label={ok ? "Approve clip" : "Reject clip"}
      title={ok ? "Approve" : "Reject"}
      className={cx(
        "grid size-7 place-items-center rounded-lg border transition",
        active
          ? ok
            ? "border-ok bg-ok text-white"
            : "border-err bg-err text-white"
          : "border-line text-muted hover:border-line-strong hover:text-fg",
      )}
    >
      {ok ? <Check className="size-3.5" strokeWidth={2.5} /> : <X className="size-3.5" strokeWidth={2.5} />}
    </button>
  );
}

const SCORE_LABELS: [keyof Clip["scores"], string][] = [
  ["hook", "Hook"],
  ["standalone", "Standalone"],
  ["payoff", "Payoff"],
  ["energy", "Energy"],
];

function Scores({ scores }: { scores: Clip["scores"] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
      {SCORE_LABELS.map(([k, label]) => (
        <div key={k} className="min-w-0">
          <div className="flex items-baseline justify-between text-[10px] text-faint">
            <dt>{label}</dt>
            <dd className="font-mono">{scores[k]}</dd>
          </div>
          <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-fg/60" style={{ width: `${scores[k] * 10}%` }} />
          </div>
        </div>
      ))}
    </dl>
  );
}

function Finding({ message }: { message: string | null }) {
  return (
    <div className="p-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="shimmer aspect-[9/16] rounded-xl" />
        ))}
      </div>
      {message && <p className="mt-4 text-center text-xs text-muted">{message}</p>}
    </div>
  );
}

function Empty({ planned }: { planned: boolean }) {
  return (
    <div className="flex flex-col items-center gap-5 px-6 py-10 sm:flex-row sm:items-center sm:gap-8 sm:px-8">
      <div className="flex shrink-0 gap-2" aria-hidden>
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="aspect-[9/16] w-12 rounded-lg border border-dashed border-line-strong bg-surface-2"
            style={{ transform: `rotate(${(i - 1) * 6}deg) translateY(${i === 1 ? -4 : 0}px)` }}
          />
        ))}
      </div>
      <div className="text-center sm:text-left">
        <p className="flex items-center justify-center gap-1.5 text-sm font-medium sm:justify-start">
          <Sparkles className="size-4 text-accent" /> Vertical clips land here
        </p>
        <p className="mt-1 max-w-md text-sm text-muted">
          {planned
            ? "AI moment detection ships in the next milestone. The transcript below is what it will work from."
            : "Once the transcript is ready, Gemini picks the moments worth posting. Each clip shows its score, the hook and why it was picked, ready for you to approve."}
        </p>
      </div>
    </div>
  );
}
