import { useCallback, useEffect, useRef, useState, type AnchorHTMLAttributes, type MouseEvent } from "react";

/* ---------- tiny router (two routes don't need a library) ---------- */

export function navigate(to: string) {
  if (to === location.pathname) return;
  history.pushState(null, "", to);
  window.dispatchEvent(new PopStateEvent("popstate"));
  window.scrollTo({ top: 0 });
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

export function usePoll<T>(
  fetcher: () => Promise<T>,
  intervalFor: (data: T | undefined) => number | null,
  key: string,
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string>();
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async () => {
    try {
      setData(await fetcherRef.current());
      setError(undefined);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setData(undefined);
    setError(undefined);
    load();
  }, [key, load]);

  const ms = intervalFor(data);
  useEffect(() => {
    if (ms == null) return;
    const t = setInterval(() => {
      if (!document.hidden) load();
    }, ms);
    return () => clearInterval(t);
  }, [ms, load]);

  return { data, error, reload: load, setData };
}

/* ---------- formatting ---------- */

export function fmtTime(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec)) return "–";
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function timeAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 45) return "just now";
  const units: [number, string][] = [[60, "min"], [3600, "h"], [86400, "d"], [604800, "w"]];
  for (let i = units.length - 1; i >= 0; i--) {
    const [size, label] = units[i];
    if (diff >= size) return `${Math.floor(diff / size)}${label === "min" ? " min" : label} ago`;
  }
  return "just now";
}

export function elapsed(start: string | null, end: string | null): string | null {
  if (!start) return null;
  const s = (new Date(end ?? Date.now()).getTime() - new Date(start).getTime()) / 1000;
  if (s < 1) return null;
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");
