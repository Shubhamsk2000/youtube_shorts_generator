import type { ReactNode } from "react";
import { ArrowRight, Clapperboard, History, Music2, Scissors } from "lucide-react";
import { cx, fmtTime, Link, timeAgo, usePoll } from "@kit/lib";
import { Card, CardHeader, Empty, ProgressBar } from "@kit/ui";
import { jobHref, studio, type RecentJob, type Status } from "./api";

const STATUS_DOT: Record<RecentJob["status"], string> = {
  queued: "bg-faint", running: "bg-accent animate-pulse", done: "bg-ok", failed: "bg-err", interrupted: "bg-warn",
};
const STATUS_LABEL: Record<RecentJob["status"], string> = {
  queued: "Queued", running: "Working", done: "Ready", failed: "Failed", interrupted: "Paused",
};

export function Dashboard({ status }: { status?: Status }) {
  const busy = !!status?.queue.current;
  const recent = usePoll(studio.recent, () => (busy ? 3000 : 20000), `recent-${busy}`);
  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-6">
      <header className="mb-10 max-w-2xl fade-in">
        <p className="mb-2 text-xs font-semibold tracking-wider text-accent uppercase">Studio</p>
        <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-5xl">What are we making today?</h1>
        <p className="mt-3 text-muted text-pretty">
          Two tools, one queue. Start something, close the tab, and come back to finished shorts.
        </p>
      </header>

      <div className="grid gap-5 md:grid-cols-2">
        <Tile
          to="/shorts"
          icon={<Scissors className="size-5" />}
          title="Long video → shorts"
          points={["YouTube link or your own file", "Gemini picks the moments", "Face-tracked 9:16, word-by-word captions"]}
          cta="Start from a video"
        />
        <Tile
          to="/anime"
          icon={<Clapperboard className="size-5" />}
          title="Anime episode → edits"
          points={["Complete scenes, 40-60 s montages", "AMVs cut to the beat of your songs", "Your tweet-card template on top"]}
          cta="Start from an episode"
          extra={<Link to="/anime/music" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg"><Music2 className="size-4" /> Music library</Link>}
        />
      </div>

      <Card className="mt-8">
        <CardHeader title={<span className="flex items-center gap-2"><History className="size-4" /> Recent work</span>} />
        {recent.data && recent.data.length === 0 ? (
          <Empty icon={<History className="size-5" />} title="Nothing yet">Start with one of the two tools above.</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {(recent.data ?? []).map((j) => (
              <li key={`${j.app}-${j.id}`}>
                <Link to={jobHref(j.app, j.id)} className="flex items-center gap-4 px-5 py-3.5 transition hover:bg-surface-2">
                  <span className={cx("grid size-8 shrink-0 place-items-center rounded-lg",
                    j.app === "anime" ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted")}>
                    {j.app === "anime" ? <Clapperboard className="size-4" /> : <Scissors className="size-4" />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{j.title}</p>
                    <p className="mt-0.5 text-xs text-muted capitalize">
                      {j.app}{j.duration ? ` · ${fmtTime(j.duration)}` : ""} · {timeAgo(j.created_at)}
                    </p>
                    {j.status === "running" && <div className="mt-2 max-w-sm"><ProgressBar value={j.progress} active /></div>}
                  </div>
                  <span className="inline-flex items-center gap-1.5 text-xs font-medium text-muted">
                    <span className={cx("size-1.5 rounded-full", STATUS_DOT[j.status])} />
                    {STATUS_LABEL[j.status]}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function Tile({ to, icon, title, points, cta, extra }: {
  to: string; icon: ReactNode; title: string; points: string[]; cta: string; extra?: ReactNode;
}) {
  return (
    <Card className="group flex flex-col p-6 transition hover:border-line-strong">
      <span className="grid size-10 place-items-center rounded-xl bg-accent-soft text-accent">{icon}</span>
      <h2 className="mt-4 text-lg font-semibold tracking-tight">{title}</h2>
      <ul className="mt-3 space-y-1.5 text-sm text-muted">
        {points.map((p) => (
          <li key={p} className="flex gap-2"><span className="mt-2 size-1 shrink-0 rounded-full bg-faint" />{p}</li>
        ))}
      </ul>
      <div className="mt-6 flex flex-wrap items-center gap-4">
        <Link to={to} className="inline-flex h-9 items-center gap-2 rounded-xl bg-fg px-3.5 text-sm font-medium text-bg transition hover:opacity-85">
          {cta} <ArrowRight className="size-4 transition group-hover:translate-x-0.5" />
        </Link>
        {extra}
      </div>
    </Card>
  );
}
