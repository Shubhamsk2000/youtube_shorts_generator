import { useEffect, useRef } from "react";
import { Film } from "lucide-react";
import type { Job } from "../api";

export interface Seek {
  t: number;
  n: number; // bump to re-seek to the same time
}

/** YouTube jobs use the embed (plays everywhere); local files stream from the API. */
export function Player({ job, seek }: { job: Job; seek: Seek | null }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const downloaded = job.stages.find((s) => s.key === "download")?.status === "done";

  useEffect(() => {
    if (seek && videoRef.current) {
      videoRef.current.currentTime = seek.t;
      videoRef.current.play().catch(() => {});
    }
  }, [seek]);

  return (
    <div className="relative aspect-video overflow-hidden rounded-2xl border border-line bg-black shadow-card">
      {job.is_youtube ? (
        <iframe
          key={seek?.n ?? 0}
          className="absolute inset-0 size-full"
          src={`https://www.youtube-nocookie.com/embed/${job.id}?rel=0&modestbranding=1${
            seek ? `&start=${Math.floor(seek.t)}&autoplay=1` : ""
          }`}
          title={job.title}
          allow="autoplay; encrypted-media; picture-in-picture"
          allowFullScreen
        />
      ) : downloaded ? (
        <video ref={videoRef} src={`/api/shorts/jobs/${job.id}/source`} controls className="absolute inset-0 size-full" />
      ) : (
        <div className="grid size-full place-items-center text-white/40">
          <Film className="size-8" />
        </div>
      )}
    </div>
  );
}
