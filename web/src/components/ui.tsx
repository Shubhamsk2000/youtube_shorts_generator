import type { ButtonHTMLAttributes, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import { currentStage, overallProgress, type Job } from "../api";
import { cx } from "../lib";

export function Button({
  variant = "primary",
  loading,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger"; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx(
        "inline-flex h-9 items-center justify-center gap-2 rounded-xl px-3.5 text-sm font-medium transition",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-fg text-bg hover:opacity-85",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-fg",
        variant === "danger" && "bg-err text-white hover:opacity-90",
        className,
      )}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cx("rounded-2xl border border-line bg-surface shadow-card", className)}>{children}</section>
  );
}

export function CardHeader({ title, aside }: { title: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
      <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
      {aside && <div className="text-xs text-muted">{aside}</div>}
    </div>
  );
}

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

export function ProgressBar({ value, active }: { value: number | null; active?: boolean }) {
  const indeterminate = value == null;
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className={cx(
          "h-full rounded-full bg-accent transition-[width] duration-500 ease-out",
          active && "progress-stripe",
          indeterminate && "w-1/3 animate-pulse",
        )}
        style={indeterminate ? undefined : { width: `${Math.max(2, value * 100)}%` }}
      />
    </div>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="8" className="fill-fg" />
        <rect x="11" y="6" width="10" height="20" rx="2.5" fill="none" strokeWidth="2.2" className="stroke-bg" />
        <path d="M14.5 13.2v5.6l4.6-2.8z" className="fill-accent" />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight">Clipwise</span>
    </span>
  );
}
