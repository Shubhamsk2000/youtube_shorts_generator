import { Film } from "lucide-react";
import { overallProgress, thumbnailUrl, type Job } from "../api";
import { fmtTime, Link, timeAgo } from "../lib";
import { StatusBadge } from "./ui";

export function JobCard({ job }: { job: Job }) {
  const thumb = thumbnailUrl(job);
  const running = job.status === "running" || job.status === "queued";
  return (
    <Link
      to={`/jobs/${job.id}`}
      className="group block rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      <div className="relative aspect-video overflow-hidden rounded-2xl border border-line bg-surface-2">
        {thumb ? (
          <img
            src={thumb}
            alt=""
            loading="lazy"
            className="size-full object-cover transition duration-500 group-hover:scale-[1.03]"
          />
        ) : (
          <div className="grid size-full place-items-center text-faint">
            <Film className="size-8" />
          </div>
        )}
        {job.duration != null && (
          <span className="absolute right-2 bottom-2 rounded-md bg-black/75 px-1.5 py-0.5 font-mono text-[11px] font-medium text-white backdrop-blur">
            {fmtTime(job.duration)}
          </span>
        )}
        {running && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/30">
            <div
              className="h-full bg-accent transition-[width] duration-700"
              style={{ width: `${Math.max(3, overallProgress(job) * 100)}%` }}
            />
          </div>
        )}
      </div>
      <div className="mt-3 px-0.5">
        <h3 className="line-clamp-2 text-[15px] leading-snug font-medium tracking-tight group-hover:text-accent">
          {job.title}
        </h3>
        <div className="mt-2 flex items-center gap-2 text-xs text-muted">
          <StatusBadge job={job} />
          <span>{timeAgo(job.created_at)}</span>
        </div>
      </div>
    </Link>
  );
}
