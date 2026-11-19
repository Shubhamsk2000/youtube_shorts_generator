import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ImageUp, RotateCcw, Trash2 } from "lucide-react";
import { useConfirm, useToast } from "@kit/feedback";
import { cx, errorText, usePoll } from "@kit/lib";
import { Alert, Button, Card, CardHeader, Field, Input, PageHeader, Segmented, Select, Toggle } from "@kit/ui";
import { api, type CardSettings, type SettingField, type SettingsData } from "../shorts/api";

type Tab = "defaults" | "card";

export function SettingsPage() {
  const [tab, setTabState] = useState<Tab>(() => (new URLSearchParams(location.search).get("tab") === "card" ? "card" : "defaults"));
  useEffect(() => {  // back/forward between /settings and /settings?tab=card
    const onPop = () => setTabState(new URLSearchParams(location.search).get("tab") === "card" ? "card" : "defaults");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const setTab = (t: Tab) => {
    setTabState(t);
    history.replaceState(null, "", t === "card" ? "/settings?tab=card" : "/settings");
  };
  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-6">
      <PageHeader eyebrow="Settings" title={tab === "card" ? "Channel card" : "How shorts are made"}>
        {tab === "card"
          ? "The tweet-style card around the video (Layout → Background → Tweet card). Use your own channel's name, handle and picture."
          : "Defaults for every video. Saved on this Studio in configs/shorts.local.yaml, on top of configs/shorts.yaml."}
      </PageHeader>
      <div className="mb-6">
        <Segmented<Tab>
          value={tab}
          onChange={setTab}
          options={[{ value: "defaults", label: "Defaults" }, { value: "card", label: "Channel card" }]}
        />
      </div>
      {tab === "card" ? <ChannelCard /> : <Defaults />}
    </div>
  );
}

/* ---------------------------------------------------------------- defaults */

const APPLIES: Record<SettingField["applies"], string> = {
  new: "New videos & re-pick",
  render: "Next render",
  export: "Next HD export",
};

function Defaults() {
  const { data, error, setData, reload } = usePoll(api.settings, () => null, "settings");
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();
  const confirm = useConfirm();

  const fields = useMemo(() => new Map((data?.groups ?? []).flatMap((g) => g.fields.map((f) => [f.key, f] as const))), [data]);
  const dirty = Object.keys(draft).filter((k) => fields.has(k) && draft[k] !== fields.get(k)!.value);

  useEffect(() => {  // leaving with unsaved changes
    if (!dirty.length) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty.length]);

  if (error && !data) return <Alert>Couldn't load settings: {error} <button className="ml-2 underline" onClick={reload}>Retry</button></Alert>;
  if (!data) return <div className="grid gap-5 lg:grid-cols-2">{[0, 1, 2, 3].map((i) => <div key={i} className="shimmer h-64 rounded-2xl" />)}</div>;

  async function save(changes: Record<string, unknown>, okText: string) {
    setSaving(true);
    setErr(null);
    try {
      const next: SettingsData = await api.saveSettings(changes);
      setData(next);
      setDraft({});
      toast.ok(okText);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  const customCount = [...fields.values()].filter((f) => f.custom).length;

  return (
    <>
      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        {data.groups.map((g) => (
          <Card key={g.id}>
            <CardHeader title={g.label} />
            <div className="divide-y divide-line">
              <p className="px-5 py-2.5 text-xs text-muted">{g.help}</p>
              {g.fields.map((f) => (
                <SettingRow key={f.key} f={f} value={f.key in draft ? draft[f.key] : f.value}
                  changed={f.key in draft && draft[f.key] !== f.value}
                  onChange={(v) => setDraft((d) => ({ ...d, [f.key]: v }))}
                  onReset={() => (f.custom ? save({ [f.key]: null }, `“${f.label}” back to the default`) : setDraft(({ [f.key]: _, ...d }) => d))} />
              ))}
            </div>
          </Card>
        ))}
      </div>

      <div className={cx("sticky bottom-4 z-20 mt-6 flex flex-wrap items-center gap-3 rounded-2xl border bg-surface/95 px-4 py-3 shadow-card backdrop-blur transition",
        dirty.length ? "border-accent/50" : "border-line")}>
        <span className={cx("mr-auto text-sm", err ? "text-err" : "text-muted")}>
          {err ?? (dirty.length ? `${dirty.length} unsaved change${dirty.length === 1 ? "" : "s"}` : customCount ? `${customCount} setting${customCount === 1 ? "" : "s"} differ from shorts.yaml` : "Using the shorts.yaml defaults")}
        </span>
        {customCount > 0 && !dirty.length && (
          <Button variant="ghost" disabled={saving} onClick={async () => {
            const ok = await confirm({ title: "Reset every setting?", body: "All values go back to configs/shorts.yaml. Your channel card is kept.", confirm: "Reset all", danger: true });
            if (ok) save(Object.fromEntries([...fields.values()].filter((f) => f.custom).map((f) => [f.key, null])), "All settings reset");
          }}>
            <RotateCcw className="size-4" /> Reset all
          </Button>
        )}
        {dirty.length > 0 && <Button variant="ghost" onClick={() => { setDraft({}); setErr(null); }} disabled={saving}>Discard</Button>}
        <Button onClick={() => save(Object.fromEntries(dirty.map((k) => [k, draft[k]])), "Settings saved")} disabled={!dirty.length} loading={saving}>
          Save
        </Button>
      </div>
    </>
  );
}

function SettingRow({ f, value, changed, onChange, onReset }: {
  f: SettingField; value: unknown; changed: boolean; onChange: (v: unknown) => void; onReset: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3">
      <div className="min-w-0 flex-1 basis-48">
        <div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">
          {f.label}
          {(f.custom || changed) && <span className={cx("size-1.5 rounded-full", changed ? "bg-accent" : "bg-warn")} title={changed ? "Unsaved" : "Changed from shorts.yaml"} />}
        </div>
        {f.help && <p className="mt-0.5 text-xs leading-relaxed text-muted">{f.help}</p>}
        <p className="mt-0.5 text-[11px] text-faint">
          Applies to: {APPLIES[f.applies]}
          {(f.custom || changed) && <> · default {fmt(f, f.default)} · <button className="underline hover:text-fg" onClick={onReset}>{changed && !f.custom ? "undo" : "reset"}</button></>}
        </p>
      </div>
      <div className="shrink-0"><Control f={f} value={value} onChange={onChange} /></div>
    </div>
  );
}

const scale = (f: SettingField) => (f.unit === "%" ? 100 : 1);

function fmt(f: SettingField, v: unknown): string {
  if (f.type === "bool") return v ? "on" : "off";
  if (f.type === "choice") return f.options.find((o) => o.value === String(v))?.label ?? String(v);
  if (typeof v === "number") return `${+(v * scale(f)).toFixed(2)}${f.unit ? ` ${f.unit}` : ""}`;
  return String(v);
}

function Control({ f, value, onChange }: { f: SettingField; value: unknown; onChange: (v: unknown) => void }) {
  if (f.type === "bool") return <Toggle checked={!!value} onChange={onChange} label={<span className="sr-only">{f.label}</span>} />;
  if (f.type === "choice") {
    const opts = f.options.some((o) => o.value === String(value)) ? f.options : [...f.options, { value: String(value), label: `${value} (custom)` }];
    return opts.length <= 3 ? (
      <Segmented value={String(value)} options={opts} onChange={onChange} />
    ) : (
      <Select value={String(value)} onChange={(e) => onChange(e.target.value)} aria-label={f.label} className="w-40">
        {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
    );
  }
  if (f.type === "color") return <ColorInput value={String(value ?? "")} onChange={onChange} label={f.label} />;
  return <NumberInput f={f} value={Number(value)} onChange={onChange} />;
}

function NumberInput({ f, value, onChange }: { f: SettingField; value: number; onChange: (v: number) => void }) {
  const k = scale(f);
  const [text, setText] = useState(String(+(value * k).toFixed(4)));
  useEffect(() => setText(String(+(value * k).toFixed(4))), [value, k]);
  const n = Number(text);
  const lo = f.min != null ? f.min * k : -Infinity;
  const hi = f.max != null ? f.max * k : Infinity;
  const bad = text.trim() === "" || !isFinite(n) || n < lo || n > hi;
  return (
    <div className="flex items-center gap-2">
      {f.min != null && f.max != null && (
        <input type="range" min={lo} max={hi} step={(f.step ?? 1) * k} value={isFinite(n) ? n : 0} aria-label={f.label}
          onChange={(e) => { setText(e.target.value); onChange(Number(e.target.value) / k); }}
          className="hidden w-28 accent-[var(--accent)] sm:block" />
      )}
      <div className="relative">
        <Input type="number" inputMode="decimal" value={text} min={lo} max={hi} step={(f.step ?? 1) * k} aria-invalid={bad} aria-label={f.label}
          title={bad ? `Between ${lo} and ${hi}` : undefined}
          onChange={(e) => {
            setText(e.target.value);
            const v = Number(e.target.value);
            if (e.target.value.trim() !== "" && isFinite(v) && v >= lo && v <= hi) onChange(v / k);
          }}
          className={cx("w-24 font-mono", f.unit && "pr-10")} />
        {f.unit && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-xs text-faint">{f.unit}</span>}
      </div>
    </div>
  );
}

function ColorInput({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const valid = /^#[0-9a-f]{6}$/i.test(text);
  return (
    <div className="flex items-center gap-2">
      <input type="color" value={valid ? text : "#000000"} aria-label={`${label} picker`}
        onChange={(e) => { setText(e.target.value.toUpperCase()); onChange(e.target.value.toUpperCase()); }}
        className="size-9 cursor-pointer rounded-lg border border-line bg-bg p-1" />
      <Input value={text} aria-label={label} aria-invalid={!valid} maxLength={7} className="w-24 font-mono uppercase"
        onChange={(e) => {
          const v = e.target.value.startsWith("#") ? e.target.value : `#${e.target.value}`;
          setText(v);
          if (/^#[0-9a-f]{6}$/i.test(v)) onChange(v.toUpperCase());
        }} />
    </div>
  );
}

/* ---------------------------------------------------------------- channel card */

const SAMPLE = "nobody was ready for what he said next";

function ChannelCard() {
  const { data, setData, error, reload } = usePoll(api.card, () => null, "card");
  const [form, setForm] = useState<CardSettings>();
  const [text, setText] = useState(SAMPLE);
  const [v, setV] = useState(0);
  const [busy, setBusy] = useState<string>();
  const [imgErr, setImgErr] = useState(false);
  const toast = useToast();
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => { if (data) setForm(data); }, [data]);
  if (error && !data) return <Alert>Couldn't load the card: {error} <button className="ml-2 underline" onClick={reload}>Retry</button></Alert>;
  if (!form || !data) return <div className="shimmer h-80 rounded-2xl" />;

  const dirty = (["name", "handle", "theme", "verified", "footer"] as const).some((k) => form[k] !== data[k]);
  const handle = form.handle.trim().replace(/^@/, "");
  const invalid = !form.name.trim() ? "The display name can't be empty"
    : form.name.length > 50 ? "Display name: max 50 characters"
    : !/^[\w.-]{1,30}$/.test(handle) ? "Handle: letters, numbers, dots, dashes or underscores (max 30)"
    : null;

  async function run(name: string, fn: () => Promise<CardSettings>, ok?: string) {
    setBusy(name);
    try {
      setData(await fn());
      setV((x) => x + 1);
      setImgErr(false);
      if (ok) toast.ok(ok);
    } catch (e) {
      toast.err("Not saved", errorText(e));
    } finally {
      setBusy(undefined);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      <Card className="lg:col-span-5">
        <CardHeader title="Details" />
        <div className="space-y-5 p-5">
          <Text label="Display name" value={form.name} onChange={(name) => setForm({ ...form, name })} max={50} />
          <Text label="Handle" value={form.handle} onChange={(h) => setForm({ ...form, handle: h })} max={31} prefix="@" />
          <Field label="Picture" hint="Square works best; it's cropped to a circle. Without one, your initial is used.">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" loading={busy === "avatar"} onClick={() => file.current?.click()}>
                <ImageUp className="size-4" /> {data.has_avatar ? "Replace" : "Upload"}
              </Button>
              {data.has_avatar && (
                <Button variant="ghost" loading={busy === "rm"} onClick={() => run("rm", () => api.saveCard({ remove_avatar: true }), "Picture removed")}>
                  <Trash2 className="size-4" /> Remove
                </Button>
              )}
              <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                if (f.size > 15e6) return toast.err("Image too large", "Max 15 MB");
                run("avatar", () => api.uploadAvatar(f), "Picture updated");
              }} />
            </div>
          </Field>
          <Field label="Theme">
            <Segmented value={form.theme} onChange={(theme) => setForm({ ...form, theme })}
              options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} />
          </Field>
          <div className="flex flex-col gap-3">
            <Toggle checked={form.verified} onChange={(verified) => setForm({ ...form, verified })} label="Verified badge" />
            <Toggle checked={form.footer === "date"} onChange={(d) => setForm({ ...form, footer: d ? "date" : "none" })} label="Time & date under the video" />
          </div>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button onClick={() => run("save", () => api.saveCard({ name: form.name.trim(), handle: handle, theme: form.theme, verified: form.verified, footer: form.footer }),
              "Saved: new previews and exports use this card")}
              disabled={!dirty || !!invalid} loading={busy === "save"}>Save</Button>
            {dirty && <Button variant="ghost" onClick={() => setForm(data)}>Discard</Button>}
            {invalid && dirty && <span className="text-xs text-err">{invalid}</span>}
          </div>
        </div>
      </Card>

      <Card className="lg:col-span-7">
        <CardHeader title="Preview" aside={dirty ? "save to update" : undefined} />
        <div className="space-y-4 p-5">
          <div className="grid min-h-48 place-items-center rounded-2xl bg-[radial-gradient(circle_at_30%_20%,#3b3f55,#14151c)] p-6">
            {imgErr ? (
              <p className="text-sm text-white/70">The preview couldn't be drawn. Check that the fonts are installed (System).</p>
            ) : (
              <img src={api.cardPreview(text || SAMPLE, v)} alt="Card preview" className="w-full max-w-md" onError={() => setImgErr(true)} />
            )}
          </div>
          <Text label="Sample hook" value={text} onChange={setText} max={140} onBlur={() => setV((x) => x + 1)} />
        </div>
      </Card>
    </div>
  );
}

function Text({ label, value, onChange, max, onBlur, prefix }: {
  label: string; value: string; onChange: (v: string) => void; max: number; onBlur?: () => void; prefix?: string;
}): ReactNode {
  return (
    <label className="block">
      <span className="mb-2 flex justify-between text-xs font-medium tracking-wide text-muted uppercase">
        {label}<span className="font-mono normal-case text-faint">{value.length}/{max}</span>
      </span>
      <div className="relative">
        {prefix && <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-sm text-faint">{prefix}</span>}
        <Input value={prefix ? value.replace(/^@/, "") : value} maxLength={max} onBlur={onBlur}
          onChange={(e) => onChange(prefix ? `${prefix}${e.target.value.replace(/^@/, "")}` : e.target.value)}
          className={prefix ? "pl-7" : undefined} />
      </div>
    </label>
  );
}
