import { useEffect, useState } from "react";
import { Pencil, Sparkles } from "lucide-react";
import { useConfirm, useToast } from "@kit/feedback";
import { Segmented, TextArea } from "@kit/ui";
import { api, isActive, type FocusMode, type Job } from "../api";
import { errorText } from "../lib";
import { Button, Card, CardHeader } from "./ui";

const MAX = 500;
const same = (a: Job["focus"], b: Job["focus"]) => (a?.text ?? "") === (b?.text ?? "") && (a?.mode ?? "prefer") === (b?.mode ?? "prefer");

/** The project's focus note: what the AI looks for. Editing it can pick the moments again. */
export function FocusPanel({ job, onJob }: { job: Job; onJob: (j: Job) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(job.focus?.text ?? "");
  const [mode, setMode] = useState<FocusMode>(job.focus?.mode ?? "prefer");
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const confirm = useConfirm();
  const active = isActive(job.status);
  const transcribed = job.stages.find((s) => s.key === "transcribe")?.status === "done";
  const picked = job.stages.find((s) => s.key === "select")?.status === "done";
  const stale = picked && !active && !same(job.focus, job.picked_focus);

  useEffect(() => {
    if (!editing) {
      setText(job.focus?.text ?? "");
      setMode(job.focus?.mode ?? "prefer");
    }
  }, [job.focus, editing]);

  async function save(repick: boolean) {
    if (repick && picked) {
      const ok = await confirm({
        title: "Pick the moments again?",
        body: "The AI picks new clips with this note. Reviews, text edits and framing stay only on clips that come back with exactly the same start and end.",
        confirm: "Pick again",
      });
      if (!ok) return;
    }
    setBusy(true);
    try {
      const j = await api.setFocus(job.id, text.trim(), mode, repick);
      onJob(j);
      setEditing(false);
      toast.ok(repick && transcribed ? "Picking moments again with your note" : "Focus note saved",
        repick && transcribed ? undefined : "It applies when the moments are picked");
    } catch (e) {
      toast.err("Couldn't save the focus note", errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const canRepick = transcribed && !active;
  return (
    <Card>
      <CardHeader
        title={<span className="inline-flex items-center gap-1.5"><Sparkles className="size-3.5 text-accent" /> Focus</span>}
        aside={!editing && !(active && picked) && (
          <button onClick={() => setEditing(true)} className="inline-flex items-center gap-1 text-xs text-muted hover:text-fg">
            <Pencil className="size-3" /> {job.focus ? "Edit" : "Add"}
          </button>
        )}
      />
      <div className="space-y-3 p-4 text-sm">
        {editing ? (
          <>
            <TextArea value={text} onChange={(e) => setText(e.target.value.slice(0, MAX))} rows={3} autoFocus
              placeholder="e.g. every time they talk about money" aria-label="Focus note" />
            <Segmented<FocusMode> value={mode} onChange={setMode} options={[
              { value: "prefer", label: "These first", hint: "These moments first, plus the other strong moments" },
              { value: "only", label: "Only these", hint: "Just these moments" },
            ]} />
            <div className="flex flex-wrap gap-2">
              {canRepick ? (
                <Button onClick={() => save(true)} loading={busy} disabled={same(job.focus, text.trim() ? { text: text.trim(), mode } : null) && !stale}>
                  Save and pick again
                </Button>
              ) : (
                <Button onClick={() => save(false)} loading={busy}>Save</Button>
              )}
              <Button variant="ghost" onClick={() => setEditing(false)} disabled={busy}>Cancel</Button>
            </div>
            <p className="text-xs text-faint">Picking again reuses the transcript: only the AI pass, framing and previews run.</p>
          </>
        ) : job.focus ? (
          <>
            <p className="leading-relaxed">“{job.focus.text}”</p>
            <p className="text-xs text-muted">{job.focus.mode === "only" ? "Only these moments" : "These moments first, plus the other strong ones"}</p>
          </>
        ) : (
          <p className="text-xs text-muted">No note: the AI picks the video's best moments. Add one to ask for specific moments.</p>
        )}
        {stale && !editing && (
          <div className="rounded-lg border border-warn/30 bg-warn/5 p-2.5 text-xs">
            <p className="text-warn">The current clips were picked {job.picked_focus ? "with a different note" : "without this note"}.</p>
            <Button variant="outline" className="mt-2 h-8" onClick={() => save(true)} loading={busy}>Pick again with this note</Button>
          </div>
        )}
      </div>
    </Card>
  );
}
