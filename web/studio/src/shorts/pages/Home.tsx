import { useMemo, useState } from "react";
import { CircleAlert, Search, X } from "lucide-react";
import { cx, fmtDuration, plural, useStored, usePoll } from "@kit/lib";
import { Button, Select, Stat } from "@kit/ui";
import type { Status } from "../../app/api";
import { api, isActive, type Job } from "../api";
import { JobCard } from "../components/JobCard";
import { ModelPicker } from "../components/ModelPicker";
import { UrlForm } from "../components/UrlForm";

type Filter = "all" | "active" | "review" | "done" | "problem";
type Sort = "new" | "old" | "clips" | "title";

const FILTERS: { value: Filter; label: string; test: (j: Job) => boolean }[] = [
  { value: "all", label: "All", test: () => true },
  { value: "active", label: "Processing", test: (j) => isActive(j.status) },
  { value: "review", label: "To review", test: (j) => j.status === "done" && j.stats.clips - j.stats.approved - j.stats.rejected > 0 },
  { value: "done", label: "Ready", test: (j) => j.status === "done" },
  { value: "problem", label: "Failed / paused", test: (j) => j.status === "failed" || j.status === "interrupted" },
];

const SORTS: Record<Sort, (a: Job, b: Job) => number> = {
  new: (a, b) => b.created_at.localeCompare(a.created_at),
  old: (a, b) => a.created_at.localeCompare(b.created_at),
  clips: (a, b) => b.stats.clips - a.stats.clips,
  title: (a, b) => a.title.localeCompare(b.title),
};

