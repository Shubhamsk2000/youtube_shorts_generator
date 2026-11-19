import { useMemo, useState, type KeyboardEvent } from "react";
import {
  Check, CheckCheck, CircleAlert, Clock, Copy, Crop, Download, FileArchive, Flame, FolderOpen, LoaderCircle, Pencil,
  RefreshCw, Sparkles, Square, Undo2, X,
} from "lucide-react";
import { useConfirm, useToast } from "@kit/feedback";
import { Kbd, Select } from "@kit/ui";
import { api, editorHref, isActive, isExporting, type Clip, type ClipsData, type ExportState, type Job, type Review } from "../api";
import { copyText, cx, errorText, fmtDuration, fmtTime, Link, navigate, plural, useStored, usePoll } from "../lib";
import { ClipEditor } from "./ClipEditor";
import { ModelPicker } from "./ModelPicker";
import { Card, CardHeader } from "./ui";

type Filter = "all" | "pending" | "approved" | "rejected" | "hd" | "focus";
type Sort = "rank" | "score" | "hook" | "heat" | "short" | "long" | "time";

const FILTERS: { value: Filter; label: string; test: (c: Clip) => boolean }[] = [
  { value: "all", label: "All", test: () => true },
  { value: "pending", label: "To review", test: (c) => !c.review },
  { value: "approved", label: "Approved", test: (c) => c.review === "approved" },
  { value: "rejected", label: "Rejected", test: (c) => c.review === "rejected" },
  { value: "hd", label: "HD", test: (c) => !!c.final_url },
  { value: "focus", label: "Focus", test: (c) => !!c.focus_match },
];

const SORTS: Record<Sort, { label: string; fn: (a: Clip, b: Clip) => number }> = {
  rank: { label: "AI ranking", fn: (a, b) => a.rank - b.rank },
  score: { label: "Score", fn: (a, b) => b.score - a.score || a.rank - b.rank },
  hook: { label: "Hook score", fn: (a, b) => b.scores.hook - a.scores.hook || b.score - a.score },
  heat: { label: "Most replayed", fn: (a, b) => (b.heat ?? -1) - (a.heat ?? -1) || a.rank - b.rank },
  short: { label: "Shortest", fn: (a, b) => a.duration - b.duration },
  long: { label: "Longest", fn: (a, b) => b.duration - a.duration },
  time: { label: "Order in video", fn: (a, b) => a.start - b.start },
};

