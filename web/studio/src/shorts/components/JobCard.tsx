import { CircleAlert, Film } from "lucide-react";
import { isActive, jobHref, overallProgress, thumbnailUrl, type Job } from "../api";
import { fmtDuration, fmtTime, Link, timeAgo } from "../lib";
import { StatusBadge } from "./ui";

export function JobCard({ job }: { job: Job }) {
  const thumb = thumbnailUrl(job);
  const running = isActive(job.status);
  const s = job.stats;
  const toReview = Math.max(0, s.clips - s.approved - s.rejected);
  return (
    <Link to={jobHref(job.id)} className="group block rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <div className="relative aspect-video overflow-hidden rounded-2xl border border-line bg-surface-2">
        {thumb ? (
          <img src={thumb} alt="" loading="lazy" className="size-full object-cover transition duration-500 group-hover:scale-[1.03]"
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
        ) : (
          <div className="grid size-full place-items-center text-faint"><Film className="size-8" /></div>
        )}
        {job.duration != null && (
          <span className="absolute right-2 bottom-2 rounded-md bg-black/75 px-1.5 py-0.5 font-mono text-[11px] font-medium text-white backdrop-blur">
            {fmtTime(job.duration)}
          </span>
        )}
        {s.clips > 0 && (
          <span className="absolute top-2 left-2 rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white backdrop-blur">
            {s.clips} clip{s.clips === 1 ? "" : "s"}
          </span>
        )}
        {running && (
          <div className="absolute inset-x-0 bottom-0 h-1 bg-black/30">
            <div className="h-full bg-accent transition-[width] duration-700" style={{ width: `${Math.max(3, overallProgress(job) * 100)}%` }} />
          </div>
        )}
      </div>
      <div className="mt-3 px-0.5">
        <h3 className="line-clamp-2 text-[15px] leading-snug font-medium tracking-tight group-hover:text-accent">{job.title}</h3>
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <StatusBadge job={job} />
          <span>{timeAgo(job.created_at)}</span>
          {s.clips > 0 && (
            <span className="font-mono text-[11px]" title={`${s.approved} approved · ${s.rejected} rejected · ${toReview} to review · ${s.exported} exported in HD · ${fmtDuration(s.clip_seconds)} total`}>
              <span className="text-ok">{s.approved}✓</span>
              {toReview > 0 && <span className="text-warn"> · {toReview} to review</span>}
              {s.exported > 0 && <span> · {s.exported} HD</span>}
            </span>
          )}
        </div>
        {job.status === "failed" && job.error && (
          <p className="mt-1.5 flex items-start gap-1 text-xs text-err" title={job.error}>
            <CircleAlert className="mt-px size-3 shrink-0" /><span className="line-clamp-1">{job.error}</span>
          </p>
        )}
      </div>
    </Link>
  );
}
