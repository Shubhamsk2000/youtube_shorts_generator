import { useEffect, useRef, useState } from "react";
import { Bot, Check, ChevronDown, KeyRound, LoaderCircle } from "lucide-react";
import { studio, type AiChoice, type AiModels } from "../../app/api";
import { cx } from "../lib";

const PROVIDER_LABEL: Record<string, string> = {
  gemini: "Google Gemini",
  nvidia: "NVIDIA",
  openrouter: "OpenRouter",
  groq: "Groq",
  ollama: "Ollama (this computer)",
};

/** "nvidia/nemotron-3.5-super-vl-120b-a12b" -> { name: "nemotron-3.5-super-vl-120b-a12b", vendor: "nvidia" } */
function split(model: string) {
  const i = model.lastIndexOf("/");
  return i < 0 ? { name: model, vendor: "" } : { name: model.slice(i + 1), vendor: model.slice(0, i) };
}

type Item = { key: string; provider: string | null; model: string | null; disabled: boolean };

/** Which AI picks shorts moments. Saved on the server (configs/llm.local.yaml), used by the next pick / re-pick. */
export function ModelPicker({ className, up = false, center = false }: { className?: string; up?: boolean; center?: boolean }) {
  const [data, setData] = useState<AiModels | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hi, setHi] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    studio.aiModels().then(setData, (e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => root.current && !root.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.querySelector<HTMLElement>(`[data-i="${hi}"]`)?.scrollIntoView({ block: "nearest" });
  }, [open, hi]);

  if (!data) return error ? <span className={cx("text-xs text-err", className)}>{error}</span> : null;

  const providers = [...new Set(data.choices.map((c) => c.provider))];
  const items: Item[] = [
    { key: "default", provider: null, model: null, disabled: false },
    ...data.choices.map((c) => ({ key: `${c.provider}|${c.model}`, provider: c.provider, model: c.model, disabled: !c.key_set })),
  ];
  const selectedKey = data.picked ? `${data.picked.provider}|${data.picked.model ?? ""}` : "default";
  const active = data.current.models?.[0] ?? null;
  const shown = active ? split(active).name : "not configured";

  async function choose(it: Item) {
    if (it.disabled) return;
    setOpen(false);
    if (it.key === selectedKey) return;
    setSaving(true);
    setError(null);
    try {
      setData(await studio.setAiModel(it.provider, it.model));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  function openAt() {
    setHi(Math.max(0, items.findIndex((i) => i.key === selectedKey)));
    setOpen(true);
  }

  function move(d: number) {
    let n = hi;
    for (let k = 0; k < items.length; k++) {
      n = (n + d + items.length) % items.length;
      if (!items[n].disabled) break;
    }
    setHi(n);
  }

  function onKey(e: React.KeyboardEvent) {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openAt();
      }
      return;
    }
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") move(1);
    else if (e.key === "ArrowUp") move(-1);
    else if (e.key === "Enter" || e.key === " ") choose(items[hi]);
    else return;
    e.preventDefault();
  }

  const row = (it: Item, i: number, label: React.ReactNode, sub?: React.ReactNode) => (
    <div
      key={it.key}
      data-i={i}
      role="option"
      aria-selected={it.key === selectedKey}
      aria-disabled={it.disabled}
      onMouseEnter={() => !it.disabled && setHi(i)}
      onClick={() => choose(it)}
      className={cx(
        "flex cursor-pointer items-start gap-2 rounded-lg px-2.5 py-1.5",
        it.disabled ? "cursor-not-allowed opacity-45" : hi === i && "bg-surface-2",
      )}
    >
      <Check className={cx("mt-0.5 size-3.5 shrink-0 text-accent", it.key !== selectedKey && "invisible")} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-fg">{label}</div>
        {sub && <div className="truncate text-[11px] text-faint">{sub}</div>}
      </div>
    </div>
  );

  let i = 0;
  return (
    <div ref={root} className={cx("relative inline-flex items-center gap-2 text-xs", className)} onKeyDown={onKey}>
      <span className="inline-flex items-center gap-1.5 text-muted">
        <Bot className="size-3.5 text-faint" /> AI model
      </span>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={saving}
        onClick={() => (open ? setOpen(false) : openAt())}
        className={cx(
          "inline-flex max-w-[15rem] items-center gap-1.5 rounded-lg border bg-surface px-2.5 py-1 text-fg shadow-card transition",
          "hover:border-line-strong focus-visible:border-accent/60 focus-visible:outline-none disabled:opacity-60",
          open ? "border-accent/60" : "border-line",
        )}
        title={active ?? undefined}
      >
        <span className="truncate font-medium">{shown}</span>
        {!data.picked && <span className="shrink-0 rounded bg-surface-2 px-1 text-[10px] text-faint">default</span>}
        {saving ? (
          <LoaderCircle className="size-3.5 shrink-0 animate-spin text-faint" />
        ) : (
          <ChevronDown className={cx("size-3.5 shrink-0 text-faint transition", open && "rotate-180")} />
        )}
      </button>

      {open && (
        <div
          ref={list}
          role="listbox"
          className={cx(
            "absolute z-50 max-h-80 w-[19rem] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-line-strong bg-surface p-1.5 text-left shadow-card",
            up ? "bottom-full mb-2" : "top-full mt-2",
            center ? "left-1/2 -translate-x-1/2" : "right-0",
          )}
        >
          {row(items[i], i++, "Default", <>from configs/llm.yaml{active && !data.picked ? ` · ${split(active).name}` : ""}</>)}
          {providers.map((p) => (
            <div key={p} className="mt-1.5 border-t border-line pt-1.5">
              <div className="px-2.5 pb-1 text-[10px] font-semibold tracking-wider text-faint uppercase">
                {PROVIDER_LABEL[p] ?? p}
              </div>
              {data.choices
                .filter((c) => c.provider === p)
                .map((c: AiChoice) => {
                  const { name, vendor } = split(c.model);
                  const sub = !c.key_set ? (
                    <span className="inline-flex items-center gap-1">
                      <KeyRound className="size-3" /> API key missing in .env
                    </span>
                  ) : vendor && vendor !== p ? (
                    vendor
                  ) : undefined;
                  return row(items[i], i++, name, sub);
                })}
            </div>
          ))}
        </div>
      )}
      {error && <span className="text-err">{error}</span>}
    </div>
  );
}
