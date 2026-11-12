import { useEffect, useRef, useState, type ReactNode } from "react";
import { Clapperboard, Home, LoaderCircle, Music2, Scissors, UserRound } from "lucide-react";
import { cx, Link } from "@kit/lib";
import { jobHref, KIND_LABEL, type Status } from "./api";

export type Section = "home" | "shorts" | "anime" | "music" | "channel";

const NAV: { key: Section; to: string; label: string; icon: ReactNode }[] = [
  { key: "home", to: "/", label: "Home", icon: <Home className="size-4" /> },
  { key: "shorts", to: "/shorts", label: "Shorts", icon: <Scissors className="size-4" /> },
  { key: "anime", to: "/anime", label: "Anime", icon: <Clapperboard className="size-4" /> },
  { key: "music", to: "/anime/music", label: "Music", icon: <Music2 className="size-4" /> },
  { key: "channel", to: "/settings/channel", label: "Channel", icon: <UserRound className="size-4" /> },
];

export function Logo() {
  return (
    <span className="flex items-center gap-2.5">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="8" className="fill-fg" />
        <rect x="10" y="6" width="12" height="20" rx="3" fill="none" strokeWidth="2" className="stroke-bg" />
        <path d="M14 13v6l5-3z" className="fill-accent" />
      </svg>
      <span className="text-[15px] font-semibold tracking-tight">Studio</span>
    </span>
  );
}

export function Nav({ section, status, offline }: { section: Section; status?: Status; offline: boolean }) {
  return (
    <nav className="sticky top-0 z-30 border-b border-line bg-bg/80 backdrop-blur-xl" aria-label="Main">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4 sm:px-6">
        <Link to="/" className="mr-2 shrink-0 rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-accent" aria-label="Studio home">
          <Logo />
        </Link>
        <div className="hidden items-center gap-0.5 md:flex">
          {NAV.map((n) => <NavLink key={n.key} item={n} active={section === n.key} />)}
        </div>
        <div className="ml-auto"><QueuePill status={status} offline={offline} /></div>
      </div>
      {/* small screens: a scrollable tab row */}
      <div className="flex gap-1 overflow-x-auto border-t border-line px-2 py-1.5 md:hidden">
        {NAV.map((n) => <NavLink key={n.key} item={n} active={section === n.key} />)}
      </div>
    </nav>
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

/** What the shared worker is doing right now, across both apps. */
function QueuePill({ status, offline }: { status?: Status; offline: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  const cur = status?.queue.current;
  const waiting = status?.queue.queued.length ?? 0;
  const label = offline ? "Server offline" : !status ? "Connecting…" : cur ? `${KIND_LABEL[cur.kind] ?? "Working"}${waiting ? ` · +${waiting}` : ""}` : "Idle";
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="inline-flex max-w-[220px] items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-muted hover:text-fg"
      >
        {cur ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-accent" />
          : <span className={cx("size-1.5 shrink-0 rounded-full", offline ? "bg-err" : status ? "bg-ok" : "bg-faint")} />}
        <span className="truncate">{label}</span>
      </button>
      {open && status && (
        <div className="absolute right-0 z-40 mt-2 w-80 rounded-2xl border border-line bg-surface p-3 shadow-card fade-in">
          <p className="px-1 pb-2 text-xs font-medium text-muted">Worker · one job at a time</p>
          {!cur && !waiting && <p className="px-1 pb-1 text-sm">Nothing running.</p>}
          <ul className="space-y-1">
            {[cur, ...status.queue.queued].filter(Boolean).map((t, i) => (
              <li key={`${t!.app}-${t!.job_id}-${t!.kind}-${i}`}>
                <Link to={jobHref(t!.app, t!.job_id)} onClick={() => setOpen(false)}
                  className="flex items-center gap-2 rounded-lg px-1 py-1.5 text-sm hover:bg-surface-2">
                  {i === 0 && cur ? <LoaderCircle className="size-3.5 shrink-0 animate-spin text-accent" />
                    : <span className="size-1.5 shrink-0 rounded-full bg-faint" />}
                  <span className="min-w-0 flex-1 truncate">{t!.title}</span>
                  <span className="shrink-0 text-xs text-muted capitalize">{t!.app} · {KIND_LABEL[t!.kind] ?? t!.kind}</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function Footer({ status, offline }: { status?: Status; offline: boolean }) {
  return (
    <footer className="mt-auto border-t border-line bg-surface/40">
      <div className="mx-auto grid max-w-6xl gap-8 px-4 py-10 sm:grid-cols-2 sm:px-6 lg:grid-cols-4">
        <div className="sm:col-span-2 lg:col-span-1">
          <Logo />
          <p className="mt-3 max-w-xs text-sm text-muted">
            Vertical shorts from long videos and anime episodes. Everything is processed on this machine.
          </p>
        </div>
        <FooterCol title="Make">
          <FooterLink to="/shorts">Long video → shorts</FooterLink>
          <FooterLink to="/anime">Anime edits</FooterLink>
          <FooterLink to="/anime/music">Music library</FooterLink>
        </FooterCol>
        <FooterCol title="Settings">
          <FooterLink to="/settings/channel">Channel card</FooterLink>
          <a href="/api/docs" target="_blank" rel="noreferrer" className="text-sm text-muted hover:text-fg">API docs</a>
        </FooterCol>
        <FooterCol title="System">
          <Stat ok={!offline} label={offline ? "Server offline" : "Server online"} />
          {status && (
            <>
              <Stat ok={status.gemini} label={status.gemini ? "Gemini key set" : "Gemini key missing (.env)"} />
              <Stat ok={status.disk_free_gb > 3} label={`${status.disk_free_gb} GB disk free`} />
              <Stat ok={status.ram_available_gb > 1} label={`${status.ram_available_gb} GB RAM available`} />
            </>
          )}
        </FooterCol>
      </div>
      <div className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-xs text-faint sm:px-6">
          <span>Upload as private first and check YouTube's copyright result before publishing.</span>
          <span className="font-mono">Studio v{status?.version ?? "–"}</span>
        </div>
      </div>
    </footer>
  );
}

function FooterCol({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="mb-3 text-xs font-semibold tracking-wider text-fg uppercase">{title}</p>
      <div className="flex flex-col gap-2">{children}</div>
    </div>
  );
}

function FooterLink({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="text-sm text-muted hover:text-fg">{children}</Link>;
}

function Stat({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="flex items-center gap-2 text-sm text-muted">
      <span className={cx("size-1.5 rounded-full", ok ? "bg-ok" : "bg-warn")} />
      {label}
    </span>
  );
}
