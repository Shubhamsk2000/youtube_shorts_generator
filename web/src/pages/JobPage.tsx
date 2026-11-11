import { useState } from "react";
import { ArrowLeft, CircleAlert, ExternalLink, RotateCcw, Trash2 } from "lucide-react";
import { api, isActive, isExporting } from "../api";
import { ClipsPanel } from "../components/ClipsPanel";
import { Pipeline } from "../components/Pipeline";
import { Player, type Seek } from "../components/Player";
import { TranscriptPanel } from "../components/TranscriptPanel";
import { Button, StatusBadge } from "../components/ui";
import { fmtTime, Link, navigate, timeAgo, usePoll } from "../lib";

export function JobPage({ id }: { id: string }) {
  const { data: job, error, setData } = usePollJob(id);
  const [seek, setSeek] = useState<Seek | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState<"retry" | "delete" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  if (error && !job) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <p className="text-sm font-medium">{error}</p>
        <Link to="/" className="mt-3 inline-block text-sm text-accent hover:underline">
          Back to projects
        </Link>
      </div>
    );
  }
  if (!job) return <PageSkeleton />;

  async function retry() {
    setBusy("retry");
    setActionError(null);
    try {
      setData(await api.retry(id));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("delete");
    try {
      await api.remove(id);
      navigate("/");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
      setBusy(null);
      setConfirmDelete(false);
    }
  }

  const canRetry = job.status === "failed" || job.status === "interrupted";
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
          <h1 className="text-2xl leading-tight font-semibold tracking-tight text-balance sm:text-3xl">{job.title}</h1>
          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted">
            <StatusBadge job={job} />
            {job.duration != null && <span className="font-mono text-xs">{fmtTime(job.duration)}</span>}
            {job.height && (
              <span className="font-mono text-xs">
                {job.height}p{job.fps ? Math.round(job.fps) : ""}
              </span>
            )}
            <span className="text-xs">{timeAgo(job.created_at)}</span>
            {job.is_youtube && (
              <a
                href={job.source}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs hover:text-fg"
              >
                Source <ExternalLink className="size-3" />
              </a>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {canRetry && (
            <Button onClick={retry} loading={busy === "retry"}>
              <RotateCcw className="size-4" /> {job.status === "failed" ? "Retry" : "Resume"}
            </Button>
          )}
          {confirmDelete ? (
            <span className="fade-in flex items-center gap-1.5">
              <span className="text-sm text-muted">Delete files?</span>
              <Button variant="danger" onClick={remove} loading={busy === "delete"}>
                Delete
              </Button>
              <Button variant="ghost" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              variant="ghost"
              onClick={() => setConfirmDelete(true)}
              disabled={isActive(job.status)}
              title={isActive(job.status) ? "Can't delete while processing" : "Delete project and its files"}
              aria-label="Delete project"
            >
              <Trash2 className="size-4" />
            </Button>
          )}
        </div>
      </header>

      {(job.error || actionError) && (
        <div className="fade-in mt-5 flex gap-3 rounded-2xl border border-err/30 bg-err/5 p-4 text-sm">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-err" />
          <div className="min-w-0">
            <p className="font-medium text-err">Something went wrong</p>
            <p className="mt-1 font-mono text-xs leading-relaxed break-words text-muted">{actionError ?? job.error}</p>
            {job.status === "failed" && (
              <p className="mt-2 text-xs text-muted">Finished steps are saved. Retry picks up where it stopped.</p>
            )}
          </div>
        </div>
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <aside className="lg:sticky lg:top-20 lg:order-2 lg:self-start">
          <Pipeline job={job} />
        </aside>
        <div className="min-w-0 space-y-5 lg:order-1">
          <Player job={job} seek={seek} />
          <ClipsPanel job={job} onSeek={seekTo} onJob={setData} />
          <TranscriptPanel job={job} onSeek={seekTo} />
        </div>
      </div>
    </div>
  );
}

function usePollJob(id: string) {
  return usePoll(() => api.job(id), (j) => (!j || isActive(j.status) || isExporting(j) ? 1000 : null), id);
}

function PageSkeleton() {
  return (
    <div className="mx-auto max-w-6xl px-4 pt-6 sm:px-6">
      <div className="shimmer h-4 w-20 rounded" />
      <div className="shimmer mt-6 h-8 w-2/3 rounded-lg" />
      <div className="mt-6 grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="shimmer aspect-video rounded-2xl" />
        <div className="shimmer h-80 rounded-2xl" />
      </div>
    </div>
  );
}
