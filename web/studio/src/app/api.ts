import { json, request } from "@kit/http";

export interface QueueTask {
  app: "shorts";
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

export const studio = {
  status: () => request<Status>("/api/status", { timeoutMs: 10000 }),
  aiModels: () => request<AiModels>("/api/ai/shorts"),
  setAiModel: (provider: string | null, model: string | null) =>
    request<AiModels>("/api/ai/shorts", json("PUT", { provider, model })),
};

export const KIND_LABEL: Record<string, string> = { pipeline: "Processing", render: "Re-rendering", export: "Exporting" };
