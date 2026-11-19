import { useEffect, useState } from "react";
import { Segmented } from "@kit/ui";
import { api, isActive, type Background, type Job, type Layout, type ScreenLayout } from "../api";
import { Button, Card, CardHeader } from "./ui";
import { Link } from "../lib";

type Draft = Pick<Layout, "screen_layout" | "cam_size" | "background" | "smart_layout">;

const LAYOUTS: { value: ScreenLayout; label: string; hint: string }[] = [
  { value: "split", label: "Stacked", hint: "Facecam panel on top, gameplay fills the rest" },
  { value: "pip", label: "Corner cam", hint: "Gameplay fills the frame, facecam in a top corner" },
  { value: "track", label: "Ignore cam", hint: "Follow faces like a normal video" },
];
const CAM_SIZES: { value: string; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "0.2", label: "20%" },
  { value: "0.25", label: "25%" },
  { value: "0.3", label: "30%" },
];
const BACKGROUNDS: { value: Background; label: string }[] = [
  { value: "blur", label: "Blur" },
  { value: "tweet", label: "Tweet card + blur" },
];

/** Diagram of the 9:16 frame for the current choice. */
function Preview({ d }: { d: Draft }) {
  const cam = Math.round((d.cam_size ?? 0.25) * 100);
  const tweet = d.background === "tweet";
  const inner =
    d.screen_layout === "split" ? (
      <div className="flex h-full flex-col">
        <div className="grid place-items-center bg-accent/25 text-[9px] font-medium" style={{ height: `${cam}%` }}>
          CAM {d.cam_size == null ? "auto" : `${cam}%`}
        </div>
        <div className="grid flex-1 place-items-center bg-fg/15 text-[9px] font-medium">GAMEPLAY</div>
      </div>
    ) : d.screen_layout === "pip" ? (
      <div className="relative grid h-full place-items-center bg-fg/15 text-[9px] font-medium">
        GAMEPLAY
        <div className="absolute top-[7%] right-[4%] h-[18%] w-[42%] rounded bg-accent/40" />
      </div>
    ) : (
      <div className="grid h-full place-items-center bg-fg/15 text-[9px] font-medium">FACE CROP</div>
    );
  return (
    <div className="relative aspect-[9/16] w-24 shrink-0 overflow-hidden rounded-lg border border-line-strong bg-surface-2">
      {tweet ? (
        <div className="absolute inset-x-[4%] top-[9%] bottom-[20%] flex flex-col rounded-md bg-surface p-[5%] shadow-card">
          <div className="mb-[6%] flex items-center gap-1">
            <div className="size-2 rounded-full bg-accent" />
            <div className="h-1 w-8 rounded bg-fg/30" />
          </div>
          <div className="mb-[6%] h-1 w-full rounded bg-fg/20" />
          <div className="flex-1 overflow-hidden rounded">{inner}</div>
        </div>
      ) : (
        inner
      )}
    </div>
  );
}

export function LayoutPanel({ job, onJob }: { job: Job; onJob: (j: Job) => void }) {
  const [saved, setSaved] = useState<Layout | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.layout(job.id).then((l) => {
      setSaved(l);
      setDraft({ screen_layout: l.screen_layout, cam_size: l.cam_size, background: l.background, smart_layout: l.smart_layout });
    }, (e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [job.id]);

  if (!draft || !saved) return null;
  const dirty =
    draft.screen_layout !== saved.screen_layout ||
    draft.cam_size !== saved.cam_size ||
    draft.background !== saved.background ||
    draft.smart_layout !== saved.smart_layout;
  const running = isActive(job.status);
  const set = (p: Partial<Draft>) => setDraft({ ...draft, ...p });

  async function apply() {
    setBusy(true);
    setErr(null);
    try {
      setSaved(await api.setLayout(job.id, draft!));
      onJob(await api.job(job.id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Layout" aside={saved.custom ? "This video" : "Defaults from shorts.yaml"} />
      <div className="space-y-4 p-5">
        <div className="flex justify-center">
          <Preview d={draft} />
        </div>
        <div className="space-y-4 text-sm">
          <Row label="Facecam + gameplay" hint={LAYOUTS.find((l) => l.value === draft.screen_layout)?.hint}>
            <Segmented value={draft.screen_layout} options={LAYOUTS} onChange={(v) => set({ screen_layout: v })} />
          </Row>
          {draft.screen_layout === "split" && (
            <Row
              label="Cam size"
              hint={
                draft.cam_size == null
                  ? `Sized to the cam's shape, ${Math.round(saved.cam_range[0] * 100)}-${Math.round(saved.cam_range[1] * 100)}% of the height`
                  : "Fraction of the height; gameplay gets the rest"
              }
            >
              <Segmented
                value={draft.cam_size == null ? "auto" : String(draft.cam_size)}
                options={CAM_SIZES}
                onChange={(v) => set({ cam_size: v === "auto" ? null : Number(v) })}
              />
            </Row>
          )}
          <Row
            label="Background"
            hint={
              draft.background === "tweet" ? (
                <>
                  Video inside your <Link to="/settings/channel" className="text-accent hover:underline">Channel card</Link>,
                  hook text as the tweet
                </>
              ) : (
                "Blurred copy of the video wherever it doesn't fill the frame"
              )
            }
          >
            <Segmented value={draft.background} options={BACKGROUNDS} onChange={(v) => set({ background: v })} />
          </Row>
          <label className="flex cursor-pointer items-start gap-2.5">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-[var(--accent)]"
              checked={draft.smart_layout}
              onChange={(e) => set({ smart_layout: e.target.checked })}
            />
            <span>
              <span className="font-medium">Smart layout</span>
              <span className="block text-xs text-muted">
                Picks the framing per shot: gameplay-only moments are shown whole instead of cropped, and the cam
                layout doesn't flicker when the cam drops out for a moment.
              </span>
            </span>
          </label>
          {err && <p className="text-xs text-err">{err}</p>}
          <div className="flex items-center gap-3">
            <Button onClick={apply} loading={busy} disabled={!dirty || running}>
              Apply &amp; re-render
            </Button>
            {running && <span className="text-xs text-muted">Available when processing finishes</span>}
          </div>
        </div>
      </div>
    </Card>
  );
}

function Row({ label, hint, children }: { label: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 font-medium">{label}</div>
      {children}
      {hint && <p className="mt-1.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}
