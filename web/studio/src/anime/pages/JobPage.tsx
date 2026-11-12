import { useState, type ReactNode } from "react";
import { ArrowLeft, Download, Film, FolderOpen, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import { fmtTime, Link, navigate, timeAgo, usePoll } from "@kit/lib";
import { Pipeline } from "@kit/Pipeline";
import { Alert, Button, Card, CardHeader, Empty, ProgressBar, Segmented, Toggle } from "@kit/ui";
import { api, exporting, isActive, STYLE_LABEL, type Job, type PicksData, type Style } from "../api";
import { PickCard } from "../components/PickCard";
import { StatusBadge } from "../components/StatusBadge";

export function AnimeJobPage({ id }: { id: string }) {
  const { data: job, error, setData } = usePoll(() => api.job(id), (j) => (j && (isActive(j) || exporting(j)) ? 1500 : null), id);
  const editDone = job?.stages.find((s) => s.key === "edit")?.status === "done";
  const renderDone = job?.stages.find((s) => s.key === "render")?.status === "done";
  // refetch the picks whenever something that changes them finishes
  const picksKey = `${id}-${editDone}-${renderDone}-${job?.task}-${job?.status}-${Object.keys(job?.export?.files ?? {}).length}`;
  const picks = usePoll(() => api.picks(id), () => null, picksKey);

  if (error && !job) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-16 sm:px-6">
        <Empty icon={<Film className="size-5" />} title="Episode not found">
          <Link to="/anime" className="underline">Back to episodes</Link>
        </Empty>
      </div>
    );
  }
  if (!job) return <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6"><div className="h-40 rounded-2xl shimmer" /></div>;

  return (
    <div className="mx-auto max-w-6xl px-4 pt-6 pb-16 sm:px-6">
      <Link to="/anime" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" /> Episodes
      </Link>
      <Header job={job} onChange={setData} />
      <div className="mt-6 grid gap-6 lg:grid-cols-12">
        <aside className="space-y-6 lg:col-span-4">
          <Pipeline stages={job.stages} />
          {picks.data && <EpisodeInfo data={picks.data} />}
          <ExportCard job={job} picks={picks.data} onChange={setData} />
        </aside>
        <section className="lg:col-span-8">
          <Shorts job={job} data={picks.data} onJob={setData} reload={picks.reload} />
        </section>
      </div>
    </div>
  );
}

