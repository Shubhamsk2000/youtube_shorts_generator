import { useState } from "react";
import { Check, HardDrive, LoaderCircle, RefreshCw, Trash2, X } from "lucide-react";
import { useConfirm, useToast } from "@kit/feedback";
import { cx, errorText, fmtBytes, Link, timeAgo, usePoll } from "@kit/lib";
import { Button, Card, CardHeader, PageHeader, Stat } from "@kit/ui";
import { api, isActive, jobHref } from "../shorts/api";
import { KIND_LABEL, type Status } from "./api";

export function SystemPage({ status }: { status?: Status }) {
  const system = usePoll(api.system, () => 30000, "system-page");
  const [storageKey, setStorageKey] = useState(0);
  const storage = usePoll(api.storage, () => null, `storage-${storageKey}`);
  const [deleting, setDeleting] = useState<string | null>(null);
  const toast = useToast();
  const confirm = useConfirm();

  async function remove(id: string, title: string, exportsBytes: number) {
    const ok = await confirm({
      title: "Delete this project?",
      body: <>Frees the working files of <b className="text-fg">{title}</b>. This can't be undone.</>,
      confirm: "Delete",
      danger: true,
      check: exportsBytes ? `Also delete its exported HD clips (${fmtBytes(exportsBytes)})` : undefined,
    });
    if (!ok) return;
    setDeleting(id);
    try {
      await api.remove(id, ok.checked);
      toast.ok("Project deleted");
      setStorageKey((k) => k + 1);
    } catch (e) {
      toast.err("Couldn't delete", errorText(e));
    } finally {
      setDeleting(null);
    }
  }

  const checks = system.data?.checks ?? [];
  const required = checks.filter((c) => c.required);
  const optional = checks.filter((c) => !c.required);
  const s = storage.data;
  const used = s ? s.total_bytes : null;
  const ai = status?.ai;

  return (
    <div className="mx-auto max-w-6xl px-4 pt-10 pb-16 sm:px-6">
      <PageHeader eyebrow="System" title="Status & storage">
        What this machine has, what's running, and where the disk space goes.
      </PageHeader>

      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Setup" value={!system.data ? "–" : system.data.ready ? "Ready" : "Needs setup"}
          tone={system.data ? (system.data.ready ? "ok" : "err") : undefined}
          sub={system.data ? `${required.filter((c) => c.ok).length}/${required.length} required checks` : system.error ?? "checking…"} />
        <Stat label="Disk free" value={status ? `${status.disk_free_gb} GB` : "–"} tone={status && status.disk_free_gb < 5 ? "warn" : undefined}
          sub={used != null ? `${fmtBytes(used)} used by projects` : "counting…"} />
        <Stat label="RAM available" value={status ? `${status.ram_available_gb} GB` : "–"} tone={status && status.ram_available_gb < 1.5 ? "warn" : undefined}
          sub="transcription needs ~1.5 GB" />
        <Stat label="Moment picker" value={ai?.provider ? (ai.models?.[0]?.split("/").pop() ?? "?") : "–"}
          tone={ai ? (ai.provider && ai.key_set ? undefined : "warn") : undefined}
          sub={ai ? (ai.provider ? `${ai.provider}${ai.key_set ? "" : " · API key missing"}` : ai.error ?? "not configured") : ""} />
      </div>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="Setup checks" aside={
            <button onClick={system.reload} className="inline-flex items-center gap-1 hover:text-fg"><RefreshCw className="size-3" /> Re-check</button>
          } />
          {!system.data ? (
            <p className="p-5 text-sm text-muted">{system.error ?? "Checking…"}</p>
          ) : (
            <ul className="divide-y divide-line">
              {[...required, ...optional].map((c) => (
                <li key={c.name} className="flex items-start gap-3 px-5 py-2.5">
                  <span className={cx("mt-0.5 grid size-5 shrink-0 place-items-center rounded-full",
                    c.ok ? "bg-ok/15 text-ok" : c.required ? "bg-err/15 text-err" : "bg-warn/15 text-warn")}>
                    {c.ok ? <Check className="size-3" strokeWidth={3} /> : <X className="size-3" strokeWidth={3} />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{c.name}{!c.required && <span className="ml-1.5 text-xs font-normal text-faint">optional</span>}</p>
                    {c.detail && <p className="mt-0.5 font-mono text-xs break-words text-muted">{c.detail}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card>
          <CardHeader title="Worker" aside="one task at a time" />
          {!status ? (
            <p className="p-5 text-sm text-muted">Connecting…</p>
          ) : !status.queue.current && !status.queue.queued.length ? (
            <p className="p-5 text-sm text-muted">Idle. New videos start right away.</p>
          ) : (
            <ul className="divide-y divide-line">
              {[status.queue.current, ...status.queue.queued].filter(Boolean).map((t, i) => (
                <li key={`${t!.job_id}-${t!.kind}-${i}`}>
                  <Link to={jobHref(t!.job_id)} className="flex items-center gap-3 px-5 py-2.5 text-sm hover:bg-surface-2">
                    {i === 0 && status.queue.current ? <LoaderCircle className="size-4 shrink-0 animate-spin text-accent" /> : <span className="w-4 text-center font-mono text-xs text-faint">{i}</span>}
                    <span className="min-w-0 flex-1 truncate">{t!.title}</span>
                    <span className="shrink-0 text-xs text-muted">{KIND_LABEL[t!.kind] ?? t!.kind}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card className="min-w-0 lg:col-span-2">
          <CardHeader title={<span className="flex items-center gap-2"><HardDrive className="size-4" /> Storage</span>} aside={
            <button onClick={() => setStorageKey((k) => k + 1)} className="inline-flex items-center gap-1 hover:text-fg"><RefreshCw className="size-3" /> Recount</button>
          } />
          {!s ? (
            <p className="p-5 text-sm text-muted">{storage.error ? `Couldn't count: ${storage.error}` : "Counting files… (can take a few seconds)"}</p>
          ) : (
            <>
              <DiskBar used={s.total_bytes} free={s.disk_free_gb * 1e9} />
              <div className="relative overflow-x-auto">
                <table className="w-full min-w-[560px] text-sm">
                  <thead className="text-left text-xs text-muted">
                    <tr className="border-b border-line">
                      <th className="px-5 py-2 font-medium">Project</th>
                      <th className="px-3 py-2 text-right font-medium" title="Downloads, transcript, previews, HD renders">Working files</th>
                      <th className="px-3 py-2 text-right font-medium" title="data/shorts/exports (hard-linked to the HD renders when on the same disk: counted once)">Exports</th>
                      <th className="px-5 py-2"><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {s.projects.map((p) => (
                      <tr key={p.id}>
                        <td className="max-w-0 px-5 py-2.5">
                          <Link to={jobHref(p.id)} className="block truncate font-medium hover:text-accent">{p.title}</Link>
                          <span className="text-xs text-faint">{timeAgo(p.created_at)}</span>
                        </td>
                        <td className="px-3 py-2.5 text-right font-mono text-xs">{fmtBytes(p.workspace_bytes)}</td>
                        <td className="px-3 py-2.5 text-right font-mono text-xs text-muted">{p.exports_bytes ? fmtBytes(p.exports_bytes) : "–"}</td>
                        <td className="px-5 py-2.5 text-right">
                          <Button variant="ghost" className="h-8 px-2" aria-label={`Delete ${p.title}`} title={isActive(p.status) ? "Stop it first" : "Delete project"}
                            disabled={isActive(p.status)} loading={deleting === p.id} onClick={() => remove(p.id, p.title, p.exports_bytes)}>
                            <Trash2 className="size-4" />
                          </Button>
                        </td>
                      </tr>
                    ))}
                    {s.uploads_bytes > 0 && (
                      <tr>
                        <td className="px-5 py-2.5 text-muted">Uploaded source files <span className="text-xs text-faint">(data/shorts/uploads)</span></td>
                        <td className="px-3 py-2.5 text-right font-mono text-xs">{fmtBytes(s.uploads_bytes)}</td>
                        <td colSpan={2} />
                      </tr>
                    )}
                    {!s.projects.length && <tr><td colSpan={4} className="px-5 py-6 text-center text-muted">No projects.</td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      </div>
    </div>
  );
}

function DiskBar({ used, free }: { used: number; free: number }) {
  const total = used + free;
  const pct = total ? (used / total) * 100 : 0;
  return (
    <div className="border-b border-line px-5 py-4">
      <div className="h-2 overflow-hidden rounded-full bg-surface-2">
        <div className={cx("h-full rounded-full", free < 5e9 ? "bg-warn" : "bg-accent")} style={{ width: `${Math.max(1, pct)}%` }} />
      </div>
      <p className="mt-2 flex justify-between text-xs text-muted">
        <span>{fmtBytes(used)} used by Studio</span>
        <span>{fmtBytes(free)} free</span>
      </p>
    </div>
  );
}
