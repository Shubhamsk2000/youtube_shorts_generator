import { useEffect, useRef, useState } from "react";
import { Check, CircleAlert, X } from "lucide-react";
import type { SystemInfo } from "../api";
import { cx } from "../lib";

export function SystemStatus({ system, error }: { system?: SystemInfo; error?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const state = error ? "offline" : !system ? "loading" : system.ready ? "ready" : "setup";
  const label = { offline: "Server offline", loading: "Checking…", ready: "Ready", setup: "Setup needed" }[state];
  const dot = { offline: "bg-err", loading: "bg-faint", ready: "bg-ok", setup: "bg-warn" }[state];

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex h-8 items-center gap-2 rounded-full border border-line bg-surface px-3 text-xs font-medium text-muted transition hover:text-fg"
        aria-expanded={open}
      >
        <span className="relative flex size-2">
          {state === "ready" && <span className="absolute inset-0 animate-ping rounded-full bg-ok opacity-40" />}
          <span className={cx("relative size-2 rounded-full", dot)} />
        </span>
        {label}
      </button>

      {open && (
        <div className="fade-in absolute right-0 z-20 mt-2 w-80 max-w-[calc(100vw-2rem)] rounded-2xl border border-line bg-surface p-2 shadow-card">
          {error && <p className="p-3 text-sm text-err">{error}</p>}
          {system && (
            <>
              <div className="grid grid-cols-2 gap-2 p-1">
                <Stat label="Free disk" value={`${system.disk_free_gb} GB`} warn={system.disk_free_gb < 6} />
                <Stat label="Free RAM" value={`${system.ram_available_gb} GB`} warn={system.ram_available_gb < 1.5} />
              </div>
              <ul className="mt-1">
                {system.checks
                  .filter((c) => !["Free disk", "Available RAM"].includes(c.name))
                  .map((c) => (
                    <li key={c.name} className="flex items-start gap-2.5 rounded-lg px-2.5 py-2 text-sm">
                      <span className="mt-0.5">
                        {c.ok ? (
                          <Check className="size-4 text-ok" />
                        ) : c.required ? (
                          <X className="size-4 text-err" />
                        ) : (
                          <CircleAlert className="size-4 text-warn" />
                        )}
                      </span>
                      <span className="min-w-0">
                        <span className="font-medium">{c.name}</span>
                        {!c.ok && c.detail && (
                          <span className="mt-0.5 block font-mono text-xs break-words text-muted">{c.detail}</span>
                        )}
                      </span>
                    </li>
                  ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="rounded-xl bg-surface-2 px-3 py-2.5">
      <div className="text-xs text-muted">{label}</div>
      <div className={cx("mt-0.5 font-mono text-sm font-medium", warn && "text-warn")}>{value}</div>
    </div>
  );
}
