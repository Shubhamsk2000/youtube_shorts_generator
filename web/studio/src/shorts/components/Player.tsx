import { useEffect, useRef, useState } from "react";
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
  const [failed, setFailed] = useState(false);

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
      ) : downloaded && !failed ? (
        <video ref={videoRef} src={`/api/shorts/jobs/${encodeURIComponent(job.id)}/source`} controls className="absolute inset-0 size-full"
          onError={() => setFailed(true)} />
      ) : downloaded ? (
        <div className="grid size-full place-items-center p-6 text-center text-sm text-white/60">
          <span><Film className="mx-auto mb-2 size-6" />The full video isn't kept for this project (only each clip's own video), so it can't be played here.</span>
        </div>
      ) : (
        <div className="grid size-full place-items-center text-white/40">
          <Film className="size-8" />
        </div>
      )}
    </div>
  );
}