export function ClipsPanel({ job, onSeek, onJob }: { job: Job; onSeek: (t: number) => void; onJob: (j: Job) => void }) {
  const select = job.stages.find((s) => s.key === "select");
  const render = job.stages.find((s) => s.key === "render");
  const hasSelection = select?.status === "done";
  const rendering = render?.status === "running";
  const active = isActive(job.status);
  const exporting = isExporting(job);

  const { data, setData, error, reload } = usePoll<ClipsData | null>(
    () => (hasSelection ? api.clips(job.id) : Promise.resolve(null)),
    () => (rendering || exporting ? 3000 : null),
    `${job.id}:${select?.status}:${render?.status}:${job.export?.status}:${job.export?.files.length}:${job.export?.current}:${job.updated_at.slice(0, 16)}`,
  );
  const [filter, setFilter] = useStored<Filter>("clips.filter", "all");
  const [sort, setSort] = useStored<Sort>("clips.sort", "rank");
  const [editing, setEditing] = useState<Clip | null>(null);
  const [repicking, setRepicking] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();

  const clips = data?.clips ?? [];
  const shown = useMemo(() => {
    const f = FILTERS.find((x) => x.value === filter) ?? FILTERS[0];
    return clips.filter(f.test).sort((SORTS[sort] ?? SORTS.rank).fn);
  }, [clips, filter, sort]);

  async function exportClips(ids: string[] | null) {
    try {
      onJob(await api.exportClips(job.id, ids));
      toast.info(ids?.length === 1 ? "Clip queued for HD export" : "HD export queued");
    } catch (e) {
      toast.err("Couldn't start the export", errorText(e));
    }
  }

  async function stopExport() {
    try {
      onJob(await api.stopExport(job.id));
    } catch (e) {
      toast.err("Couldn't cancel", errorText(e));
    }
  }

  async function repick() {
    const reviewed = clips.filter((c) => c.review).length;
    const ok = await confirm({
      title: "Ask the AI to pick moments again?",
      body: <>The {plural(clips.length, "current clip")}{reviewed ? <> and <b className="text-fg">{plural(reviewed, "review")}</b></> : ""} are replaced. The transcript is reused, so it's much faster than the first run. Exported HD files stay in the export folder.</>,
      confirm: "Re-pick moments",
      danger: reviewed > 0,
    });
    if (!ok) return;
    setRepicking(true);
    try {
      onJob(await api.reselect(job.id));
    } catch (e) {
      toast.err("Couldn't re-pick", errorText(e));
    } finally {
      setRepicking(false);
    }
  }

  async function rerender() {
    try {
      onJob(await api.rerender(job.id));
      toast.info("Re-rendering previews with the current settings");
    } catch (e) {
      toast.err("Couldn't re-render", errorText(e));
    }
  }

  async function setReview(clip: Clip, review: Review) {
    const next = clip.review === review ? null : review;
    setData((d) => d && { ...d, clips: d.clips.map((c) => (c.id === clip.id ? { ...c, review: next } : c)) });
    try {
      await api.review(job.id, clip.id, next);
    } catch (e) {
      setData((d) => d && { ...d, clips: d.clips.map((c) => (c.id === clip.id ? { ...c, review: clip.review } : c)) });
      toast.err("Review not saved", errorText(e));
    }
  }

  async function bulk(review: Review, list: Clip[]) {
    if (!list.length) return;
    const before = new Map(list.map((c) => [c.id, c.review]));
    const ids = new Set(list.map((c) => c.id));
    setData((d) => d && { ...d, clips: d.clips.map((c) => (ids.has(c.id) ? { ...c, review } : c)) });
    try {
      const r = await api.reviews(job.id, [...ids], review);
      toast.ok(`${plural(r.updated, "clip")} ${review === "approved" ? "approved" : review === "rejected" ? "rejected" : "reset"}`);
    } catch (e) {
      setData((d) => d && { ...d, clips: d.clips.map((c) => (ids.has(c.id) ? { ...c, review: before.get(c.id) ?? null } : c)) });
      toast.err("Reviews not saved", errorText(e));
    }
  }

  if (!hasSelection || !data) {
    return (
      <Card>
        <CardHeader title="Clips" aside={select?.status === "running" ? "Finding moments…" : undefined} />
        {error ? (
          <div className="p-5 text-sm">
            <p className="text-err">Couldn't load the clips: {error}</p>
            <button onClick={reload} className="mt-2 text-xs font-medium text-accent hover:underline">Try again</button>
          </div>
        ) : hasSelection ? (
          <Finding message="Loading clips…" />
        ) : select?.status === "running" ? (
          <Finding message={select.message} />
        ) : (
          <Empty failed={select?.status === "failed"} />
        )}
      </Card>
    );
  }

  const counts = Object.fromEntries(FILTERS.map((f) => [f.value, clips.filter(f.test).length])) as Record<Filter, number>;
  const approvedNotHd = clips.filter((c) => c.review === "approved" && !c.final_url).length;
  const outdated = !active && render?.status === "done" ? clips.filter((c) => !c.ready).length : 0;
  const totalSecs = clips.reduce((a, c) => a + c.duration, 0);
  const dropped = Object.entries(data.rejected ?? {}).filter(([, n]) => n > 0);
  const pendingShown = shown.filter((c) => !c.review);

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            Clips <span className="font-mono text-xs font-normal text-muted">{clips.length} · {fmtDuration(totalSecs)}</span>
          </span>
        }
        aside={
          clips.length > 0 && (
            <div className="flex flex-wrap items-center justify-end gap-2">
              {job.stats.exported > 0 && (
                <a href={api.zipUrl(job.id)} download
                  className="inline-flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-xs font-semibold text-muted transition hover:border-line-strong hover:text-fg"
                  title="Every exported clip of this video, with its upload text, as one .zip">
                  <FileArchive className="size-3.5" /> Download all HD
                </a>
              )}
              {counts.approved > 0 && (
                <button onClick={() => exportClips(null)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-accent px-2.5 py-1 text-xs font-semibold text-white transition hover:opacity-90"
                  title="Render every approved clip in full quality (1080×1920). Clips already exported with the same settings are reused.">
                  <Download className="size-3.5" /> Export approved ({counts.approved}{approvedNotHd && approvedNotHd !== counts.approved ? `, ${approvedNotHd} new` : ""})
                </button>
              )}
            </div>
          )
        }
      />

      <ExportBanner ex={job.export} onCancel={stopExport} zipUrl={job.stats.exported > 0 ? api.zipUrl(job.id) : null} />

      {data.note && (
        <div className="mx-5 mt-4 flex gap-2.5 rounded-xl border border-warn/30 bg-warn/5 p-3 text-xs leading-relaxed text-muted">
          <CircleAlert className="mt-px size-3.5 shrink-0 text-warn" />
          <span>{data.note}</span>
        </div>
      )}

      {outdated > 0 && (
        <div className="mx-5 mt-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-line bg-surface-2 p-3 text-xs text-muted">
          <RefreshCw className="size-3.5 shrink-0 text-accent" />
          <span className="min-w-0 flex-1">{plural(outdated, "preview")} {outdated === 1 ? "is" : "are"} out of date: settings changed since {outdated === 1 ? "it was" : "they were"} rendered.</span>
          <button onClick={rerender} disabled={exporting} className="font-semibold text-accent hover:underline disabled:opacity-50">Re-render now</button>
        </div>
      )}

      {clips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-5 py-3">
          <div className="flex flex-wrap gap-0.5 rounded-lg bg-surface-2 p-0.5" role="tablist" aria-label="Filter clips">
            {FILTERS.filter((f) => f.value !== "focus" || counts.focus > 0 || filter === "focus").map((f) => (
              <button key={f.value} role="tab" aria-selected={filter === f.value} onClick={() => setFilter(f.value)}
                className={cx("rounded-md px-2.5 py-1 text-xs font-medium transition",
                  filter === f.value ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg")}>
                {f.label} <span className="font-mono text-faint">{counts[f.value]}</span>
              </button>
            ))}
          </div>
          <Select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort clips" className="h-8 w-auto py-1 text-xs">
            {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </Select>
          <div className="ml-auto flex flex-wrap items-center gap-1">
            {pendingShown.length > 0 && (
              <BulkButton onClick={() => bulk("approved", pendingShown)} title="Approve every clip shown that isn't reviewed yet">
                <CheckCheck className="size-3.5" /> Approve {pendingShown.length === shown.length ? "all" : pendingShown.length}
              </BulkButton>
            )}
            {shown.some((c) => c.review) && (
              <BulkButton onClick={() => bulk(null, shown.filter((c) => c.review))} title="Clear the review of every clip shown">
                <Undo2 className="size-3.5" /> Reset
              </BulkButton>
            )}
          </div>
        </div>
      )}

      {clips.length === 0 ? (
        <p className="px-5 py-10 text-center text-sm text-muted">
          No moment passed the checks{data.candidates ? ` (${data.candidates} proposed)` : ""}. Lower “Minimum score” in Settings, then Re-pick.
        </p>
      ) : shown.length === 0 ? (
        <p className="px-5 py-10 text-center text-sm text-muted">
          No clips here.{" "}
          <button className="font-medium text-accent hover:underline" onClick={() => setFilter("all")}>Show all</button>
        </p>
      ) : (
        <div className="grid gap-4 p-5 sm:grid-cols-2 xl:grid-cols-3">
          {shown.map((c) => (
            <ClipCard
              key={c.id}
              clip={c}
              ex={job.export}
              outdated={!c.ready && !active && render?.status === "done"}
              onSeek={onSeek}
              onReview={(r) => setReview(c, r)}
              onExport={() => exportClips([c.id])}
              onEdit={() => setEditing(c)}
              editHref={editorHref(job.id, c.id)}
            />
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-line px-5 py-3 text-xs text-faint">
        <span title={dropped.map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`).join(" · ") || undefined}>
          {data.source === "gemini" ? `Picked by ${data.model ?? "AI"}` : "Picked offline (no AI)"}
          {data.candidates ? ` · ${data.candidates} proposed, ${clips.length} kept${dropped.length ? " (hover for why)" : ""}` : ""}
          {" · "}low-res previews, export for HD
          <span className="hidden lg:inline"> · keys: <Kbd>A</Kbd> approve <Kbd>X</Kbd> reject <Kbd>E</Kbd> edit</span>
        </span>
        <span className="flex flex-wrap items-center gap-3">
          <ModelPicker up />
          <button onClick={repick} disabled={repicking || active || exporting}
            className="inline-flex items-center gap-1 rounded-md text-muted transition hover:text-fg disabled:opacity-40"
            title={active ? "Available when processing finishes" : "Ask the selected AI model again, then reframe, caption and preview"}>
            <RefreshCw className={cx("size-3", repicking && "animate-spin")} /> Re-pick moments
          </button>
        </span>
      </div>

      <ClipEditor
        jobId={job.id}
        clip={editing}
        busy={active}
        onClose={() => setEditing(null)}
        onSaved={(c, rerender) => {
          setData((d) => d && { ...d, clips: d.clips.map((x) => (x.id === c.id ? c : x)) });
          if (rerender) api.job(job.id).then(onJob, () => {});
        }}
      />
    </Card>
  );
}

function BulkButton({ onClick, title, children }: { onClick: () => void; title: string; children: React.ReactNode }) {
  return (
    <button onClick={onClick} title={title}
      className="inline-flex h-7 items-center gap-1 rounded-lg px-2 text-xs font-medium text-muted transition hover:bg-surface-2 hover:text-fg">
      {children}
    </button>
  );
}

function ExportBanner({ ex, onCancel, zipUrl }: { ex: ExportState | null; onCancel: () => void; zipUrl: string | null }) {
  if (!ex) return null;
  const active = ex.status === "queued" || ex.status === "running";
  const cancelled = ex.status === "failed" && ex.message?.startsWith("Export cancelled");
  return (
    <div className={cx("mx-5 mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-xs",
      ex.status === "failed" && !cancelled ? "border-err/30 bg-err/5" : "border-line bg-surface-2")}>
      {active ? <LoaderCircle className="size-3.5 animate-spin text-accent" />
        : ex.status === "failed" && !cancelled ? <CircleAlert className="size-3.5 text-err" />
        : <Check className="size-3.5 text-ok" />}
      <span className="font-medium">
        {active ? (ex.status === "queued" ? "HD export waiting" : "Exporting HD") : cancelled ? "Export cancelled" : ex.status === "failed" ? "Export failed" : "HD export done"}
        {active && ex.progress != null ? ` · ${Math.floor(ex.progress * 100)}%` : ""}
        {active && ex.queue.length > 0 ? ` · ${ex.queue.length} more queued` : ""}
      </span>
      {ex.message && !cancelled && <span className="min-w-0 text-muted">{ex.message}</span>}
      {active && (
        <button onClick={onCancel} className="ml-auto inline-flex items-center gap-1 font-medium text-muted hover:text-fg">
          <Square className="size-3 fill-current" /> Cancel
        </button>
      )}
      {!active && ex.dir && (
        <span className="flex min-w-0 items-center gap-1 font-mono text-[11px] text-faint" title="Exported files + upload text (on the server)">
          <FolderOpen className="size-3 shrink-0" />
          <span className="truncate">{ex.dir}</span>
        </span>
      )}
      {!active && zipUrl && ex.status === "done" && (
        <a href={zipUrl} download className="ml-auto font-medium text-accent hover:underline">Download .zip</a>
      )}
    </div>
  );
}

function ClipCard({ clip: c, ex, outdated, onSeek, onReview, onExport, onEdit, editHref }: {
  clip: Clip;
  ex: ExportState | null;
  outdated: boolean;
  onSeek: (t: number) => void;
  onReview: (r: Review) => void;
  onExport: () => void;
  onEdit: () => void;
  editHref: string;
}) {
  const toast = useToast();
  const exportActive = !!ex && (ex.status === "running" || ex.status === "queued");
  const exporting = ex?.status === "running" && ex.current === c.id;
  const queued = exportActive && ex!.queue.includes(c.id);

  function onKey(e: KeyboardEvent) {
    if (e.target !== e.currentTarget || e.metaKey || e.ctrlKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (k === "a") onReview("approved");
    else if (k === "x" || k === "r") onReview("rejected");
    else if (k === "e") onEdit();
    else if (k === "enter" || k === "o") navigate(editHref);
    else return;
    e.preventDefault();
  }

  async function copy() {
    if (await copyText(c.upload_text)) toast.ok("Upload text copied", "Title, description and hashtags");
    else toast.err("Copy failed");
  }

  return (
    <article
      tabIndex={0}
      onKeyDown={onKey}
      aria-label={`Clip ${c.rank}: ${c.title}`}
      className={cx(
        "fade-in flex flex-col overflow-hidden rounded-xl border bg-surface transition outline-none focus-visible:ring-2 focus-visible:ring-accent",
        c.review === "approved" ? "border-ok/50 ring-1 ring-ok/30" : "border-line",
        c.review === "rejected" && "opacity-55",
      )}
    >
      <div className="relative aspect-[9/16] bg-black">
        {c.ready && c.url ? (
          <Link to={editHref} className="group absolute inset-0 block" title="Open the editor: framing, window shape, hook" tabIndex={-1}>
            <video src={`${c.url}#t=0.1`} muted loop preload="metadata" playsInline className="absolute inset-0 size-full"
              onMouseEnter={(e) => { e.currentTarget.play().catch(() => {}); }}
              onMouseLeave={(e) => { e.currentTarget.pause(); }} />
            <span className="pointer-events-none absolute inset-x-0 bottom-3 mx-auto flex w-fit items-center gap-1.5 rounded-lg bg-black/70 px-2.5 py-1.5 text-xs font-medium text-white opacity-0 backdrop-blur transition group-hover:opacity-100 group-focus-visible:opacity-100">
              <Crop className="size-3.5" /> Open editor
            </span>
          </Link>
        ) : outdated ? (
          <div className="absolute inset-0 grid place-items-center bg-surface-2 p-4 text-center">
            <span className="text-xs text-muted"><RefreshCw className="mx-auto mb-2 size-4" />Preview out of date<br />Re-render to see it</span>
          </div>
        ) : (
          <div className="shimmer absolute inset-0 grid place-items-center">
            <span className="flex items-center gap-1.5 text-xs text-white/60"><LoaderCircle className="size-3.5 animate-spin" /> Rendering</span>
          </div>
        )}
        <span className="pointer-events-none absolute top-2 left-2 rounded-md bg-black/60 px-1.5 py-0.5 font-mono text-[11px] text-white backdrop-blur">#{c.rank}</span>
        <span className={cx("pointer-events-none absolute top-2 right-2 rounded-md px-1.5 py-0.5 font-mono text-[11px] font-semibold backdrop-blur",
          c.score >= 7 ? "bg-accent text-white" : "bg-black/60 text-white")} title="Overall score: 35% hook, 25% standalone, 25% payoff, 15% energy">
          {c.score.toFixed(1)}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-2.5 p-3.5">
        <div className="flex items-start gap-1.5">
          <h3 className="min-w-0 flex-1 text-sm leading-snug font-semibold text-balance break-words">{c.title}</h3>
          <Link to={editHref} className="-mt-0.5 shrink-0 rounded-md p-1 text-faint transition hover:bg-surface-2 hover:text-fg" title="Open the editor: framing, window shape, hook (Enter)" aria-label="Open the clip editor">
            <Crop className="size-3.5" />
          </Link>
          <button onClick={onEdit} className="-mt-0.5 shrink-0 rounded-md p-1 text-faint transition hover:bg-surface-2 hover:text-fg" title="Edit title, hook, description, hashtags (E)" aria-label="Edit clip">
            <Pencil className="size-3.5" />
          </button>
        </div>
        {(c.hook_text || c.show_hook === false || c.loop || c.focus_match || (c.heat != null && c.heat >= 0.6)) && (
          <div className="flex flex-wrap gap-1.5">
            {c.focus_match && (
              <span className="inline-flex items-center gap-0.5 rounded-md bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent" title="Matches your focus note">
                <Sparkles className="size-3" /> Focus
              </span>
            )}
            {c.show_hook === false ? (
              <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium text-muted" title="The first spoken line already states the premise, so no text banner is shown">
                Spoken hook · no banner
              </span>
            ) : c.hook_text && (
              <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium" title="On-screen hook text">{c.hook_text}</span>
            )}
            {c.loop && <span className="rounded-md bg-surface-2 px-1.5 py-0.5 text-[11px] font-medium text-muted" title="The last line leads back into the first: replays without a seam">Loops</span>}
            {c.heat != null && c.heat >= 0.6 && (
              <span className="inline-flex items-center gap-0.5 rounded-md bg-warn/10 px-1.5 py-0.5 text-[11px] font-medium text-warn" title={`YouTube “Most replayed”: ${Math.round(c.heat * 100)}% of the video's peak`}>
                <Flame className="size-3" /> Most replayed
              </span>
            )}
          </div>
        )}
        <p className="line-clamp-2 text-xs leading-relaxed text-muted italic">“{c.hook}”</p>
        <p className="line-clamp-3 text-xs leading-relaxed text-muted" title={c.reason}>{c.reason}</p>
        <Scores scores={c.scores} heat={c.heat} />

        <div className="mt-auto flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 pt-1">
          <span className="flex items-center gap-0.5">
            <button onClick={() => onSeek(c.start)} className="inline-flex items-center gap-1 rounded-md font-mono text-[11px] text-muted transition hover:text-fg"
              title={`Play in the source video · ${fmtTime(c.start)}–${fmtTime(c.end)}`}>
              <Clock className="size-3" /> {fmtTime(c.start)} · {Math.round(c.duration)}s
            </button>
            <button onClick={copy} className="rounded-md p-1 text-faint transition hover:text-fg" title="Copy title, description and hashtags for the upload form" aria-label="Copy upload text">
              <Copy className="size-3" />
            </button>
          </span>
          <div className="ml-auto flex items-center gap-1">
            {exporting || queued ? (
              <span className="inline-flex h-7 items-center gap-1 rounded-lg px-2 font-mono text-[11px] text-muted">
                <LoaderCircle className="size-3.5 animate-spin" />
                {exporting ? `${Math.floor((ex?.progress ?? 0) * 100)}%` : "queued"}
              </span>
            ) : c.final_url ? (
              <a href={c.final_url} download className="inline-flex h-7 items-center gap-1 rounded-lg border border-ok/50 px-2 text-[11px] font-semibold text-ok transition hover:bg-ok/10"
                title="Download the full-quality 1080×1920 version">
                <Download className="size-3.5" /> HD
              </a>
            ) : (
              <button onClick={onExport} className="inline-flex h-7 items-center gap-1 rounded-lg border border-line px-2 text-[11px] font-semibold whitespace-nowrap text-muted transition hover:border-line-strong hover:text-fg"
                title="Render this clip in full quality (1080×1920, loudness-normalised)">
                <Download className="size-3.5" /> Export HD
              </button>
            )}
            <ReviewButton active={c.review === "rejected"} kind="rejected" onClick={() => onReview("rejected")} />
            <ReviewButton active={c.review === "approved"} kind="approved" onClick={() => onReview("approved")} />
          </div>
        </div>
      </div>
    </article>
  );
}

function ReviewButton({ kind, active, onClick }: { kind: "approved" | "rejected"; active: boolean; onClick: () => void }) {
  const ok = kind === "approved";
  return (
    <button onClick={onClick} aria-pressed={active} aria-label={ok ? "Approve clip" : "Reject clip"} title={ok ? "Approve (A)" : "Reject (X)"}
      className={cx("grid size-7 place-items-center rounded-lg border transition",
        active ? (ok ? "border-ok bg-ok text-white" : "border-err bg-err text-white") : "border-line text-muted hover:border-line-strong hover:text-fg")}>
      {ok ? <Check className="size-3.5" strokeWidth={2.5} /> : <X className="size-3.5" strokeWidth={2.5} />}
    </button>
  );
}

const SCORE_LABELS: [keyof Clip["scores"], string, string][] = [
  ["hook", "Hook", "Do the first ~3 s make you stay?"],
  ["standalone", "Standalone", "Understandable without the rest of the video"],
  ["payoff", "Payoff", "Does it land / resolve?"],
  ["energy", "Energy", "Emotion, stakes, surprise"],
];

function Scores({ scores, heat }: { scores: Clip["scores"]; heat: number | null }) {
  return (
    <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
      {SCORE_LABELS.map(([k, label, hint]) => (
        <div key={k} className="min-w-0" title={hint}>
          <div className="flex items-baseline justify-between text-[10px] text-faint">
            <dt>{label}</dt>
            <dd className="font-mono">{Number(scores[k]).toFixed(1).replace(/\.0$/, "")}</dd>
          </div>
          <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-fg/60" style={{ width: `${Math.min(100, Math.max(0, scores[k] * 10))}%` }} />
          </div>
        </div>
      ))}
      {heat != null && (
        <div className="col-span-2 min-w-0" title="How much viewers rewatched this part (YouTube “Most replayed”), vs the video's peak">
          <div className="flex items-baseline justify-between text-[10px] text-faint">
            <dt>Replayed</dt>
            <dd className="font-mono">{Math.round(heat * 100)}%</dd>
          </div>
          <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full rounded-full bg-warn/70" style={{ width: `${Math.round(heat * 100)}%` }} />
          </div>
        </div>
      )}
    </dl>
  );
}

function Finding({ message }: { message: string | null }) {
  return (
    <div className="p-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => <div key={i} className="shimmer aspect-[9/16] rounded-xl" />)}
      </div>
      {message && <p className="mt-4 text-center text-xs text-muted">{message}</p>}
    </div>
  );
}

function Empty({ failed }: { failed: boolean }) {
  return (
    <div className="flex flex-col items-center gap-5 px-6 py-10 sm:flex-row sm:items-center sm:gap-8 sm:px-8">
      <div className="flex shrink-0 gap-2" aria-hidden>
        {[0, 1, 2].map((i) => (
          <div key={i} className="aspect-[9/16] w-12 rounded-lg border border-dashed border-line-strong bg-surface-2"
            style={{ transform: `rotate(${(i - 1) * 6}deg) translateY(${i === 1 ? -4 : 0}px)` }} />
        ))}
      </div>
      <div className="text-center sm:text-left">
        <p className="flex items-center justify-center gap-1.5 text-sm font-medium sm:justify-start">
          <Sparkles className="size-4 text-accent" /> {failed ? "Finding moments failed" : "Vertical clips land here"}
        </p>
        <p className="mt-1 max-w-md text-sm text-muted">
          {failed
            ? "See the error above. Retry once it's fixed: the transcript is kept."
            : "Once the transcript is ready, the AI picks the moments worth posting. Each clip shows its score, the hook and why it was picked, ready for you to approve."}
        </p>
      </div>
    </div>
  );
}
