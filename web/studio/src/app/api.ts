export type AppName = "shorts" | "anime";

export interface QueueTask {
  app: AppName;
  job_id: string;
  kind: string;
  title: string;
  queued_at?: string;
}

export interface Status {
  version: string;
  queue: { current: QueueTask | null; queued: QueueTask[] };
  disk_free_gb: number;
  ram_available_gb: number;
  gemini: boolean;
  ai?: { provider: string | null; models?: string[]; key_set: boolean; error?: string };
}

export interface AiChoice {
  provider: string;
  model: string;
  key_set: boolean;
}

export interface AiModels {
  choices: AiChoice[];
  current: NonNullable<Status["ai"]>;
  picked: { provider: string; model: string | null } | null;
}

export interface RecentJob {
  app: AppName;
  id: string;
  title: string;
  status: "queued" | "running" | "done" | "failed" | "interrupted";
  created_at: string;
  duration: number | null;
  progress: number;
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const studio = {
  status: () => get<Status>("/api/status"),
  recent: () => get<RecentJob[]>("/api/recent?limit=10"),
  aiModels: () => get<AiModels>("/api/ai/shorts"),
  setAiModel: async (provider: string | null, model: string | null): Promise<AiModels> => {
    const res = await fetch("/api/ai/shorts", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, model }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `${res.status} ${res.statusText}`);
    return res.json();
  },
};

export const jobHref = (app: AppName, id: string) => `/${app}/jobs/${encodeURIComponent(id)}`;
export const KIND_LABEL: Record<string, string> = { pipeline: "Processing", render: "Re-rendering", export: "Exporting" };
