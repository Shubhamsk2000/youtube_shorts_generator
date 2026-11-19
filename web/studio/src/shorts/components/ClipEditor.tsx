import { useEffect, useState, type ReactNode } from "react";
import { Copy } from "lucide-react";
import { Modal, useToast } from "@kit/feedback";
import { Button, Input, TextArea, Toggle } from "@kit/ui";
import { api, type Clip, type ClipEdit } from "../api";
import { copyText, cx, errorText, fmtTime } from "../lib";

const LIMITS = { title: 100, hook_text: 120, description: 4500 };
/** YouTube cuts titles in the Shorts feed around here. */
const TITLE_VISIBLE = 60;

type Draft = { title: string; hook_text: string; show_hook: boolean; description: string; hashtags: string };

const toDraft = (c: Clip): Draft => ({
  title: c.title,
  hook_text: c.hook_text,
  show_hook: c.show_hook !== false,
  description: c.description,
  hashtags: c.hashtags.join(" "),
});

const parseTags = (s: string) =>
  [...new Set(s.split(/[\s,]+/).map((t) => t.replace(/^#+/, "").replace(/[^\p{L}\p{N}_]/gu, "")).filter(Boolean).map((t) => `#${t}`))];

export function ClipEditor({ jobId, clip, busy, onClose, onSaved }: {
  jobId: string;
  clip: Clip | null;
  busy: boolean;           // the project is processing: on-screen text can't change right now
  onClose: () => void;
  onSaved: (c: Clip, rerender: boolean) => void;
}) {
  const [d, setD] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    setD(clip ? toDraft(clip) : null);
    setErr(null);
  }, [clip]);

  if (!clip || !d) return <Modal open={false} onClose={onClose} title="">{null}</Modal>;
  const base = toDraft(clip);
  const tags = parseTags(d.hashtags);
  const changes: ClipEdit = {};
  if (d.title.trim() !== base.title) changes.title = d.title.trim();
  if (d.hook_text.trim() !== base.hook_text) changes.hook_text = d.hook_text.trim();
  if (d.show_hook !== base.show_hook) changes.show_hook = d.show_hook;
  if (d.description.trim() !== base.description.trim()) changes.description = d.description.trim();
  if (tags.join(" ") !== clip.hashtags.join(" ")) changes.hashtags = tags;
  const dirty = Object.keys(changes).length > 0;
  const visual = "hook_text" in changes || "show_hook" in changes || ("title" in changes && !d.hook_text.trim());
  const invalid = !d.title.trim()
    ? "The title can't be empty"
    : d.title.length > LIMITS.title ? `Title: max ${LIMITS.title} characters`
    : d.hook_text.length > LIMITS.hook_text ? `Hook: max ${LIMITS.hook_text} characters`
    : d.description.length > LIMITS.description ? "Description is too long"
    : visual && busy ? "The project is processing: on-screen text can be changed when it's done"
    : null;

  async function save() {
    if (!dirty || invalid) return;
    setSaving(true);
    setErr(null);
    try {
      const r = await api.editClip(jobId, clip!.id, changes);
      onSaved({ ...clip!, ...r.clip, review: clip!.review, ready: r.rerender ? false : clip!.ready }, r.rerender);
      toast.ok(r.rerender ? "Saved · re-rendering the preview" : "Saved");
      onClose();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  const copy = async (label: string, text: string) => {
    if (await copyText(text)) toast.ok(`${label} copied`);
    else toast.err("Copy failed", "Select the text and copy it by hand");
  };

  return (
    <Modal
      open
      wide
      onClose={() => !saving && onClose()}
      title={<span>Edit clip <span className="font-mono text-xs font-normal text-muted">#{clip.rank} · {fmtTime(clip.start)} · {Math.round(clip.duration)}s</span></span>}
      footer={
        <>
          <span className={cx("mr-auto text-xs", invalid || err ? "text-err" : "text-muted")}>
            {err ?? invalid ?? (visual ? "Changes the video: the preview is re-rendered (HD export again after)" : dirty ? "Text only: no re-render needed" : "")}
          </span>
          <Button variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button onClick={save} disabled={!dirty || !!invalid} loading={saving}>Save</Button>
        </>
      }
    >
      <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); save(); }}
        onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") save(); }}>
        <Row label="Title" count={d.title.length} max={LIMITS.title} onCopy={() => copy("Title", d.title.trim())}
          hint={d.title.length > TITLE_VISIBLE ? `The feed usually shows about the first ${TITLE_VISIBLE} characters.` : undefined}>
          <Input value={d.title} maxLength={LIMITS.title + 20} onChange={(e) => setD({ ...d, title: e.target.value })} autoFocus
            aria-invalid={!d.title.trim() || d.title.length > LIMITS.title} />
        </Row>
        <Row label="On-screen hook" count={d.hook_text.length} max={LIMITS.hook_text}
          hint={d.hook_text.trim() ? "Shown at the top for the first seconds (or as the tweet with the tweet-card background)." : "Empty: the title is used."}>
          <Input value={d.hook_text} maxLength={LIMITS.hook_text + 20} onChange={(e) => setD({ ...d, hook_text: e.target.value })}
            placeholder={d.title} aria-invalid={d.hook_text.length > LIMITS.hook_text} />
          <div className="mt-2.5">
            <Toggle checked={d.show_hook} onChange={(show_hook) => setD({ ...d, show_hook })}
              label={<span className="text-sm">Show the hook banner <span className="text-muted">(off when the first spoken line already says it)</span></span>} />
          </div>
        </Row>
        <Row label="Description" count={d.description.length} max={LIMITS.description} onCopy={() => copy("Description", d.description.trim())}>
          <TextArea rows={4} value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} />
        </Row>
        <Row label="Hashtags" count={tags.length} max={15} onCopy={() => copy("Hashtags", tags.join(" "))}
          hint="Space or comma separated. YouTube shows the first 3 above the title; more than 15 and it ignores them all.">
          <Input value={d.hashtags} onChange={(e) => setD({ ...d, hashtags: e.target.value })} placeholder="#shorts #gaming" />
          {tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {tags.map((t, i) => (
                <span key={t} className={cx("rounded-md px-1.5 py-0.5 text-[11px] font-medium", i < 3 ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted", i >= 15 && "line-through")}>{t}</span>
              ))}
            </div>
          )}
        </Row>
        <div className="rounded-xl border border-line bg-surface-2 p-3 text-xs leading-relaxed text-muted">
          <p className="font-medium text-fg">Why the AI picked it</p>
          <p className="mt-1">{clip.reason}</p>
          <p className="mt-2 italic">“{clip.hook}”</p>
        </div>
        <button type="button" onClick={() => copy("Upload text", clip.upload_text)}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:underline">
          <Copy className="size-3.5" /> Copy everything for the upload form (saved version)
        </button>
      </form>
    </Modal>
  );
}

function Row({ label, count, max, hint, onCopy, children }: {
  label: string; count: number; max: number; hint?: ReactNode; onCopy?: () => void; children: ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2 text-xs font-medium tracking-wide text-muted uppercase">
        <span className="mr-auto">{label}</span>
        <span className={cx("font-mono normal-case", count > max ? "text-err" : "text-faint")}>{count}/{max}</span>
        {onCopy && (
          <button type="button" onClick={onCopy} className="rounded p-0.5 text-faint hover:text-fg" title={`Copy ${label.toLowerCase()}`} aria-label={`Copy ${label}`}>
            <Copy className="size-3.5" />
          </button>
        )}
      </div>
      {children}
      {hint && <p className="mt-1.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}
