import { useState, type ClipboardEvent, type FormEvent } from "react";
import { ArrowRight, Link2 } from "lucide-react";
import { api } from "../api";
import { cx, navigate } from "../lib";
import { Button } from "./ui";

const looksLikeVideoUrl = (s: string) =>
  /^https?:\/\/(www\.|m\.)?(youtube\.com\/(watch|shorts|live)|youtu\.be\/)/i.test(s.trim());

export function UrlForm({ blocked }: { blocked?: string | null }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(value = url) {
    const v = value.trim();
    if (!v || busy) return;
    if (!/^https?:\/\//i.test(v)) {
      setError("Paste a full link, like https://youtube.com/watch?v=…");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const job = await api.create(v);
      navigate(`/jobs/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  // Fully automatic: pasting a YouTube link starts the job immediately.
  function onPaste(e: ClipboardEvent<HTMLInputElement>) {
    const pasted = e.clipboardData.getData("text");
    if (looksLikeVideoUrl(pasted) && !blocked) {
      e.preventDefault();
      setUrl(pasted.trim());
      submit(pasted);
    }
  }

  return (
    <div className="w-full">
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault();
          submit();
        }}
        className={cx(
          "group flex items-center gap-2 rounded-2xl border bg-surface p-1.5 pl-4 shadow-card transition",
          error ? "border-err/50" : "border-line-strong focus-within:border-accent focus-within:ring-4 focus-within:ring-accent-soft",
        )}
      >
        <Link2 className="size-[18px] shrink-0 text-faint transition group-focus-within:text-accent" />
        <input
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setError(null);
          }}
          onPaste={onPaste}
          placeholder="Paste a YouTube link"
          aria-label="YouTube URL"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          autoFocus
          disabled={busy}
          className="h-11 min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-faint"
        />
        <Button type="submit" loading={busy} disabled={!url.trim() || !!blocked} className="h-11 rounded-xl px-5">
          {busy ? "Fetching" : "Generate"}
          {!busy && <ArrowRight className="size-4" />}
        </Button>
      </form>
      <p className={cx("mt-2.5 min-h-5 px-1 text-sm", error ? "text-err" : "text-faint")}>
        {error ?? blocked ?? "Pasting a link starts processing right away."}
      </p>
    </div>
  );
}
