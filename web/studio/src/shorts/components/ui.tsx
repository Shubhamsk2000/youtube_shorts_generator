import { currentStage, overallProgress, type Job } from "../api";
import { cx } from "../lib";

export { Button, Card, CardHeader, ProgressBar } from "@kit/ui";

const STATUS_STYLE: Record<Job["status"], { dot: string; text: string; label: string }> = {
  queued: { dot: "bg-faint", text: "text-muted", label: "Queued" },
  running: { dot: "bg-accent animate-pulse", text: "text-accent", label: "Processing" },
  done: { dot: "bg-ok", text: "text-ok", label: "Ready" },
  failed: { dot: "bg-err", text: "text-err", label: "Failed" },
  interrupted: { dot: "bg-warn", text: "text-warn", label: "Paused" },
};

export function StatusBadge({ job }: { job: Job }) {
  const s = STATUS_STYLE[job.status];
  const stage = currentStage(job);
  const pct = Math.round(overallProgress(job) * 100);
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2 py-0.5 text-xs font-medium",
        s.text,
      )}
    >
      <span className={cx("size-1.5 rounded-full", s.dot)} />
      {job.status === "running" && stage ? `${stage.label} · ${pct}%` : s.label}
    </span>
  );
}

