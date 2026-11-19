import {
  Component,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ErrorInfo,
  type ReactNode,
} from "react";
import { CircleAlert, CircleCheck, Info, RotateCcw, X } from "lucide-react";
import { cx } from "./lib";
import { Button } from "./ui";

/* ---------- toasts ---------- */

type Tone = "ok" | "err" | "info";
interface Toast {
  id: number;
  tone: Tone;
  text: ReactNode;
  detail?: string;
}
interface ToastApi {
  ok: (text: ReactNode, detail?: string) => void;
  err: (text: ReactNode, detail?: string) => void;
  info: (text: ReactNode, detail?: string) => void;
}

const ToastCtx = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setItems((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback((tone: Tone, text: ReactNode, detail?: string) => {
    const id = next.current++;
    setItems((t) => [...t.slice(-3), { id, tone, text, detail }]);
    setTimeout(() => dismiss(id), tone === "err" ? 9000 : 4500);
  }, [dismiss]);
  const api = useRef<ToastApi>({
    ok: (t, d) => push("ok", t, d),
    err: (t, d) => push("err", t, d),
    info: (t, d) => push("info", t, d),
  });
  return (
    <ToastCtx.Provider value={api.current}>
      {children}
      <div
        aria-live="polite"
        className="pointer-events-none fixed inset-x-0 bottom-4 z-[60] flex flex-col items-center gap-2 px-4 sm:right-4 sm:left-auto sm:items-end"
      >
        {items.map((t) => (
          <div
            key={t.id}
            role={t.tone === "err" ? "alert" : "status"}
            className="fade-in pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-xl border border-line-strong bg-surface p-3 text-sm shadow-card"
          >
            {t.tone === "ok" ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-ok" />
            ) : t.tone === "err" ? (
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-err" />
            ) : (
              <Info className="mt-0.5 size-4 shrink-0 text-accent" />
            )}
            <div className="min-w-0 flex-1">
              <div className="font-medium">{t.text}</div>
              {t.detail && <div className="mt-0.5 text-xs break-words text-muted">{t.detail}</div>}
            </div>
            <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="rounded p-0.5 text-faint hover:text-fg">
              <X className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast(): ToastApi {
  const t = useContext(ToastCtx);
  if (!t) throw new Error("useToast outside ToastProvider");
  return t;
}

/* ---------- modal ---------- */

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    requestAnimationFrame(() => ref.current?.querySelector<HTMLElement>("[autofocus], input, textarea, button")?.focus());
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      prev?.focus?.();
    };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-0 backdrop-blur-sm sm:items-center sm:p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        className={cx(
          "fade-in flex max-h-[92dvh] w-full flex-col rounded-t-2xl border border-line-strong bg-surface shadow-card sm:rounded-2xl",
          wide ? "sm:max-w-2xl" : "sm:max-w-md",
        )}
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1 text-muted hover:bg-surface-2 hover:text-fg">
            <X className="size-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

/* ---------- confirm ---------- */

interface ConfirmOpts {
  title: string;
  body?: ReactNode;
  confirm?: string;
  danger?: boolean;
  /** optional checkbox, its value is passed to the result */
  check?: string;
}
type ConfirmFn = (o: ConfirmOpts) => Promise<false | { checked: boolean }>;
const ConfirmCtx = createContext<ConfirmFn | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<(ConfirmOpts & { resolve: (v: false | { checked: boolean }) => void }) | null>(null);
  const [checked, setChecked] = useState(false);
  const ask = useCallback<ConfirmFn>((o) => new Promise((resolve) => {
    setChecked(false);
    setState({ ...o, resolve });
  }), []);
  const close = (v: false | { checked: boolean }) => {
    state?.resolve(v);
    setState(null);
  };
  return (
    <ConfirmCtx.Provider value={ask}>
      {children}
      <Modal
        open={!!state}
        onClose={() => close(false)}
        title={state?.title}
        footer={
          <>
            <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
            <Button variant={state?.danger ? "danger" : "primary"} onClick={() => close({ checked })} autoFocus>
              {state?.confirm ?? "Continue"}
            </Button>
          </>
        }
      >
        {state?.body && <div className="text-sm text-muted">{state.body}</div>}
        {state?.check && (
          <label className="mt-4 flex cursor-pointer items-center gap-2 text-sm">
            <input type="checkbox" className="size-4 accent-[var(--accent)]" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
            {state.check}
          </label>
        )}
      </Modal>
    </ConfirmCtx.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  const c = useContext(ConfirmCtx);
  if (!c) throw new Error("useConfirm outside ConfirmProvider");
  return c;
}

/* ---------- error boundary ---------- */

export class ErrorBoundary extends Component<
  { children: ReactNode; label?: string; resetKey?: string },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`[${this.props.label ?? "ui"}]`, error, info.componentStack);
  }
  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-4 rounded-2xl border border-err/30 bg-err/5 p-5 text-sm" role="alert">
        <p className="flex items-center gap-2 font-medium text-err">
          <CircleAlert className="size-4" /> {this.props.label ? `${this.props.label} couldn't be shown` : "This part of the page crashed"}
        </p>
        <p className="mt-1.5 font-mono text-xs break-words text-muted">{this.state.error.message}</p>
        <Button variant="outline" className="mt-3" onClick={() => this.setState({ error: null })}>
          <RotateCcw className="size-4" /> Try again
        </Button>
      </div>
    );
  }
}