export function Home({ status }: { status?: Status }) {
  const jobs = usePoll(api.jobs, (d) => (d?.some((j) => isActive(j.status) || j.export?.status === "running") ? 2000 : 10000), "jobs");
  const system = usePoll(api.system, () => 60000, "system");
  const [q, setQ] = useState("");
  const [filter, setFilter] = useStored<Filter>("projects.filter", "all");
  const [sort, setSort] = useStored<Sort>("projects.sort", "new");

  const missing = system.data && !system.data.ready ? system.data.checks.filter((c) => c.required && !c.ok) : [];
  const lowDisk = status && status.disk_free_gb < 3;
  const blocked = missing.length
    ? `Setup needed: ${missing.map((c) => c.name).join(", ")}. See System.`
    : lowDisk ? `Only ${status!.disk_free_gb} GB disk free: delete old projects first (System → Storage).` : null;

  const list = jobs.data;
  const totals = useMemo(() => {
    const t = { clips: 0, approved: 0, exported: 0, toReview: 0, seconds: 0, active: 0 };
    for (const j of list ?? []) {
      t.clips += j.stats.clips;
      t.approved += j.stats.approved;
      t.exported += j.stats.exported;
      t.toReview += Math.max(0, j.stats.clips - j.stats.approved - j.stats.rejected);
      t.seconds += j.stats.clip_seconds;
      t.active += isActive(j.status) ? 1 : 0;
    }
    return t;
  }, [list]);

  const shown = useMemo(() => {
    const f = FILTERS.find((x) => x.value === filter) ?? FILTERS[0];
    const needle = q.trim().toLowerCase();
    return (list ?? [])
      .filter((j) => f.test(j) && (!needle || j.title.toLowerCase().includes(needle) || j.id.toLowerCase().includes(needle)))
      .sort(SORTS[sort] ?? SORTS.new);
  }, [list, filter, q, sort]);

  return (
    <>
      <section className="relative">
        <div className="hero-grid pointer-events-none absolute inset-x-0 -top-24 h-[480px]" aria-hidden />
        <div className="relative mx-auto flex max-w-2xl flex-col items-center px-4 pt-14 pb-10 text-center sm:pt-20 sm:pb-14">
          <h1 className="fade-in text-[2.4rem] leading-[1.05] font-semibold tracking-[-0.035em] sm:text-6xl">
            Long videos in.
            <br />
            <span className="bg-gradient-to-r from-faint via-faint to-accent bg-clip-text text-transparent">Shorts out.</span>
          </h1>
          <p className="fade-in mt-4 max-w-lg text-base text-pretty text-muted sm:text-lg">
            Paste a YouTube link or upload a video. The AI finds the moments worth posting, reframes them to 9:16 around
            faces, and captions every word.
          </p>
          <div className="fade-in mt-8 w-full">
            <UrlForm blocked={blocked} />
            <ModelPicker className="mt-3" center />
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
        {system.error && !system.data && (
          <p className="mb-5 flex items-center gap-2 text-sm text-warn">
            <CircleAlert className="size-4" /> Couldn't run the setup checks: {system.error}
          </p>
        )}

        {list && list.length > 0 && (
          <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="Projects" value={list.length} sub={totals.active ? `${totals.active} processing` : "none processing"} />
            <Stat label="Clips found" value={totals.clips} sub={`${fmtDuration(totals.seconds)} of video`} />
            <Stat label="To review" value={totals.toReview} tone={totals.toReview ? "warn" : undefined} sub="not approved or rejected" />
            <Stat label="Approved" value={totals.approved} sub={`${totals.exported} exported in HD`} />
            <Stat label="Disk free" value={status ? `${status.disk_free_gb} GB` : "–"} tone={status && status.disk_free_gb < 5 ? "warn" : undefined}
              sub={status ? `${status.ram_available_gb} GB RAM free` : ""} hint="Where projects are stored" />
          </div>
        )}

        <div className="mb-5 flex flex-wrap items-center gap-3 border-b border-line pb-3">
          <h2 className="mr-auto text-sm font-semibold tracking-tight">
            Projects {list && <span className="ml-1 font-mono text-xs font-normal text-muted">{shown.length}{shown.length !== list.length ? ` of ${list.length}` : ""}</span>}
          </h2>
          {list && list.length > 0 && (
            <>
              <label className="flex h-9 items-center gap-2 rounded-xl border border-line bg-surface px-2.5 text-sm focus-within:border-accent/60">
                <Search className="size-4 text-faint" />
                <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search" aria-label="Search projects"
                  className="w-32 bg-transparent outline-none placeholder:text-faint sm:w-44" />
                {q && <button onClick={() => setQ("")} aria-label="Clear search" className="text-faint hover:text-fg"><X className="size-3.5" /></button>}
              </label>
              <div className="flex flex-wrap gap-1 rounded-xl bg-surface-2 p-1" role="tablist" aria-label="Filter">
                {FILTERS.map((f) => {
                  const n = list.filter(f.test).length;
                  return (
                    <button key={f.value} role="tab" aria-selected={filter === f.value} onClick={() => setFilter(f.value)}
                      className={cx("rounded-lg px-2.5 py-1 text-xs font-medium transition",
                        filter === f.value ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg")}>
                      {f.label} <span className="font-mono text-faint">{n}</span>
                    </button>
                  );
                })}
              </div>
              <Select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort" className="w-auto">
                <option value="new">Newest</option>
                <option value="old">Oldest</option>
                <option value="clips">Most clips</option>
                <option value="title">Title A–Z</option>
              </Select>
            </>
          )}
        </div>

        {jobs.error && !list ? (
          <div className="rounded-2xl border border-err/30 bg-err/5 px-6 py-10 text-center">
            <p className="text-sm font-medium text-err">Couldn't load projects</p>
            <p className="mt-1 text-sm text-muted">{jobs.error}</p>
            <Button variant="outline" className="mt-4" onClick={jobs.reload}>Try again</Button>
          </div>
        ) : !list ? (
          <div className="grid grid-cols-1 gap-x-5 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i}>
                <div className="shimmer aspect-video rounded-2xl" />
                <div className="shimmer mt-3 h-4 w-3/4 rounded" />
              </div>
            ))}
          </div>
        ) : list.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line-strong px-6 py-14 text-center">
            <p className="text-sm font-medium">No projects yet</p>
            <p className="mt-1 text-sm text-muted">Paste a link above to make your first set of shorts.</p>
          </div>
        ) : shown.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-line-strong px-6 py-10 text-center text-sm text-muted">
            No {filter !== "all" ? `“${FILTERS.find((f) => f.value === filter)?.label}” ` : ""}projects{q ? ` matching “${q}”` : ""}.{" "}
            <button className="font-medium text-accent hover:underline" onClick={() => { setQ(""); setFilter("all"); }}>Show all</button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-x-5 gap-y-8 sm:grid-cols-2 lg:grid-cols-3">
            {shown.map((j) => <JobCard key={j.id} job={j} />)}
          </div>
        )}
        {jobs.stale && <p className="mt-6 text-center text-xs text-warn">Showing the last known state: {jobs.error}</p>}
        {list && list.length > 0 && (
          <p className="mt-8 text-center text-xs text-faint">{plural(totals.clips, "clip")} across {plural(list.length, "project")}</p>
        )}
      </section>
    </>
  );
}
