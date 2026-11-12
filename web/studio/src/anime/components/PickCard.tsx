import { useEffect, useState } from "react";
import { Check, Download, Eye, LoaderCircle, Music, Pencil, ThumbsDown, ThumbsUp } from "lucide-react";
import { cx, fmtTime } from "@kit/lib";
import { Button, Card } from "@kit/ui";
import { api, exporting, isActive, type Job, type Pick } from "../api";

const FX_LABEL: Record<string, string> = {
  zoom_punch: "zoom punch", shake: "shake", slowmo: "slow-mo", freeze_end: "freeze", flash_in: "flash",
};

export function PickCard({ job, pick: p, onChanged, onJob }: {
  job: Job;
  pick: Pick;
  onChanged: () => void;
  onJob: (j: Job) => void;
}) {
  const [tweet, setTweet] = useState(p.tweet);
  const [title, setTitle] = useState(p.title);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string>();
  useEffect(() => { setTweet(p.tweet); setTitle(p.title); }, [p.tweet, p.title]);

  const dirty = tweet.trim() !== p.tweet || title.trim() !== p.title;
  const idle = !isActive(job);
  const queued = job.export?.queue.includes(p.id) || job.export?.current === p.id;

  async function change(body: Parameters<typeof api.editPick>[2]) {
    setSaving(true);
    setErr(undefined);
    try {
      await api.editPick(job.id, p.id, body);
      onJob(await api.job(job.id));
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  async function review(r: "approved" | "rejected") {
    await api.editPick(job.id, p.id, { review: p.review === r ? "none" : r });
    onChanged();
  }

  return (
    <Card className={cx("overflow-hidden fade-in", p.review === "rejected" && "opacity-60")}>
      <div className="flex flex-col gap-5 p-4 sm:flex-row">
        <div className="phone relative w-full shrink-0 overflow-hidden rounded-xl bg-black sm:w-[260px]">
          {p.url ? (
            <video key={p.url} src={p.url} controls playsInline preload="metadata" className="absolute inset-0 size-full" />
          ) : (
            <div className="absolute inset-0 grid place-items-center text-sm text-white/50">
              <span className="flex items-center gap-2"><LoaderCircle className="size-4 animate-spin" /> Rendering preview…</span>
            </div>
          )}
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-fg px-2 py-0.5 font-semibold text-bg">#{p.rank}</span>
            <span className="rounded-full border border-line px-2 py-0.5 font-mono" title="Viral potential (Gemini)">{p.score.toFixed(1)}/10</span>
            {p.kind && <span className="rounded-full border border-line px-2 py-0.5 capitalize">{p.kind}</span>}
            <span className="rounded-full border border-line px-2 py-0.5 font-mono">{p.duration.toFixed(0)}s</span>
            {p.style === "song" && <span className="rounded-full border border-line px-2 py-0.5 font-mono">{p.segments.length} shots</span>}
            {p.watched && (
              <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-2 py-0.5 text-accent" title="Gemini watched this clip">
                <Eye className="size-3" /> watched
              </span>
            )}
          </div>

          {p.song && <SongPicker pick={p} disabled={!idle || saving} onPick={(song) => change({ song })} />}

          <label className="block">
            <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-muted"><Pencil className="size-3" /> Hook on the card</span>
            <textarea
              value={tweet}
              onChange={(e) => setTweet(e.target.value)}
              rows={2}
              maxLength={140}
              className="w-full resize-none rounded-xl border border-line bg-bg px-3 py-2 text-[15px] leading-snug outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20"
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-muted">Upload title</span>
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={100}
              className="w-full rounded-xl border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20"
            />
          </label>
          {dirty && (
            <div className="flex items-center gap-2">
              <Button onClick={() => change({ tweet: tweet.trim(), title: title.trim() })} loading={saving} disabled={!idle || !tweet.trim()}>
                Save & re-render
              </Button>
              <Button variant="ghost" onClick={() => { setTweet(p.tweet); setTitle(p.title); }}>Cancel</Button>
              {!idle && <span className="text-xs text-muted">Wait for the current task to finish</span>}
            </div>
          )}
          {err && <p className="text-sm text-err">{err}</p>}

          {p.why && <p className="text-sm leading-relaxed text-muted">{p.why}</p>}

          {p.style !== "song" && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted">{p.segments.length > 1 ? `${p.segments.length} beats` : "From the episode"}</p>
              <ol className="flex flex-wrap gap-1.5">
                {p.segments.map((s, i) => (
                  <li key={i} className="rounded-lg bg-surface-2 px-2 py-1 font-mono text-[11px] text-muted">
                    {i > 0 && s.transition_in !== "cut" && <span className="text-accent">{s.transition_in} → </span>}
                    {fmtTime(s.start)}–{fmtTime(s.end)}
                    {s.effects.length > 0 && <span className="text-fg"> · {s.effects.map((e) => FX_LABEL[e] ?? e).join(", ")}</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {p.hashtags.length > 0 && <p className="text-xs text-muted">{p.hashtags.map((h) => `#${h}`).join(" ")}</p>}

          <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Button variant={p.review === "approved" ? "primary" : "outline"} onClick={() => review("approved")} aria-pressed={p.review === "approved"}>
              {p.review === "approved" ? <Check className="size-4" /> : <ThumbsUp className="size-4" />}
              {p.review === "approved" ? "Approved" : "Approve"}
            </Button>
            <Button variant="ghost" onClick={() => review("rejected")} aria-pressed={p.review === "rejected"} aria-label="Reject">
              <ThumbsDown className={cx("size-4", p.review === "rejected" && "text-err")} />
            </Button>
            <div className="ml-auto flex items-center gap-2">
              {p.final_url ? (
                <a href={p.final_url} download className="inline-flex h-9 items-center gap-2 rounded-xl border border-line-strong px-3.5 text-sm font-medium hover:bg-surface-2">
                  <Download className="size-4" /> HD file
                </a>
              ) : (
                <Button variant="outline" disabled={queued || !p.ready} loading={queued && exporting(job)}
                  onClick={async () => onJob(await api.export(job.id, [p.id]))}>
                  <Download className="size-4" /> {queued ? "Exporting…" : "Export HD"}
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
}

function SongPicker({ pick: p, disabled, onPick }: { pick: Pick; disabled: boolean; onPick: (id: string) => void }) {
  const song = p.song!;
  const others = p.song_options.filter((o) => o.id !== song.id);
  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Music className="size-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate font-medium" title={song.id}>{song.title}</span>
        <span className="font-mono text-xs text-muted">
          {song.mood} · {song.bpm.toFixed(0)} BPM · {fmtTime(song.start)}–{fmtTime(song.end)}
        </span>
      </div>
      <label className="mt-2 flex items-center gap-2 text-xs text-muted">
        <span className="shrink-0">{song.auto ? "Chosen automatically." : "Your choice."} Use instead:</span>
        <select
          disabled={disabled}
          value=""
          onChange={(e) => e.target.value !== "" && onPick(e.target.value === "auto" ? "" : e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-2 py-1 text-xs text-fg outline-none focus:border-accent/60"
        >
          <option value="">Pick another song…</option>
          {!song.auto && <option value="auto">Let the app choose</option>}
          {others.map((o) => (
            <option key={o.id} value={o.id}>{o.title} ({o.why})</option>
          ))}
        </select>
      </label>
    </div>
  );
}
