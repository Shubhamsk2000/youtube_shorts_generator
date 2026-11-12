import { Captions, Crop, Sparkles } from "lucide-react";
import { api, isActive, type SystemInfo } from "../api";
import { JobCard } from "../components/JobCard";
import { UrlForm } from "../components/UrlForm";
import { usePoll } from "../lib";

const FEATURES = [
  { icon: Sparkles, label: "AI picks the best moments" },
  { icon: Crop, label: "Face-tracked 9:16" },
  { icon: Captions, label: "Word-by-word captions" },
];

export function Home({ system }: { system?: SystemInfo }) {
  const { data: jobs, error } = usePoll(
    api.jobs,
    (d) => (d?.some((j) => isActive(j.status)) ? 1500 : 10000),
    "jobs",
  );

  const missing = system && !system.ready ? system.checks.filter((c) => c.required && !c.ok) : [];
  const blocked = missing.length ? `Setup needed: ${missing.map((c) => c.name).join(", ")}. See status, top right.` : null;

  return (
    <>
      <section className="relative">
        <div className="hero-grid pointer-events-none absolute inset-x-0 -top-24 h-[520px]" aria-hidden />
        <div className="relative mx-auto flex max-w-2xl flex-col items-center px-4 pt-16 pb-14 text-center sm:pt-24 sm:pb-20">
          <span className="fade-in inline-flex items-center gap-2 rounded-full border border-line bg-surface/70 px-3 py-1 text-xs font-medium text-muted backdrop-blur">
            <span className="size-1.5 rounded-full bg-accent" />
            Long video → shorts
          </span>
          <h1 className="fade-in mt-6 text-[2.6rem] leading-[1.05] font-semibold tracking-[-0.035em] sm:text-6xl">
            Long videos in.
            <br />
            <span className="bg-gradient-to-r from-faint via-faint to-accent bg-clip-text text-transparent">
              Viral shorts out.
            </span>
          </h1>
          <p className="fade-in mt-5 max-w-lg text-base text-pretty text-muted sm:text-lg">
            Paste a YouTube link or upload a video. Gemini finds the moments worth watching, reframes them around faces, and
            captions every word.
          </p>
          <div className="fade-in mt-9 w-full">
            <UrlForm blocked={blocked} />
          </div>
          <ul className="mt-4 flex flex-wrap justify-center gap-x-5 gap-y-2 text-sm text-muted">
            {FEATURES.map(({ icon: Icon, label }) => (
              <li key={label} className="flex items-center gap-1.5">
                <Icon className="size-4 text-faint" />
                {label}
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 pb-24 sm:px-6">
        <div className="mb-5 flex items-baseline justify-between border-b border-line pb-3">
          <h2 className="text-sm font-semibold tracking-tight">Projects</h2>
          {jobs && <span className="text-xs text-muted">{jobs.length}</span>}
        </div>
        {error && !jobs ? (
          <p className="text-sm text-err">{error}</p>
        ) : !jobs ? (
          <div className="grid gap-x-5 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i}>
                <div className="shimmer aspect-video rounded-2xl" />
                <div className="shimmer mt-3 h-4 w-3/4 rounded" />
              </div>
            ))}
          </div>
        ) : jobs.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line-strong px-6 py-14 text-center">
            <p className="text-sm font-medium">No projects yet</p>
            <p className="mt-1 text-sm text-muted">Paste a link above to make your first set of shorts.</p>
          </div>
        ) : (
          <div className="grid gap-x-5 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {jobs.map((j) => (
              <JobCard key={j.id} job={j} />
            ))}
          </div>
        )}
      </section>
    </>
  );
}
