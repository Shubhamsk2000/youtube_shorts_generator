import { useRef, useState, type DragEvent } from "react";
import { ArrowRight, Clapperboard, FileVideo, FolderOpen, Music, Upload, X } from "lucide-react";
import { cx, fmtTime, Link, navigate, timeAgo, usePoll } from "@kit/lib";
import { Alert, Button, Card, CardHeader, Empty, Field, PageHeader, ProgressBar, Segmented, Toggle } from "@kit/ui";
import { api, isActive, overallProgress, STYLE_LABEL, uploadEpisode, type Audio, type Job, type Options, type Style } from "../api";
import { StatusBadge } from "../components/StatusBadge";

const DEFAULTS: Options = { style: "clip", count: 5, audio: "auto", captions: true };

const STYLE_HINT: Record<Style, string> = {
  clip: "One complete scene per short, about a minute when the scene supports it.",
  montage: "8-14 beats from the episode, 40-60 s, with flashes, zoom punches and transitions.",
  song: "An AMV cut to one of your songs: shots change on the beat, the biggest moments land on the drop.",
};

export function AnimeHome() {
  const system = usePoll(api.system, () => 30000, "anime-system");
  const eps = usePoll(api.episodes, () => 15000, "episodes");
  const jobs = usePoll(api.jobs, (d) => (d?.some(isActive) ? 2000 : 15000), "anime-jobs");
  const music = usePoll(api.music, () => null, "music-count");
  const [picked, setPicked] = useState<string | null>(null);
  const [opts, setOpts] = useState<Options>(DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string>();

  const episodes = eps.data?.episodes ?? [];
  const selected = picked ?? (episodes.length === 1 ? episodes[0].name : null);
  const existing = episodes.find((e) => e.name === selected)?.job_id ?? null;
  const songs = music.data?.tracks.length ?? 0;
  const noSongs = opts.style === "song" && music.data && songs === 0;

  async function start() {
    if (!selected) return;
    setBusy(true);
    setErr(undefined);
    try {
      const job = await api.create(selected, opts);
      navigate(`/anime/jobs/${job.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-6">
      <PageHeader eyebrow="Anime edits" title="Turn an episode into shorts people replay.">
        Gemini reads the script, watches the best moments and cuts them into your tweet-card template: complete
        scenes, hype montages, or AMVs cut to the beat of your own music.
      </PageHeader>

      <div className="grid gap-6 lg:grid-cols-12">
        <Card className="lg:col-span-7">
          <CardHeader title="1 · Episode" aside={eps.data && <span className="font-mono">{episodes.length} in folder</span>} />
          <div className="p-4">
            {episodes.length > 0 ? (
              <ul className="space-y-1.5" role="radiogroup" aria-label="Episode">
                {episodes.map((e) => (
                  <li key={e.name}>
                    <button
                      role="radio"
                      aria-checked={selected === e.name}
                      onClick={() => setPicked(e.name)}
                      className={cx(
                        "flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition",
                        selected === e.name ? "border-accent/50 bg-accent-soft" : "border-line hover:bg-surface-2",
                      )}
                    >
                      <FileVideo className={cx("size-4 shrink-0", selected === e.name ? "text-accent" : "text-muted")} />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">{e.name}</span>
                      {e.job_id && <span className="rounded-full bg-surface-2 px-2 py-px text-[11px] text-muted">processed</span>}
                      <span className="font-mono text-xs text-faint">{(e.size_mb / 1000).toFixed(2)} GB</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              eps.data && <p className="px-1 pb-2 text-sm text-muted">No episodes yet: drop one below.</p>
            )}
            <Uploader onDone={(name) => { eps.reload(); setPicked(name); }} />
            {eps.data?.dir && (
              <p className="mt-3 flex items-center gap-1.5 px-1 text-xs text-faint">
                <FolderOpen className="size-3.5 shrink-0" />
                <span className="truncate font-mono" title={eps.data.dir}>{eps.data.dir}</span>
              </p>
            )}
          </div>
        </Card>

        <Card className="lg:col-span-5">
          <CardHeader title="2 · Shorts" />
          <div className="space-y-5 p-5">
            <Field label="Style" hint={STYLE_HINT[opts.style]}>
              <Segmented<Style>
                value={opts.style}
                onChange={(style) => setOpts({ ...opts, style })}
                options={(["clip", "montage", "song"] as Style[]).map((v) => ({ value: v, label: STYLE_LABEL[v] }))}
              />
            </Field>
            {opts.style === "song" && (
              <div className="flex items-center justify-between gap-3 rounded-xl border border-line bg-surface-2 px-3 py-2.5 text-sm">
                <span className="flex items-center gap-2 text-muted">
                  <Music className="size-4" />
                  {music.data ? `${songs} song${songs === 1 ? "" : "s"} in your library` : "Checking your library…"}
                </span>
                <Link to="/anime/music" className="font-medium text-accent hover:underline">
                  {songs ? "Manage" : "Add songs"}
                </Link>
              </div>
            )}
            <Field label="How many">
              <Segmented<number>
                value={opts.count}
                onChange={(count) => setOpts({ ...opts, count })}
                options={[3, 5, 8].map((n) => ({ value: n, label: String(n) }))}
              />
            </Field>
            <Field label="Audio" hint={opts.style === "song"
              ? "Used to understand the episode; the short itself plays your song."
              : opts.audio === "jpn"
                ? "Original voices; captions come from the English subtitle track."
                : "English dub if the file has one (captions transcribed from the voices), otherwise Japanese with the subtitle track."}>
              <Segmented<Audio>
                value={opts.audio}
                onChange={(audio) => setOpts({ ...opts, audio })}
                options={[{ value: "auto", label: "Auto" }, { value: "eng", label: "English dub" }, { value: "jpn", label: "Japanese" }]}
              />
            </Field>
            {opts.style !== "song" && (
              <Toggle checked={opts.captions} onChange={(captions) => setOpts({ ...opts, captions })} label="Captions under the card" />
            )}

            {noSongs && <Alert tone="warn">Add at least one song to your music library first.</Alert>}
            {err && <Alert>{err}</Alert>}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Button onClick={start} loading={busy} disabled={!selected || system.data?.ready === false || !!noSongs}>
                {existing ? "Re-run with these settings" : "Make shorts"}
                <ArrowRight className="size-4" />
              </Button>
              {existing && (
                <Link to={`/anime/jobs/${existing}`} className="rounded-lg px-2 py-1 text-sm font-medium text-muted hover:text-fg">
                  Open existing
                </Link>
              )}
            </div>
            {!selected && episodes.length > 1 && <p className="text-xs text-muted">Choose an episode first.</p>}
          </div>
        </Card>
      </div>

      <Card className="mt-6">
        <CardHeader title="Episodes you've cut" aside={jobs.data && `${jobs.data.length}`} />
        {jobs.data && jobs.data.length === 0 ? (
          <Empty icon={<Clapperboard className="size-5" />} title="Nothing yet">
            Pick an episode above and press <b>Make shorts</b>.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">{(jobs.data ?? []).map((j) => <JobRow key={j.id} job={j} />)}</ul>
        )}
      </Card>
    </div>
  );
}

function JobRow({ job }: { job: Job }) {
  return (
    <li>
      <Link to={`/anime/jobs/${job.id}`} className="flex items-center gap-4 px-5 py-3.5 transition hover:bg-surface-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{job.title}</p>
          <p className="mt-0.5 text-xs text-muted">
            {STYLE_LABEL[job.options.style]} · {job.options.count} shorts
            {job.audio_lang && ` · ${job.audio_lang} audio`}
            {job.duration ? ` · ${fmtTime(job.duration)}` : ""} · {timeAgo(job.created_at)}
          </p>
          {isActive(job) && (
            <div className="mt-2 max-w-sm">
              <ProgressBar value={overallProgress(job)} active />
            </div>
          )}
        </div>
        <StatusBadge job={job} />
      </Link>
    </li>
  );
}

function Uploader({ onDone }: { onDone: (name: string) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [up, setUp] = useState<{ name: string; p: number; abort: () => void } | null>(null);
  const [err, setErr] = useState<string>();

  async function send(file: File) {
    setErr(undefined);
    const { done, abort } = uploadEpisode(file, (p) => setUp((u) => (u ? { ...u, p } : u)));
    setUp({ name: file.name, p: 0, abort });
    try {
      await done;
      onDone(file.name);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUp(null);
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files[0];
    if (f) send(f);
  }

  return (
    <div className="mt-3">
      {up ? (
        <div className="rounded-xl border border-line px-4 py-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-sm">
            <span className="truncate font-medium">Uploading {up.name}</span>
            <span className="flex items-center gap-2 font-mono text-xs text-muted">
              {Math.round(up.p * 100)}%
              <button onClick={up.abort} aria-label="Cancel upload" className="rounded p-0.5 hover:bg-surface-2">
                <X className="size-3.5" />
              </button>
            </span>
          </div>
          <ProgressBar value={up.p} active />
        </div>
      ) : (
        <button
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className={cx(
            "flex w-full items-center justify-center gap-2 rounded-xl border border-dashed px-4 py-5 text-sm transition",
            drag ? "border-accent bg-accent-soft text-accent" : "border-line-strong text-muted hover:bg-surface-2 hover:text-fg",
          )}
        >
          <Upload className="size-4" />
          Drop an episode here or <span className="font-medium text-fg underline underline-offset-2">browse</span>
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept="video/*,.mkv"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = ""; }}
      />
      {err && <p className="mt-2 text-sm text-err">{err}</p>}
    </div>
  );
}
