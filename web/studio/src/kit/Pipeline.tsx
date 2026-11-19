import { Check, LoaderCircle, X } from "lucide-react";
import { cx, fmtDuration, fmtEta, secondsBetween, useNow } from "./lib";
import { stagesProgress, type Stage } from "./types";
import { Card, CardHeader, ProgressBar } from "./ui";

/** `skew`: server clock minus browser clock (ms), so elapsed times are right even if the clocks differ. */
export function Pipeline({ stages, title = "Pipeline", skew = 0 }: { stages: Stage[]; title?: string; skew?: number }) {
  const pct = Math.floor(stagesProgress(stages) * 100);
  const running = stages.some((s) => s.status === "running");
  const now = useNow(1000, running) + skew;
  return (
    <Card>
      <CardHeader title={title} aside={<span className="font-mono" title="Weighted by how long each step usually takes">{pct}%</span>} />
      <ol className="px-5 py-4">
        {stages.map((s, i) => (
          <StageRow key={s.key} stage={s} last={i === stages.length - 1} now={now} />
        ))}
      </ol>
    </Card>
  );
}

function StageRow({ stage: s, last, now }: { stage: Stage; last: boolean; now: number }) {
  const secs = s.status === "done" || s.status === "running" ? secondsBetween(s.started_at, s.finished_at, now) : null;
  const time = secs != null && secs >= 1 ? fmtDuration(secs) : null;
  const eta = s.status === "running" ? fmtEta(s.eta_s) : null;
  return (
    <li className="relative flex gap-3.5 pb-5 last:pb-0">
      {!last && (
        <span
          className={cx(
            "absolute top-7 bottom-1 left-[11px] w-px",
            s.status === "done" ? "bg-fg/25" : "bg-line-strong",
            s.status === "planned" && "bg-transparent border-l border-dashed border-line-strong",
          )}
        />
      )}
      <StageIcon status={s.status} />
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="flex items-center justify-between gap-2">
          <span
            className={cx(
              "text-sm font-medium",
              s.status === "planned" || s.status === "pending" ? "text-muted" : "text-fg",
            )}
          >
            {s.label}
          </span>
          {s.status === "planned" ? (
            <span className="rounded-full border border-dashed border-line-strong px-2 py-px text-[11px] text-faint">
              Next milestone
            </span>
          ) : (
            time && <span className="font-mono text-xs text-faint">{time}</span>
          )}
        </div>
        {s.status === "running" && (
          <div className="mt-2">
            <ProgressBar value={s.progress} active />
            {(s.progress != null || eta) && (
              <div className="mt-1 flex justify-between font-mono text-[11px] text-faint">
                <span>{s.progress != null ? `${Math.floor(s.progress * 100)}%` : ""}</span>
                <span>{eta}</span>
              </div>
            )}
          </div>
        )}
        {s.message && s.status !== "planned" && (
          <p
            className={cx(
              "mt-1.5 line-clamp-2 text-xs leading-relaxed break-words",
              s.status === "failed" ? "text-err" : "text-muted",
            )}
            title={s.message}
          >
            {s.message}
          </p>
        )}
      </div>
    </li>
  );
}

function StageIcon({ status }: { status: Stage["status"] }) {
  const base = "relative z-10 grid size-6 shrink-0 place-items-center rounded-full";
  switch (status) {
    case "done":
      return (
        <span className={cx(base, "bg-fg text-bg")}>
          <Check className="size-3.5" strokeWidth={3} />
        </span>
      );
    case "running":
      return (
        <span className={cx(base, "bg-accent-soft text-accent ring-1 ring-accent/40")}>
          <LoaderCircle className="size-3.5 animate-spin" strokeWidth={2.5} />
        </span>
      );
    case "failed":
      return (
        <span className={cx(base, "bg-err text-white")}>
          <X className="size-3.5" strokeWidth={3} />
        </span>
      );
    case "planned":
      return <span className={cx(base, "border border-dashed border-line-strong bg-bg")} />;
    default:
      return <span className={cx(base, "border border-line-strong bg-surface")} />;
  }
}
