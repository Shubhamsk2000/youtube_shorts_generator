import { useCallback, useEffect, useMemo, useRef, useState, type ButtonHTMLAttributes } from "react";
import { createPortal } from "react-dom";
import { ArrowDown, Check, CircleAlert, Copy, Info, Maximize2, Search, TriangleAlert, X } from "lucide-react";
import { api, isActive, isExporting, type Job, type LogEntry } from "../api";
import { useToast } from "@kit/feedback";
import { copyText, cx } from "../lib";

const MAX_LINES = 3000;
type Filter = "all" | "issues";

/** The project's activity log, live: what the pipeline is doing right now, step by step. */
export function Console({ job, onIssues }: { job: Job; onIssues?: (n: number) => void }) {
  const { entries, loaded } = useLog(job);
  const [expanded, setExpanded] = useState(false);
  const issues = useMemo(() => entries.filter((e) => e.level === "warn" || e.level === "error").length, [entries]);
  useEffect(() => onIssues?.(issues), [issues, onIssues]);
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(false);
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = ""; };
  }, [expanded]);
  return (
    <>
      <LogView job={job} entries={entries} loaded={loaded} height="h-[420px]" onExpand={() => setExpanded(true)} />
      {expanded && createPortal(
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-0 backdrop-blur-sm sm:p-6"
          onMouseDown={(e) => e.target === e.currentTarget && setExpanded(false)}>
          <div className="fade-in flex h-full w-full max-w-6xl flex-col overflow-hidden border border-white/10 bg-[#0c0c0f] shadow-2xl sm:h-[86dvh] sm:rounded-2xl">
            <LogView job={job} entries={entries} loaded={loaded} height="min-h-0 flex-1" wide onClose={() => setExpanded(false)} />
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}

/** Polls the log incrementally: fast while the project is working, slowly otherwise. */
function useLog(job: Job) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const last = useRef(-1);
  const busy = isActive(job.status) || isExporting(job);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const load = useCallback(async () => {
    try {
      const page = await api.log(job.id, last.current);
      if (page.entries.length) {
        last.current = page.last;
        setEntries((old) => {
          const next = [...old, ...page.entries];
          return next.length > MAX_LINES ? next.slice(-MAX_LINES) : next;
        });
      }
      setLoaded(true);
    } catch {
      /* the page shows the connection state already */
    }
  }, [job.id]);

  useEffect(() => {
    last.current = -1;
    setEntries([]);
    setLoaded(false);
    let stop = false;
    let id = 0;
    const tick = async () => {
      if (!document.hidden) await load();
      if (!stop) id = window.setTimeout(tick, busyRef.current ? 1200 : 6000);
    };
    tick();
    return () => { stop = true; window.clearTimeout(id); };
  }, [job.id, load]);

  useEffect(() => { if (busy) load(); }, [busy, load]); // a run just started: show it at once
  return { entries, loaded };
}

function LogView({ job, entries, loaded, height, wide, onExpand, onClose }: {
  job: Job; entries: LogEntry[]; loaded: boolean; height: string; wide?: boolean;
  onExpand?: () => void; onClose?: () => void;
}) {
  const toast = useToast();
  const box = useRef<HTMLDivElement>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [follow, setFollow] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const seen = useRef(entries.length);
  const labels = useMemo(() => Object.fromEntries([...job.stages.map((s) => [s.key, s.label]), ["export", "Export"]]), [job.stages]);
  const live = isActive(job.status) || isExporting(job);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return entries.filter((e) => (filter === "all" || e.level === "warn" || e.level === "error")
      && (!needle || e.msg.toLowerCase().includes(needle) || (labels[e.stage ?? ""] ?? "").toLowerCase().includes(needle)));
  }, [entries, filter, q, labels]);
  const issues = entries.filter((e) => e.level === "warn" || e.level === "error").length;

  // stick to the bottom while following; otherwise count what arrived below
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (follow) {
      el.scrollTop = el.scrollHeight;
      setUnseen(0);
    } else setUnseen((n) => n + Math.max(0, entries.length - seen.current));
    seen.current = entries.length;
  }, [entries.length, shown.length, follow]);

  function onScroll() {
    const el = box.current!;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atBottom !== follow) setFollow(atBottom);
    if (atBottom) setUnseen(0);
  }

  async function copyAll() {
    const text = shown.map((e) => `${fmtStamp(e.t)}  ${e.level.toUpperCase().padEnd(5)}  ${(labels[e.stage ?? ""] ?? "-").padEnd(14)}  ${e.msg}`).join("\n");
    (await copyText(text)) ? toast.ok(`${shown.length} log lines copied`) : toast.err("Copy failed");
  }

  return (
    <div className={cx("flex flex-col overflow-hidden bg-[#0c0c0f] text-zinc-300", wide ? "h-full" : "rounded-b-2xl")}>
      {/* toolbar */}
      <div className="flex items-center gap-2 border-b border-white/[0.07] px-3 py-2">
        <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-zinc-400">
          <span className={cx("size-1.5 rounded-full", live ? "animate-pulse bg-emerald-400" : "bg-zinc-600")} />
          {live ? "Live" : "Idle"}
        </span>
        <div className="ml-1 inline-flex rounded-md bg-white/[0.04] p-0.5 text-[11px] font-medium">
          {([["all", `All ${entries.length}`], ["issues", `Issues ${issues}`]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setFilter(k)}
              className={cx("rounded px-2 py-0.5 whitespace-nowrap tabular-nums transition",
                filter === k ? "bg-white/10 text-zinc-100" : "text-zinc-500 hover:text-zinc-300",
                k === "issues" && issues > 0 && filter !== k && "text-amber-400/80")}>
              {label}
            </button>
          ))}
        </div>
        <label className={cx("relative ml-auto flex items-center", !wide && "hidden")}>
          <Search className="pointer-events-none absolute left-2 size-3 text-zinc-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter"
            className={cx("h-6 rounded-md border border-white/[0.08] bg-white/[0.03] pr-2 pl-6 text-[11px] text-zinc-200 placeholder:text-zinc-600 focus:border-white/20 focus:outline-none",
              wide ? "w-56" : "w-24")} />
        </label>
        <ToolButton onClick={copyAll} title="Copy the visible lines" className={cx(!wide && "ml-auto")}><Copy className="size-3.5" /></ToolButton>
        {onExpand && <ToolButton onClick={onExpand} title="Open the console full size"><Maximize2 className="size-3.5" /></ToolButton>}
        {onClose && <ToolButton onClick={onClose} title="Close (Esc)"><X className="size-4" /></ToolButton>}
      </div>

      {/* lines */}
      <div className={cx("relative", height)}>
        <div ref={box} onScroll={onScroll} className={cx("console-scroll absolute inset-0 overflow-y-auto overscroll-contain py-1.5 font-mono leading-[1.55]",
          wide ? "text-[12px]" : "text-[11px]")}>
          {!loaded ? (
            <div className="space-y-2 px-3 py-2">{[70, 85, 55, 90].map((w) => <div key={w} className="h-3 animate-pulse rounded bg-white/[0.05]" style={{ width: `${w}%` }} />)}</div>
          ) : !shown.length ? (
            <div className="grid h-full place-items-center px-6 text-center font-sans text-xs text-zinc-500">
              {entries.length ? "No lines match." : (
                <span>No activity logged yet.<br /><span className="text-zinc-600">The next run (retry, re-render, export…) is logged here step by step.</span></span>
              )}
            </div>
          ) : (
            shown.map((e, k) => {
              const day = fmtDay(e.t);
              const newDay = k === 0 || fmtDay(shown[k - 1].t) !== day;
              return (
                <div key={e.i}>
                  {newDay && (
                    <div className="sticky top-0 z-10 flex items-center gap-2 bg-[#0c0c0f]/95 px-3 py-1 font-sans text-[10px] font-medium tracking-wide text-zinc-500 uppercase backdrop-blur">
                      {day}<span className="h-px flex-1 bg-white/[0.06]" />
                    </div>
                  )}
                  <Line e={e} label={labels[e.stage ?? ""]} wide={wide} />
                </div>
              );
            })
          )}
        </div>
        {!follow && (
          <button onClick={() => { setFollow(true); const el = box.current; if (el) el.scrollTop = el.scrollHeight; }}
            className="fade-in absolute bottom-3 left-1/2 inline-flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-white/10 bg-zinc-800/95 px-3 py-1 font-sans text-[11px] font-medium text-zinc-100 shadow-lg backdrop-blur hover:bg-zinc-700">
            <ArrowDown className="size-3" /> {unseen > 0 ? `${unseen} new` : "Latest"}
          </button>
        )}
      </div>
    </div>
  );
}

