import { useState } from "react";
import { stagesProgress } from "@kit/types";
import { ArrowLeft, CircleAlert, Copy, ExternalLink, RefreshCw, RotateCcw, Square, Trash2 } from "lucide-react";
import { useConfirm, useToast, ErrorBoundary } from "@kit/feedback";
import { api, clockSkew, currentStage, isActive, isExporting, overallProgress, type Job } from "../api";
import { ClipsPanel } from "../components/ClipsPanel";
import { FocusPanel } from "../components/FocusPanel";
import { LayoutPanel } from "../components/LayoutPanel";
import { PipelineSteps } from "@kit/Pipeline";
import { Console } from "../components/Console";
import { Player, type Seek } from "../components/Player";
import { TranscriptPanel } from "../components/TranscriptPanel";
import { Button, Card, ProgressBar, StatusBadge } from "../components/ui";
import { copyText, cx, errorText, fmtDate, fmtDuration, fmtEta, fmtTime, Link, navigate, secondsBetween, timeAgo, useNow, usePoll, useStored } from "../lib";

export function JobPage({ id }: { id: string }) {
  const { data: job, error, setData, updatedAt, stale } = usePoll(
    () => api.job(id),
    (j) => (!j || isActive(j.status) || isExporting(j) ? 1000 : 15000),
    id,
  );
  const [seek, setSeek] = useState<Seek | null>(null);
  const [busy, setBusy] = useState<"retry" | "delete" | "stop" | "rerender" | null>(null);
  const toast = useToast();
  const confirm = useConfirm();
  const skew = clockSkew(job, updatedAt);

  if (error && !job) {
    const missing = error === "Job not found";
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="text-sm font-medium">{missing ? "This project doesn't exist (it may have been deleted)." : error}</p>
        {!missing && <p className="mt-1 text-xs text-muted">Retrying automatically…</p>}
        <Link to="/" className="mt-3 inline-block text-sm text-accent hover:underline">Back to projects</Link>
      </div>
    );
  }
  if (!job) return <PageSkeleton />;

  async function act(kind: NonNullable<typeof busy>, fn: () => Promise<Job | void>, ok?: string) {
    setBusy(kind);
    try {
      const j = await fn();
      if (j) setData(j);
      if (ok) toast.ok(ok);
    } catch (e) {
      toast.err("That didn't work", errorText(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    const hasExports = job!.stats.exported > 0;
    const ok = await confirm({
      title: "Delete this project?",
      body: <>The downloaded video, transcript, clips and previews of <b className="text-fg">{job!.title}</b> are deleted. This can't be undone.</>,
      confirm: "Delete project",
      danger: true,
      check: hasExports ? `Also delete the ${job!.stats.exported} exported HD clip${job!.stats.exported === 1 ? "" : "s"}` : undefined,
    });
    if (!ok) return;
    setBusy("delete");
    try {
      await api.remove(id, ok.checked);
      toast.ok("Project deleted");
      navigate("/");
    } catch (e) {
      toast.err("Couldn't delete", errorText(e));
      setBusy(null);
    }
  }

  async function rerender() {
    const ok = await confirm({
      title: "Re-render with the current settings?",
      body: "Framing, captions and previews are rebuilt with your current Settings and Layout. Moments, transcript and reviews are kept.",
      confirm: "Re-render",
    });
    if (ok) act("rerender", () => api.rerender(id), "Re-render queued");
  }

  const active = isActive(job.status);
  const canRetry = job.status === "failed" || job.status === "interrupted";
  const hasClips = job.stats.clips > 0;
  const seekTo = (t: number) => {
    setSeek((s) => ({ t, n: (s?.n ?? 0) + 1 }));
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <div className="mx-auto max-w-6xl px-4 pt-6 pb-24 sm:px-6">
      <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-muted transition hover:text-fg">
        <ArrowLeft className="size-4" /> Projects
      </Link>

      <header className="mt-5 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl leading-tight font-semibold tracking-tight text-balance break-words sm:text-3xl">{job.title}</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted">
            <StatusBadge job={job} />
            {job.duration != null && <span className="font-mono text-xs" title="Source length">{fmtTime(job.duration)}</span>}
            {job.height && (
              <span className="font-mono text-xs" title={`${job.width}×${job.height}${job.fps ? ` at ${job.fps.toFixed(2)} fps` : ""}`}>
                {job.height}p{job.fps ? Math.round(job.fps) : ""}
              </span>
            )}
            <span className="text-xs" title={fmtDate(job.created_at)}>{timeAgo(job.created_at)}</span>
            {job.is_youtube && (
              <a href={job.source} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs hover:text-fg">
                Source <ExternalLink className="size-3" />
              </a>
            )}
            {stale && <span className="text-xs text-warn" title={error}>Reconnecting…</span>}
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          {active && (
            <Button variant="outline" onClick={() => act("stop", () => api.stop(id), job.status === "queued" ? "Removed from the queue" : "Stopping at the next checkpoint…")}
              loading={busy === "stop"} disabled={job.stopping} title="Finished steps are kept: Resume continues from here">
              <Square className="size-3.5 fill-current" /> {job.stopping ? "Stopping…" : "Stop"}
            </Button>
          )}
          {canRetry && (
            <Button onClick={() => act("retry", () => api.retry(id))} loading={busy === "retry"}>
              <RotateCcw className="size-4" /> {job.status === "failed" ? "Retry" : "Resume"}
            </Button>
          )}
          {!active && hasClips && (
            <Button variant="ghost" onClick={rerender} loading={busy === "rerender"} disabled={isExporting(job)}
              title="Rebuild framing, captions and previews with the current settings">
              <RefreshCw className="size-4" /> <span className="hidden sm:inline">Re-render</span>
            </Button>
          )}
          <Button variant="ghost" onClick={remove} loading={busy === "delete"} disabled={active || isExporting(job)}
            title={active ? "Stop processing before deleting" : "Delete project and its files"} aria-label="Delete project">
            <Trash2 className="size-4" />
          </Button>
        </div>
      </header>

      {job.error && <ErrorPanel job={job} />}
      {active && <Progress job={job} skew={skew} />}

      <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <aside className="lg:sticky lg:top-20 lg:order-2 lg:self-start">
          <div className="space-y-5">
            <PipelinePanel job={job} skew={skew} />
            <ErrorBoundary label="Focus"><FocusPanel job={job} onJob={setData} /></ErrorBoundary>
            <ErrorBoundary label="Layout"><LayoutPanel job={job} onJob={setData} /></ErrorBoundary>
          </div>
        </aside>
        <div className="min-w-0 space-y-5 lg:order-1">
          <ErrorBoundary label="Player"><Player job={job} seek={seek} /></ErrorBoundary>
          <ErrorBoundary label="Clips"><ClipsPanel job={job} onSeek={seekTo} onJob={setData} /></ErrorBoundary>
          <ErrorBoundary label="Transcript"><TranscriptPanel job={job} onSeek={seekTo} /></ErrorBoundary>
        </div>
      </div>
    </div>
  );
}

/** The steps, or the live console of what the pipeline is doing. */
function PipelinePanel({ job, skew }: { job: Job; skew: number }) {
  const [tab, setTab] = useStored<"steps" | "console">("project.pipelineTab", "steps");
  const [issues, setIssues] = useState(0);
  const pct = Math.floor(stagesProgress(job.stages) * 100);
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
        <div role="tablist" className="inline-flex rounded-lg bg-surface-2 p-0.5 text-xs font-medium">
          {(["steps", "console"] as const).map((k) => (
            <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
              className={cx("inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 transition",
                tab === k ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg")}>
              {k === "steps" ? "Pipeline" : "Console"}
              {k === "console" && issues > 0 && <span className="size-1.5 rounded-full bg-warn" title={`${issues} warnings or errors`} />}
            </button>
          ))}
        </div>
        <span className="font-mono text-xs text-muted" title="Weighted by how long each step usually takes">{pct}%</span>
      </div>
      <div className={tab === "steps" ? "" : "hidden"}><PipelineSteps stages={job.stages} skew={skew} /></div>
      <div className={tab === "console" ? "" : "hidden"}><Console job={job} onIssues={setIssues} /></div>
    </Card>
  );
}

function Progress({ job, skew }: { job: Job; skew: number }) {
  const now = useNow(1000) + skew;
  const stage = currentStage(job);
  const pct = overallProgress(job);
  const firstStart = job.stages.filter((s) => s.started_at).map((s) => s.started_at!).sort()[0] ?? null;
  const elapsed = job.status === "running" ? secondsBetween(firstStart, null, now) : null;
  const eta = fmtEta(stage?.eta_s);
  return (
    <Card className="mt-5 p-4 fade-in">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-sm font-medium">
          {job.stopping ? "Stopping…" : job.status === "queued" ? "Waiting for the worker" : stage ? stage.label : "Starting…"}
          {stage?.progress != null && <span className="ml-2 font-mono text-xs text-muted">{Math.floor(stage.progress * 100)}% of this step</span>}
        </p>
        <p className="font-mono text-xs text-muted">
          <span className="text-fg">{Math.floor(pct * 100)}%</span> overall
          {elapsed != null && elapsed > 1 && <> · {fmtDuration(elapsed)} so far</>}
          {eta && <> · step: {eta}</>}
        </p>
      </div>
      <div className="mt-3"><ProgressBar value={pct} active={job.status === "running"} /></div>
      {stage?.message && <p className="mt-2 truncate text-xs text-muted" title={stage.message}>{stage.message}</p>}
      {job.status === "queued" && <p className="mt-2 text-xs text-muted">One video is processed at a time; this starts as soon as the current one finishes.</p>}
    </Card>
  );
}

function ErrorPanel({ job }: { job: Job }) {
  const toast = useToast();
  const failed = job.stages.find((s) => s.status === "failed");
  const report = [`Project: ${job.title} (${job.id})`, `Source: ${job.source}`, failed ? `Step: ${failed.label}` : "", `Error: ${job.error}`]
    .filter(Boolean).join("\n");
  return (
    <div className="fade-in mt-5 flex gap-3 rounded-2xl border border-err/30 bg-err/5 p-4 text-sm" role="alert">
      <CircleAlert className="mt-0.5 size-4 shrink-0 text-err" />
      <div className="min-w-0 flex-1">
        <p className="font-medium text-err">{failed ? `“${failed.label}” failed` : "Something went wrong"}</p>
        <p className="mt-1 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-muted">{job.error}</p>
        <p className="mt-2 text-xs text-muted">{hint(job.error ?? "")} Finished steps are saved: Retry picks up where it stopped.</p>
      </div>
      <button onClick={async () => ((await copyText(report)) ? toast.ok("Error details copied") : toast.err("Copy failed"))}
        className="h-fit shrink-0 rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-fg" title="Copy error details" aria-label="Copy error details">
        <Copy className="size-4" />
      </button>
    </div>
  );
}

/** A next step for the errors people actually hit. */
function hint(err: string): string {
  const e = err.toLowerCase();
  if (e.includes("sign in") || e.includes("bot") || e.includes("cookies")) return "YouTube wants a signed-in session: add data/shorts/cookies.txt (see README).";
  if (e.includes("disk") || e.includes("no space")) return "Free some disk space (System → Storage).";
  if (e.includes("memory") || e.includes("exit code -9") || e.includes("killed")) return "The machine ran out of memory: close other jobs or use a smaller Whisper model.";
  if (e.includes("quota") || e.includes("429") || e.includes("rate")) return "The AI provider's free quota ran out: wait a bit, or pick another model.";
  if (e.includes("api key") || e.includes("api_key")) return "An AI API key is missing in .env.";
  if (e.includes("private") || e.includes("unavailable") || e.includes("removed")) return "The video isn't publicly available.";
  return "";
}

function PageSkeleton() {
  return (
    <div className="mx-auto max-w-6xl px-4 pt-6 sm:px-6">
      <div className="shimmer h-4 w-20 rounded" />
      <div className="shimmer mt-6 h-8 w-2/3 rounded-lg" />
      <div className="mt-6 grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="shimmer aspect-video rounded-2xl" />
        <div className="shimmer h-80 rounded-2xl" />
      </div>
    </div>
  );
}

