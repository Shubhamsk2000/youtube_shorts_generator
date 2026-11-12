import type { ButtonHTMLAttributes, ReactNode } from "react";
import { LoaderCircle } from "lucide-react";
import { cx } from "./lib";

export function Button({
  variant = "primary",
  loading,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" | "outline"; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={cx(
        "inline-flex h-9 items-center justify-center gap-2 rounded-xl px-3.5 text-sm font-medium transition",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-fg text-bg hover:opacity-85",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-fg",
        variant === "outline" && "border border-line-strong bg-surface text-fg hover:bg-surface-2",
        variant === "danger" && "bg-err text-white hover:opacity-90",
        className,
      )}
    >
      {loading && <LoaderCircle className="size-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section className={cx("rounded-2xl border border-line bg-surface shadow-card", className)}>{children}</section>
  );
}

export function CardHeader({ title, aside }: { title: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
      <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
      {aside && <div className="text-xs text-muted">{aside}</div>}
    </div>
  );
}

export function ProgressBar({ value, active }: { value: number | null; active?: boolean }) {
  const indeterminate = value == null;
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className={cx(
          "h-full rounded-full bg-accent transition-[width] duration-500 ease-out",
          active && "progress-stripe",
          indeterminate && "w-1/3 animate-pulse",
        )}
        style={indeterminate ? undefined : { width: `${Math.max(2, value * 100)}%` }}
      />
    </div>
  );
}

/** Pill-style choice between a few options. */
export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  disabled,
}: {
  value: T;
  options: { value: T; label: ReactNode; hint?: string }[];
  onChange: (v: T) => void;
  disabled?: boolean;
}) {
  return (
    <div role="radiogroup" className="inline-flex flex-wrap gap-1 rounded-xl border border-line bg-surface-2 p-1">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          title={o.hint}
          disabled={disabled}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:opacity-50",
            "focus-visible:outline-2 focus-visible:outline-accent",
            o.value === value ? "bg-surface text-fg shadow-card" : "text-muted hover:text-fg",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
  disabled?: boolean;
}) {
  return (
    <label className={cx("inline-flex cursor-pointer items-center gap-2.5 text-sm", disabled && "opacity-50")}>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cx(
          "relative h-5 w-9 shrink-0 rounded-full transition focus-visible:outline-2 focus-visible:outline-accent",
          checked ? "bg-accent" : "bg-line-strong",
        )}
      >
        <span
          className={cx(
            "absolute top-0.5 left-0.5 size-4 rounded-full bg-white shadow transition-transform",
            checked && "translate-x-4",
          )}
        />
      </button>
      {label}
    </label>
  );
}

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="grid size-11 place-items-center rounded-2xl bg-surface-2 text-muted">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="max-w-sm text-sm text-muted">{children}</div>}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium tracking-wide text-muted uppercase">{label}</div>
      {children}
      {hint && <p className="mt-2 text-xs leading-relaxed text-muted">{hint}</p>}
    </div>
  );
}

export function Alert({ tone = "err", children }: { tone?: "err" | "warn" | "ok"; children: ReactNode }) {
  return (
    <p
      role={tone === "err" ? "alert" : "status"}
      className={cx(
        "rounded-xl px-3 py-2 text-sm",
        tone === "err" && "bg-err/10 text-err",
        tone === "warn" && "bg-warn/10 text-warn",
        tone === "ok" && "bg-ok/10 text-ok",
      )}
    >
      {children}
    </p>
  );
}

export function PageHeader({ eyebrow, title, children, aside }: {
  eyebrow?: string; title: ReactNode; children?: ReactNode; aside?: ReactNode;
}) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4 fade-in">
      <div className="max-w-2xl">
        {eyebrow && <p className="mb-2 text-xs font-semibold tracking-wider text-accent uppercase">{eyebrow}</p>}
        <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl">{title}</h1>
        {children && <p className="mt-3 text-muted text-pretty">{children}</p>}
      </div>
      {aside}
    </header>
  );
}
