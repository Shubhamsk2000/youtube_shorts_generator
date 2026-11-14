import { useEffect, useState } from "react";
import { Bot } from "lucide-react";
import { studio, type AiModels } from "../../app/api";
import { cx } from "../lib";

const DEFAULT = "";

/** Which AI picks shorts moments. Saved on the server (configs/llm.local.yaml), used by the next pick / re-pick. */
export function ModelPicker({ className }: { className?: string }) {
  const [data, setData] = useState<AiModels | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    studio.aiModels().then(setData, (e) => setError(String(e)));
  }, []);

  if (!data) return error ? <span className="text-err">{error}</span> : null;

  const value = data.picked ? `${data.picked.provider}|${data.picked.model ?? ""}` : DEFAULT;
  const active = data.current.models?.[0];

  async function change(v: string) {
    setSaving(true);
    setError(null);
    try {
      const [provider, model] = v === DEFAULT ? [null, null] : v.split("|");
      setData(await studio.setAiModel(provider, model || null));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const providers = [...new Set(data.choices.map((c) => c.provider))];
  return (
    <label className={cx("inline-flex items-center gap-1.5 text-xs text-muted", className)} title="AI that picks the moments">
      <Bot className="size-3.5 text-faint" />
      <span>AI model</span>
      <select
        value={value}
        disabled={saving}
        onChange={(e) => change(e.target.value)}
        className="max-w-[16rem] truncate rounded-md border border-line bg-transparent px-1.5 py-0.5 text-fg disabled:opacity-50"
      >
        <option value={DEFAULT}>Default (configs/llm.yaml){!data.picked && active ? `: ${active}` : ""}</option>
        {providers.map((p) => (
          <optgroup key={p} label={p}>
            {data.choices
              .filter((c) => c.provider === p)
              .map((c) => (
                <option key={c.model} value={`${c.provider}|${c.model}`} disabled={!c.key_set}>
                  {c.model}
                  {c.key_set ? "" : " (key missing in .env)"}
                </option>
              ))}
          </optgroup>
        ))}
      </select>
      {error && <span className="text-err">{error}</span>}
    </label>
  );
}
