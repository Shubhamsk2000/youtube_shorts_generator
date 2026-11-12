import { useMemo, useState } from "react";
import { AudioLines, Search } from "lucide-react";
import { api, type Job } from "../api";
import { fmtTime, usePoll } from "../lib";
import { Card, CardHeader } from "./ui";

export function TranscriptPanel({ job, onSeek }: { job: Job; onSeek: (t: number) => void }) {
  const stage = job.stages.find((s) => s.key === "transcribe");
  const ready = stage?.status === "done";
  const { data, error } = usePoll(
    () => (ready ? api.transcript(job.id) : Promise.resolve(undefined)),
    () => null,
    ready ? `${job.id}:ready` : `${job.id}:pending`,
  );
  const [q, setQ] = useState("");

  const sentences = useMemo(() => {
    if (!ready || !data) return [];
    const needle = q.trim().toLowerCase();
    return needle ? data.sentences.filter((s) => s.text.toLowerCase().includes(needle)) : data.sentences;
  }, [data, q, ready]);

  return (
    <Card>
      <CardHeader
        title="Transcript"
        aside={ready && data && `${data.word_count.toLocaleString()} words · whisper ${data.model}`}
      />
      {!ready ? (
        <Empty running={stage?.status === "running"} />
      ) : error ? (
        <p className="p-5 text-sm text-err">{error}</p>
      ) : !data ? (
        <div className="space-y-3 p-5">
          {Array.from({ length: 6 }, (_, i) => (
            <div key={i} className="shimmer h-4 rounded" style={{ width: `${60 + ((i * 17) % 35)}%` }} />
          ))}
        </div>
      ) : (
        <>
          <div className="border-b border-line px-3 py-2">
            <label className="flex items-center gap-2 rounded-lg px-2 text-sm focus-within:bg-surface-2">
              <Search className="size-4 text-faint" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search transcript"
                className="h-9 flex-1 bg-transparent outline-none placeholder:text-faint"
              />
              {q && <span className="text-xs text-faint">{sentences.length} matches</span>}
            </label>
          </div>
          <ul className="max-h-[520px] overflow-y-auto p-2">
            {sentences.map((s) => (
              <li key={s.id}>
                <button
                  onClick={() => onSeek(s.start)}
                  className="group flex w-full gap-4 rounded-lg px-3 py-2 text-left transition hover:bg-surface-2"
                >
                  <span className="w-14 shrink-0 pt-px font-mono text-xs text-faint group-hover:text-accent">
                    {fmtTime(s.start)}
                  </span>
                  <span className="text-sm leading-relaxed">
                    <Highlight text={s.text} q={q} />
                  </span>
                </button>
              </li>
            ))}
            {!sentences.length && <li className="p-6 text-center text-sm text-muted">No matches</li>}
          </ul>
        </>
      )}
    </Card>
  );
}

function Empty({ running }: { running: boolean }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      <div className="grid size-11 place-items-center rounded-2xl bg-surface-2 text-muted">
        <AudioLines className={running ? "size-5 animate-pulse text-accent" : "size-5"} />
      </div>
      <p className="mt-3 text-sm font-medium">{running ? "Listening…" : "No transcript yet"}</p>
      <p className="mt-1 max-w-xs text-sm text-muted">
        {running
          ? "Whisper is transcribing locally. Words appear with exact timestamps when it finishes."
          : "The transcript appears once the video is downloaded and transcribed."}
      </p>
    </div>
  );
}

function Highlight({ text, q }: { text: string; q: string }) {
  const needle = q.trim();
  if (!needle) return <>{text}</>;
  const parts = text.split(new RegExp(`(${needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"));
  return (
    <>
      {parts.map((p, i) =>
        p.toLowerCase() === needle.toLowerCase() ? (
          <mark key={i} className="rounded bg-accent-soft px-0.5 text-fg">
            {p}
          </mark>
        ) : (
          p
        ),
      )}
    </>
  );
}
