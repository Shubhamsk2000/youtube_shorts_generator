import { useEffect, useRef, useState, type ReactNode } from "react";
import { ImageUp, Trash2 } from "lucide-react";
import { usePoll } from "@kit/lib";
import { Alert, Button, Card, CardHeader, Field, PageHeader, Segmented, Toggle } from "@kit/ui";
import { api, type CardSettings } from "../api";

const SAMPLE = "nobody was ready for what he said next";

export function Channel() {
  const { data, setData } = usePoll(api.settings, () => null, "settings");
  const [form, setForm] = useState<CardSettings>();
  const [text, setText] = useState(SAMPLE);
  const [v, setV] = useState(0);
  const [busy, setBusy] = useState<string>();
  const [msg, setMsg] = useState<{ ok: boolean; text: string }>();
  const file = useRef<HTMLInputElement>(null);

  useEffect(() => { if (data) setForm(data.card); }, [data]);
  if (!form || !data) return <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6"><div className="h-60 rounded-2xl shimmer" /></div>;

  const dirty = (["name", "handle", "theme", "verified", "footer"] as const).some((k) => form[k] !== data.card[k]);

  async function run(name: string, fn: () => Promise<void>) {
    setBusy(name);
    setMsg(undefined);
    try {
      await fn();
      setV((x) => x + 1);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(undefined);
    }
  }

  const save = () => run("save", async () => {
    setData(await api.saveCard({ name: form.name.trim(), handle: form.handle.trim(), theme: form.theme,
      verified: form.verified, footer: form.footer }));
    setMsg({ ok: true, text: "Saved. New previews and exports use this card." });
  });

  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-6">
      <PageHeader eyebrow="Settings" title="Your channel card">
        The tweet above every anime short. Use your own channel's name, handle and picture, never another account's.
      </PageHeader>
      <div className="grid gap-6 lg:grid-cols-12">
        <Card className="lg:col-span-5">
          <CardHeader title="Details" />
          <div className="space-y-5 p-5">
            <Text label="Display name" value={form.name} onChange={(name) => setForm({ ...form, name })} max={40} />
            <Text label="Handle" value={form.handle} onChange={(handle) => setForm({ ...form, handle })} max={30} />
            <Field label="Picture" hint="Square works best; it's cropped to a circle. Without one, your initial is used.">
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" loading={busy === "avatar"} onClick={() => file.current?.click()}>
                  <ImageUp className="size-4" /> {data.card.has_avatar ? "Replace" : "Upload"}
                </Button>
                {data.card.has_avatar && (
                  <Button variant="ghost" loading={busy === "rm"} onClick={() => run("rm", async () => setData(await api.saveCard({ remove_avatar: true })))}>
                    <Trash2 className="size-4" /> Remove
                  </Button>
                )}
                <input ref={file} type="file" accept="image/*" className="hidden" onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) run("avatar", async () => setData(await api.uploadAvatar(f)));
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
            <div className="flex items-center gap-3 pt-1">
              <Button onClick={save} disabled={!dirty || !form.name.trim() || !form.handle.trim()} loading={busy === "save"}>Save</Button>
              {dirty && <Button variant="ghost" onClick={() => setForm(data.card)}>Reset</Button>}
            </div>
            {msg && <Alert tone={msg.ok ? "ok" : "err"}>{msg.text}</Alert>}
          </div>
        </Card>

        <Card className="lg:col-span-7">
          <CardHeader title="Preview" aside={dirty ? "save to update" : undefined} />
          <div className="space-y-4 p-5">
            <div className="grid place-items-center rounded-2xl bg-[radial-gradient(circle_at_30%_20%,#3b3f55,#14151c)] p-6">
              <img src={api.cardPreview(text || SAMPLE, v)} alt="Card preview" className="w-full max-w-md" />
            </div>
            <Text label="Sample hook" value={text} onChange={setText} max={140} onBlur={() => setV((x) => x + 1)} />
          </div>
        </Card>
      </div>
    </div>
  );
}

function Text({ label, value, onChange, max, onBlur }: {
  label: string; value: string; onChange: (v: string) => void; max: number; onBlur?: () => void;
}): ReactNode {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-medium tracking-wide text-muted uppercase">{label}</span>
      <input
        value={value}
        maxLength={max}
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
        className="w-full rounded-xl border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent/60 focus:ring-2 focus:ring-accent/20"
      />
    </label>
  );
}