function Header({ job, onChange }: { job: Job; onChange: (j: Job) => void }) {
  const [busy, setBusy] = useState<string>();
  const [style, setStyle] = useState<Style>(job.options.style);
  const [err, setErr] = useState<string>();
  const idle = !isActive(job) && !exporting(job);

  async function act(name: string, fn: () => Promise<Job | void>) {
    setBusy(name);
    setErr(undefined);
    try {
      const j = await fn();
      if (j) onChange(j);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(undefined);
    }
  }

  const audio = job.audio_lang === "jpn" ? "Japanese" : job.audio_lang === "eng" ? "English dub" : job.audio_lang;
  return (
    <header className="mt-4 flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="truncate text-2xl font-semibold tracking-tight">{job.title}</h1>
          <StatusBadge job={job} />
        </div>
        <p className="mt-1 text-sm text-muted">
          {job.duration ? `${fmtTime(job.duration)} episode` : "Episode"}
          {audio && ` · ${audio} audio`} · {STYLE_LABEL[job.options.style]} · started {timeAgo(job.created_at)}
        </p>
        {job.error && <div className="mt-2 max-w-2xl"><Alert>{job.error}</Alert></div>}
        {err && <p className="mt-2 text-sm text-err">{err}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {(job.status === "failed" || job.status === "interrupted") && (
          <Button onClick={() => act("retry", () => api.retry(job.id))} loading={busy === "retry"}>
            <RotateCcw className="size-4" /> Resume
          </Button>
        )}
        <Segmented<Style>
          value={style}
          onChange={setStyle}
          disabled={!idle}
          options={[{ value: "clip", label: "Scenes" }, { value: "montage", label: "Montage" }, { value: "song", label: "Song" }]}
        />
        <Button
          variant="outline"
          disabled={!idle}
          loading={busy === "again"}
          onClick={() => act("again", () => api.reselect(job.id, { style }))}
          title="Ask Gemini for new picks in this style (your hook edits are kept for picks that come back)"
        >
          <Sparkles className="size-4" /> New picks
        </Button>
        <Button
          variant="ghost"
          aria-label="Delete"
          title="Delete the work files and previews (the episode and exported shorts stay)"
          disabled={!idle}
          loading={busy === "del"}
          onClick={() => {
            if (confirm("Delete this episode's previews and work files? The episode file and exported shorts are kept."))
              act("del", async () => { await api.remove(job.id); navigate("/anime"); });
          }}
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </header>
  );
}

function EpisodeInfo({ data }: { data: PicksData }) {
  const e = data.episode;
  return (
    <Card>
      <CardHeader title="Episode" />
      <dl className="space-y-2.5 px-5 py-4 text-sm">
        <Row k="Audio">{e.audio === "eng" ? "English dub" : e.audio === "jpn" ? "Japanese" : e.audio ?? "?"}</Row>
        <Row k="Dialogue">{e.subs ?? "none"}{e.lines ? ` · ${e.lines} lines` : ""}</Row>
        <Row k="Skipped">
          {e.excluded.length ? e.excluded.map((x) => `${x.what} ${fmtTime(x.start)}–${fmtTime(x.end)}`).join(", ") : "no OP/ED found"}
        </Row>
        <Row k="Picked by">{data.source === "gemini" ? `Gemini (${data.model})` : "intensity only (no Gemini)"}</Row>
      </dl>
    </Card>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0 text-muted">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function ExportCard({ job, picks, onChange }: { job: Job; picks?: PicksData; onChange: (j: Job) => void }) {
  const [err, setErr] = useState<string>();
  const ex = job.export;
  const approved = picks?.picks.filter((p) => p.review === "approved").length ?? 0;
  return (
    <Card>
      <CardHeader title="Export" aside="1080×1920 + title/hashtags .txt" />
      <div className="space-y-3 px-5 py-4">
        {ex && (
          <div className="text-sm">
            {exporting(job) && <div className="mb-2"><ProgressBar value={ex.progress} active /></div>}
            <p className={ex.status === "failed" ? "text-err" : "text-muted"}>{ex.message}</p>
            {ex.dir && ex.status === "done" && (
              <p className="mt-1 flex items-center gap-1.5 text-xs text-faint">
                <FolderOpen className="size-3.5 shrink-0" />
                <span className="truncate font-mono" title={ex.dir}>{ex.dir}</span>
              </p>
            )}
          </div>
        )}
        <Button
          className="w-full"
          disabled={!approved || exporting(job)}
          onClick={async () => {
            setErr(undefined);
            try { onChange(await api.export(job.id, null)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
          }}
        >
          <Download className="size-4" /> Export approved ({approved})
        </Button>
        {!approved && <p className="text-xs text-muted">Approve the shorts you like, or export one from its card.</p>}
        {err && <p className="text-sm text-err">{err}</p>}
      </div>
    </Card>
  );
}

function Shorts({ job, data, onJob, reload }: { job: Job; data?: PicksData; onJob: (j: Job) => void; reload: () => void }) {
  const [capBusy, setCapBusy] = useState(false);
  const idle = !isActive(job) && !exporting(job);
  const header = (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 className="text-lg font-semibold tracking-tight">
        Shorts {data && <span className="font-normal text-muted">· {data.picks.length}</span>}
      </h2>
      {job.options.style !== "song" && (
        <Toggle
          checked={job.options.captions}
          disabled={!idle || capBusy}
          label={<span className="text-muted">Captions</span>}
          onChange={async (v) => {
            setCapBusy(true);
            try { onJob(await api.setCaptions(job.id, v)); } finally { setCapBusy(false); }
          }}
        />
      )}
    </div>
  );

  if (!data) {
    return (
      <>
        {header}
        <Card>
          {isActive(job) ? (
            <div className="grid gap-4 p-4 sm:grid-cols-2">
              {[0, 1].map((i) => <div key={i} className="phone max-h-[540px] w-full rounded-2xl shimmer" />)}
            </div>
          ) : (
            <Empty icon={<Film className="size-5" />} title="No shorts yet">
              {job.status === "failed" ? "Something went wrong: see the pipeline on the left, then Resume." : "Press Resume to continue."}
            </Empty>
          )}
        </Card>
      </>
    );
  }

  return (
    <>
      {header}
      {data.note && <div className="mb-4"><Alert tone="warn">{data.note}</Alert></div>}
      {data.picks.length === 0 ? (
        <Card><Empty icon={<Film className="size-5" />} title="No good moments found">Try “New picks”, or another style.</Empty></Card>
      ) : (
        <div className="space-y-5">
          {data.picks.map((p) => <PickCard key={p.id} job={job} pick={p} onChanged={reload} onJob={onJob} />)}
        </div>
      )}
    </>
  );
}
