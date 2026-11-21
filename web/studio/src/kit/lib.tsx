import { useCallback, useEffect, useRef, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

/* ---------- tiny router ---------- */

export function navigate(to: string, { replace = false } = {}) {
  if (to === location.pathname + location.search) return;
  if (replace) history.replaceState(null, "", to);
  else history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  if (!replace) window.scrollTo({ top: 0 });
}

export function usePath() {
  const [path, setPath] = useState(location.pathname);
  useEffect(() => {
    const onPop = () => setPath(location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export function Link({ to, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { to: string }) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };
  return <a href={to} onClick={handle} {...rest} />;
}

/* ---------- polling fetch ---------- */

/**
 * Fetch now, then every `intervalFor(data)` ms (null = stop). Robust by design:
 * - the last good data is kept when a request fails (`error` says why, `stale` that it's old)
 * - failures back off (2x per failure, up to 30 s) so a stopped server isn't hammered
 * - requests never overlap; hidden tabs don't poll, and refresh as soon as they're visible again
 * - a `key` change starts over (data cleared)
 */
export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalFor: (data: T | undefined) => number | null,
  key: string,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const [failing, setFailing] = useState(0);   // failed polls in a row
  const [updatedAt, setUpdatedAt] = useState<number>();
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const inFlight = useRef(false);
  const failures = useRef(0);
  const gen = useRef(0);

  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    const g = gen.current;
    try {
      const d = await fetcherRef.current();
      if (g !== gen.current) return;
      setData(d);
      setError(undefined);
      setUpdatedAt(Date.now());
      failures.current = 0;
      setFailing(0);
    } catch (e) {
      if (g !== gen.current) return;
      failures.current += 1;
      setFailing(failures.current);
      setError(errorText(e));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    gen.current += 1;
    inFlight.current = false;
    failures.current = 0;
    setData(undefined);
    setError(undefined);
    load();
  }, [key, load]);

  const base = intervalFor(data);
  const ms = base == null ? (error ? 4000 : null) : base;
  useEffect(() => {
    if (ms == null) return;
    let t: ReturnType<typeof setTimeout>;
    const tick = () => {
      const wait = failures.current ? Math.min(30000, ms * 2 ** failures.current) : ms;
      t = setTimeout(async () => {
        if (!document.hidden) await load();
        tick();
      }, wait);
    };
    tick();
    return () => clearTimeout(t);
  }, [ms, load]);

  useEffect(() => {
    const onShow = () => !document.hidden && load();
    document.addEventListener("visibilitychange", onShow);
    window.addEventListener("online", onShow);
    return () => {
      document.removeEventListener("visibilitychange", onShow);
      window.removeEventListener("online", onShow);
    };
  }, [load]);

  return { data, error, failing, stale: !!error && data !== undefined, updatedAt, reload: load, setData };
}

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : typeof e === "string" ? e : "Something went wrong";
}

/** Value saved in localStorage (per browser). Falls back to memory when storage is blocked. */
export function useStored<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw == null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = useCallback((nv: T) => {
    setV(nv);
    try {
      localStorage.setItem(key, JSON.stringify(nv));
    } catch {
      /* private mode */
    }
  }, [key]);
  return [v, set];
}

/** Re-render every `ms` (for live elapsed times). */
export function useNow(ms = 1000, active = true) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms, active]);
  return now;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // http:// (not localhost) has no clipboard API: fall back to a hidden textarea
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

/* ---------- formatting ---------- */

export function fmtTime(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "–";
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** 42 -> "42s", 125 -> "2m 05s", 7300 -> "2h 01m" */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) return "–";
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

/** Rough "time left" wording: never more precise than the estimate is. */
export function fmtEta(sec: number | null | undefined): string | null {
  if (sec == null || !isFinite(sec) || sec < 0) return null;
  if (sec < 45) return "under a minute left";
  if (sec < 90) return "about a minute left";
  if (sec < 3600) return `about ${Math.round(sec / 60)} min left`;
  const h = sec / 3600;
  return `about ${h < 10 ? h.toFixed(1).replace(/\.0$/, "") : Math.round(h)} h left`;
}

export function fmtBytes(b: number | null | undefined): string {
  if (b == null || !isFinite(b)) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = b;
  while (v >= 1000 && i < u.length - 1) {
    v /= 1000;
    i++;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

export function timeAgo(iso: string, now = Date.now()): string {
  const t = new Date(iso).getTime();
  if (!isFinite(t)) return "";
  const diff = (now - t) / 1000;
  if (diff < 45) return "just now";
  const units: [number, string][] = [[60, " min"], [3600, "h"], [86400, "d"], [604800, "w"]];
  for (let i = units.length - 1; i >= 0; i--) {
    const [size, label] = units[i];
    if (diff >= size) return `${Math.floor(diff / size)}${label} ago`;
  }
  return "just now";
}

export function fmtDate(iso: string): string {
  const d = new Date(iso);
  return isFinite(d.getTime()) ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

/** Seconds between two ISO times (end = `now`, already corrected for server clock skew). */
export function secondsBetween(start: string | null, end: string | null, now: number): number | null {
  if (!start) return null;
  const a = new Date(start).getTime();
  const b = end ? new Date(end).getTime() : now;
  return isFinite(a) && isFinite(b) ? Math.max(0, (b - a) / 1000) : null;
}

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

export const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
