import { useRef, useState, type DragEvent } from "react";
import { FolderOpen, Music2, Pause, Play, Trash2, Upload, X } from "lucide-react";
import { cx, fmtTime, usePoll } from "@kit/lib";
import { Alert, Card, CardHeader, Empty, PageHeader, ProgressBar } from "@kit/ui";
import { api, MOODS, uploadTrack, type Mood, type Track } from "../api";

const MOOD_HINT: Record<Mood, string> = {
  hype: "fights, power-ups",
  epic: "big moments, reveals",
  sad: "emotional scenes",
  chill: "calm, everyday",
  funny: "comedy",
};

export function MusicPage() {
  const lib = usePoll(api.music, () => null, "music");
  const [mood, setMood] = useState<Mood | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [err, setErr] = useState<string>();
  const audio = useRef<HTMLAudioElement>(null);

  function toggle(t: Track) {
    const el = audio.current;
    if (!el) return;
    if (playing === t.id) {
      el.pause();
      setPlaying(null);
      return;
    }
    el.src = api.trackUrl(t.id);
    el.play().catch(() => setPlaying(null));
    setPlaying(t.id);
  }

  async function act(fn: () => Promise<unknown>) {
    setErr(undefined);
    try {
      await fn();
      lib.reload();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const tracks = lib.data?.tracks ?? [];
  const byMood = MOODS.map((m) => ({ m, n: tracks.filter((t) => t.mood === m).length }));

  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-6">
      <PageHeader eyebrow="Anime edits" title="Music library">
        Songs for song edits (AMVs). Each is analysed once: tempo, beats, where it builds and drops, and its mood. Every
        edit then gets the best-fitting song that isn't overused.
      </PageHeader>

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-6 lg:col-span-4">
          <Card>
            <CardHeader title="Add songs" />
            <div className="space-y-4 p-5">
              <div>
                <div className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">Mood</div>
                <div className="flex flex-wrap gap-1.5">
                  <Chip on={mood === null} onClick={() => setMood(null)}>Detect</Chip>
                  {MOODS.map((m) => <Chip key={m} on={mood === m} onClick={() => setMood(m)}>{m}</Chip>)}
                </div>
                <p className="mt-2 text-xs text-muted">
                  {mood ? `Saved as ${mood} (${MOOD_HINT[mood]}).` : "Gemini listens once and tags the mood; you can change it after."}
                </p>
              </div>
              <TrackUploader mood={mood} onDone={() => lib.reload()} />
            </div>
          </Card>
          <Card>
            <CardHeader title="Where to get songs" />
            <div className="space-y-2 px-5 py-4 text-sm text-muted">
              <p>
                <b className="text-fg">YouTube Audio Library</b> (YouTube Studio → Audio library) is free and licensed for
                YouTube. Filter by mood, tick “Attribution not required”, download the mp3.
              </p>
              <p>Popular songs from labels get your short claimed or blocked, on top of any anime claim.</p>
            </div>
          </Card>
        </div>

        <Card className="lg:col-span-8">
          <CardHeader
            title={`Songs · ${tracks.length}`}
            aside={<span className="hidden gap-2 sm:flex">{byMood.filter((x) => x.n).map((x) => <span key={x.m}>{x.m} {x.n}</span>)}</span>}
          />
          {err && <div className="px-5 pt-4"><Alert>{err}</Alert></div>}
          {lib.data && tracks.length === 0 ? (
            <Empty icon={<Music2 className="size-5" />} title="No songs yet">
              Add a few per mood: 10–20 is plenty to start. Song edits pick from here automatically.
            </Empty>
          ) : (
            <ul className="divide-y divide-line">
              {tracks.map((t) => (
                <li key={t.id} className="flex flex-wrap items-center gap-3 px-5 py-3">
                  <button
                    onClick={() => toggle(t)}
                    aria-label={playing === t.id ? `Pause ${t.title}` : `Play ${t.title}`}
                    className="grid size-9 shrink-0 place-items-center rounded-full border border-line hover:bg-surface-2"
                  >
                    {playing === t.id ? <Pause className="size-4" /> : <Play className="size-4" />}
                  </button>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium" title={t.id}>{t.title}</p>
                    <p className="text-xs text-muted">
                      {fmtTime(t.duration)} · {t.bpm.toFixed(0)} BPM{t.genre && ` · ${t.genre}`}
                      {t.uses > 0 && ` · used ${t.uses}×`}
                    </p>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-muted">
                    <span className="sr-only">Mood</span>
                    <select
                      value={t.mood}
                      onChange={(e) => act(() => api.setMood(t.id, e.target.value as Mood))}
                      className="rounded-lg border border-line bg-bg px-2 py-1 text-xs text-fg capitalize outline-none focus:border-accent/60"
                    >
                      {MOODS.map((m) => <option key={m} value={m}>{m}</option>)}
                    </select>
                    <span className="w-14 text-faint" title="How the mood was decided">
                      {t.mood_source === "gemini" ? "by Gemini" : t.mood_source === "folder" ? "set" : "guess"}
                    </span>
                  </label>
                  <button
                    onClick={() => confirm(`Delete ${t.title} from the library?`) && act(() => api.deleteTrack(t.id))}
                    aria-label={`Delete ${t.title}`}
                    className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-err"
                  >
                    <Trash2 className="size-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}
          {lib.data?.dir && (
            <p className="flex items-center gap-1.5 border-t border-line px-5 py-3 text-xs text-faint">
              <FolderOpen className="size-3.5 shrink-0" />
              <span className="truncate font-mono">{lib.data.dir}</span>
              <span className="hidden sm:inline">· subfolders named hype/, sad/… set the mood</span>
            </p>
          )}
        </Card>
      </div>
      <audio ref={audio} onEnded={() => setPlaying(null)} className="hidden" />
    </div>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  return (
    <button
      onClick={onClick}
      aria-pressed={on}
      className={cx(
        "rounded-full border px-2.5 py-1 text-xs font-medium capitalize transition",
        on ? "border-accent/50 bg-accent-soft text-accent" : "border-line text-muted hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

function TrackUploader({ mood, onDone }: { mood: Mood | null; onDone: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [up, setUp] = useState<{ name: string; p: number; n: number; of: number; abort: () => void } | null>(null);
  const [err, setErr] = useState<string>();

  async function send(files: File[]) {
    setErr(undefined);
    const failed: string[] = [];
    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const { done, abort } = uploadTrack(f, mood, (p) => setUp((u) => (u ? { ...u, p } : u)));
      setUp({ name: f.name, p: 0, n: i + 1, of: files.length, abort });
      try {
        await done;
      } catch (e) {
        failed.push(`${f.name}: ${e instanceof Error ? e.message : String(e)}`);
        if (e instanceof Error && e.message === "Upload cancelled") break;
      }
    }
    setUp(null);
    if (failed.length) setErr(failed.join(" · "));
    onDone();
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDrag(false);
    send(Array.from(e.dataTransfer.files));
  }

  return (
    <div>
      {up ? (
        <div className="rounded-xl border border-line px-4 py-3">
          <div className="mb-2 flex items-center justify-between gap-2 text-sm">
            <span className="truncate font-medium">{up.of > 1 && `${up.n}/${up.of} · `}{up.name}</span>
            <span className="flex items-center gap-2 font-mono text-xs text-muted">
              {up.p < 1 ? `${Math.round(up.p * 100)}%` : "analysing…"}
              <button onClick={up.abort} aria-label="Cancel upload" className="rounded p-0.5 hover:bg-surface-2"><X className="size-3.5" /></button>
            </span>
          </div>
          <ProgressBar value={up.p < 1 ? up.p : null} active />
        </div>
      ) : (
        <button
          onClick={() => input.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}
          className={cx(
            "flex w-full flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed px-4 py-6 text-sm transition",
            drag ? "border-accent bg-accent-soft text-accent" : "border-line-strong text-muted hover:bg-surface-2 hover:text-fg",
          )}
        >
          <Upload className="size-4" />
          <span>Drop songs or <span className="font-medium text-fg underline underline-offset-2">browse</span></span>
          <span className="text-xs text-faint">mp3, m4a, wav, ogg, flac · several at once</span>
        </button>
      )}
      <input ref={input} type="file" accept="audio/*" multiple className="hidden"
        onChange={(e) => { const fs = Array.from(e.target.files ?? []); e.target.value = ""; if (fs.length) send(fs); }} />
      {err && <p className="mt-2 text-sm text-err">{err}</p>}
    </div>
  );
}
