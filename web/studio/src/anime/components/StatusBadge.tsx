import { cx } from "@kit/lib";
import { currentStage, exporting, overallProgress, type Job } from "../api";

const STATUS: Record<Job["status"], { dot: string; text: string; label: string }> = {
  queued: { dot: "bg-faint", text: "text-muted", label: "Queued" },
  running: { dot: "bg-accent animate-pulse", text: "text-accent", label: "Working" },
  done: { dot: "bg-ok", text: "text-ok", label: "Ready" },
  failed: { dot: "bg-err", text: "text-err", label: "Failed" },
  interrupted: { dot: "bg-warn", text: "text-warn", label: "Paused" },
};

export function StatusBadge({ job }: { job: Job }) {
  const s = STATUS[job.status];
  const stage = currentStage(job);
  const pct = Math.round(overallProgress(job) * 100);
  const label =
    job.status === "running" && stage
      ? job.task === "render"
        ? `Re-rendering · ${Math.round((stage.progress ?? 0) * 100)}%`
        : `${stage.label} · ${pct}%`
      : exporting(job)
        ? "Exporting"
        : s.label;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-xs font-medium whitespace-nowrap",
        exporting(job) && job.status === "done" ? "text-accent" : s.text,
      )}
    >
      <span className={cx("size-1.5 rounded-full", exporting(job) ? "bg-accent animate-pulse" : s.dot)} />
      {label}
    </span>
  );
}
