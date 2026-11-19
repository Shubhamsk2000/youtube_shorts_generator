import { useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, Clapperboard, LoaderCircle, Settings, WifiOff } from "lucide-react";
import { cx, Link } from "@kit/lib";
import { jobHref } from "../shorts/api";
import { KIND_LABEL, type Status } from "./api";

export type Section = "projects" | "settings" | "system" | "none";

const NAV: { key: Section; to: string; label: string; icon: ReactNode }[] = [
  { key: "projects", to: "/", label: "Projects", icon: <Clapperboard className="size-4" /> },
  { key: "settings", to: "/settings", label: "Settings", icon: <Settings className="size-4" /> },
  { key: "system", to: "/system", label: "System", icon: <Activity className="size-4" /> },
];

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="8" className="fill-fg" />
        <rect x="10" y="6" width="12" height="20" rx="3" fill="none" strokeWidth="2" className="stroke-bg" />
        <path d="M14 13v6l5-3z" className="fill-accent" />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight">Shorts Studio</span>
    </span>
  );
}

export function Nav({ section, status, offline }: { section: Section; status?: Status; offline: boolean }) {
  return (
    <>
      <nav className="sticky top-0 z-30 border-b border-line bg-bg/80 backdrop-blur-xl" aria-label="Main">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:px-6">
          <Link to="/" className="mr-2 shrink-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="Projects">
            <Logo />
          </Link>
          <div className="hidden items-center gap-0.5 sm:flex">
            {NAV.map((n) => <NavLink key={n.key} item={n} active={section === n.key} />)}
          </div>
          <div className="ml-auto"><QueuePill status={status} offline={offline} /></div>
        </div>
        <div className="flex gap-1 overflow-x-auto border-t border-line px-2 py-1.5 sm:hidden">
          {NAV.map((n) => <NavLink key={n.key} item={n} active={section === n.key} />)}
        </div>
      </nav>
      {offline && (
        <div role="alert" className="border-b border-err/30 bg-err/10">
          <div className="mx-auto flex max-w-6xl items-center gap-2 px-4 py-2 text-sm text-err sm:px-6">
            <WifiOff className="size-4 shrink-0" />
            <span className="min-w-0 flex-1">Can't reach Studio. Reconnecting automatically; nothing you did is lost.</span>
          </div>
        </div>
      )}
    </>
  );
}

function NavLink({ item, active }: { item: (typeof NAV)[number]; active: boolean }) {
  return (
    <Link
      to={item.to}
      aria-current={active ? "page" : undefined}
      className={cx(
        "inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium transition",
        active ? "bg-surface-2 text-fg" : "text-muted hover:text-fg",
      )}
    >
      {item.icon}
      {item.label}
    </Link>
  );
}

/** What the background worker is doing right now. */
function QueuePill({ status, offline }: { status?: Status; offline: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const cur = status?.queue.current;
  const waiting = status?.queue.queued.length ?? 0;
  const label = offline ? "Offline" : !status ? "Connecting…" : cur ? `${KIND_LABEL[cur.kind] ?? "Working"}${waiting ? ` · ${waiting} waiting` : ""}` : "Idle";
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex max-w-[240px] items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-muted hover:text-fg"
      >
        {cur && !offline ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-accent" />
          : <span className={cx("size-1.5 shrink-0 rounded-full", offline ? "bg-err" : status ? "bg-ok" : "bg-faint")} />}
        <span className="truncate">{label}</span>
      </button>
      {open && status && (
        <div className="absolute right-0 z-40 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-2xl border border-line bg-surface p-3 shadow-card fade-in">
          <p className="px-1 pb-2 text-xs font-medium text-muted">Worker · one task at a time</p>
          {!cur && !waiting && <p className="px-1 pb-1 text-sm">Nothing running.</p>}
          <ul className="space-y-1">
            {[cur, ...status.queue.queued].filter(Boolean).map((t, i) => (
              <li key={`${t!.job_id}-${t!.kind}-${i}`}>
                <Link to={jobHref(t!.job_id)} onClick={() => setOpen(false)}
                  className="flex items-center gap-2 rounded-lg px-1 py-1.5 text-sm hover:bg-surface-2">
                  {i === 0 && cur ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-accent" />
                    : <span className="size-1.5 shrink-0 rounded-full bg-faint" />}
                  <span className="min-w-0 flex-1 truncate">{t!.title}</span>
                  <span className="shrink-0 text-xs text-muted">{i === 0 && cur ? "" : "waiting · "}{KIND_LABEL[t!.kind] ?? t!.kind}</span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-2 grid grid-cols-2 gap-2 border-t border-line px-1 pt-2 text-xs text-muted">
            <span className={cx(status.disk_free_gb < 5 && "text-warn")}>{status.disk_free_gb} GB disk free</span>
            <span className={cx("text-right", status.ram_available_gb < 1.5 && "text-warn")}>{status.ram_available_gb} GB RAM free</span>
          </div>
          <Link to="/system" onClick={() => setOpen(false)} className="mt-2 block px-1 text-xs font-medium text-accent hover:underline">
            System status →
          </Link>
        </div>
      )}
    </div>
  );
}

export function Footer({ status }: { status?: Status }) {
  return (
    <footer className="mt-auto border-t border-line">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-5 text-xs text-faint sm:px-6">
        <span>Everything is processed on this machine. Upload as private first and check YouTube's copyright result.</span>
        <span className="flex items-center gap-4">
          <a href="/api/docs" target="_blank" rel="noreferrer" className="hover:text-fg">API docs</a>
          <span className="font-mono">v{status?.version ?? "–"}</span>
        </span>
      </div>
    </footer>
  );
}