const TONE: Record<LogEntry["level"], { icon: typeof Info; c: string; text: string; row: string }> = {
  info: { icon: Info, c: "text-zinc-500", text: "text-zinc-300", row: "" },
  ok: { icon: Check, c: "text-emerald-400", text: "text-emerald-300", row: "" },
  warn: { icon: TriangleAlert, c: "text-amber-400", text: "text-amber-200", row: "bg-amber-400/[0.05]" },
  error: { icon: CircleAlert, c: "text-red-400", text: "text-red-300", row: "bg-red-500/[0.08]" },
};

function Line({ e, label, wide }: { e: LogEntry; label?: string; wide?: boolean }) {
  const t = TONE[e.level] ?? TONE.info;
  const Icon = t.icon;
  if (!wide) {  // the side panel: the message gets the full width, time and step underneath
    return (
      <div className={cx("flex gap-2 px-3 py-1.5 hover:bg-white/[0.03]", t.row)}>
        <Icon className={cx("mt-[3px] size-3 shrink-0", t.c)} strokeWidth={2.5} />
        <div className="min-w-0 flex-1">
          <p className={cx("break-words", t.text)}>{e.msg}</p>
          <p className="mt-0.5 font-sans text-[10px] text-zinc-500" title={new Date(e.t).toLocaleString()}>
            <span className="tabular-nums">{fmtClock(e.t)}</span>{label && <> · {label}</>}
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className={cx("group flex items-start gap-2.5 px-4 py-[3px] hover:bg-white/[0.03]", t.row)}>
      <span className="shrink-0 text-zinc-600 tabular-nums select-none" title={new Date(e.t).toLocaleString()}>{fmtClock(e.t)}</span>
      <Icon className={cx("mt-[4px] size-3 shrink-0", t.c)} strokeWidth={2.5} />
      <span className="mt-[2px] w-[116px] shrink-0 truncate font-sans text-[10.5px] font-medium text-zinc-500" title={label}>
        {label ?? ""}
      </span>
      <span className={cx("min-w-0 flex-1 break-words", t.text)}>{e.msg}</span>
    </div>
  );
}

function ToolButton({ className, ...rest }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button {...rest} aria-label={rest.title} className={cx("grid size-6 place-items-center rounded-md text-zinc-500 transition hover:bg-white/[0.07] hover:text-zinc-200", className)} />;
}

const fmtClock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const fmtStamp = (iso: string) => { const d = new Date(iso); return `${d.toLocaleDateString()} ${fmtClock(iso)}`; };
function fmtDay(iso: string) {
  const d = new Date(iso);
  const today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === y.toDateString()) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
}
