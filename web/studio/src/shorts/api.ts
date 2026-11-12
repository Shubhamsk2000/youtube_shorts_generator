export type StageStatus = "pending" | "running" | "done" | "failed" | "planned";
export type JobStatus = "queued" | "running" | "done" | "failed" | "interrupted";

export interface Stage {
  key: string;
  label: string;
  status: StageStatus;
  progress: number | null;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ExportState {
  status: "queued" | "running" | "done" | "failed";
  progress: number | null;
  message: string | null;
  dir: string | null;
  files: string[];
  finished_at: string | null;
  queue: string[];
  current: string | null;
}

export interface Job {
  id: string;
  source: string;
  title: string;
  duration: number | null;
  is_youtube: boolean;
  status: JobStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  stages: Stage[];
  width: number | null;
  height: number | null;
  fps: number | null;
  export: ExportState | null;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  required: boolean;
}

export interface SystemInfo {
  ready: boolean;
  disk_free_gb: number;
  ram_available_gb: number;
  checks: Check[];
}

export interface Sentence {
  id: number;
  start: number;
  end: number;
  text: string;
}

export interface TranscriptData {
  model: string;
  word_count: number;
  sentences: Sentence[];
}

export type Review = "approved" | "rejected" | null;

export interface ClipScores {
  hook: number;
  standalone: number;
  payoff: number;
  energy: number;
}

export interface Clip {
  id: string;
  rank: number;
  start: number;
  end: number;
  duration: number;
  title: string;
  hook: string;
  reason: string;
  scores: ClipScores;
  score: number;
  emphasis_words: string[];
  text: string;
  hook_text: string;
  description: string;
  hashtags: string[];
  heat: number | null;
  review: Review;
  ready: boolean;
  url: string | null;
  final_url: string | null;
}

export interface ClipsData {
  source: "gemini" | "heuristic";
  model: string | null;
  note: string | null;
  candidates: number;
  rejected: Record<string, number>;
  clips: Clip[];
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  } catch {
    throw new ApiError("Can't reach the server. Is `shorts serve` running?", 0);
  }
  if (!res.ok) {
    let msg = res.statusText || `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(msg, res.status);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const api = {
  system: () => req<SystemInfo>("/api/shorts/system"),
  jobs: () => req<Job[]>("/api/shorts/jobs"),
  job: (id: string) => req<Job>(`/api/shorts/jobs/${encodeURIComponent(id)}`),
  create: (url: string) => req<Job>("/api/shorts/jobs", { method: "POST", body: JSON.stringify({ url }) }),
  retry: (id: string) => req<Job>(`/api/shorts/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" }),
  remove: (id: string) => req<void>(`/api/shorts/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  transcript: (id: string) => req<TranscriptData>(`/api/shorts/jobs/${encodeURIComponent(id)}/transcript`),
  clips: (id: string) => req<ClipsData>(`/api/shorts/jobs/${encodeURIComponent(id)}/clips`),
  exportClips: (id: string, clipIds: string[] | null) =>
    req<Job>(`/api/shorts/jobs/${encodeURIComponent(id)}/export`, {
      method: "POST",
      body: JSON.stringify({ clip_ids: clipIds }),
    }),
  reselect: (id: string) => req<Job>(`/api/shorts/jobs/${encodeURIComponent(id)}/reselect`, { method: "POST" }),
  review: (id: string, clipId: string, review: Review) =>
    req<{ id: string; review: Review }>(
      `/api/shorts/jobs/${encodeURIComponent(id)}/clips/${encodeURIComponent(clipId)}/review`,
      { method: "PUT", body: JSON.stringify({ review }) },
    ),
};

export const isActive = (s: JobStatus) => s === "queued" || s === "running";
export const isExporting = (j: Job) => j.export?.status === "queued" || j.export?.status === "running";

/** Average progress across implemented stages, 0..1. */
export function overallProgress(job: Job): number {
  const real = job.stages.filter((s) => s.status !== "planned");
  if (!real.length) return 0;
  const sum = real.reduce(
    (acc, s) => acc + (s.status === "done" ? 1 : s.status === "running" ? (s.progress ?? 0) : 0),
    0,
  );
  return sum / real.length;
}

export const currentStage = (job: Job) => job.stages.find((s) => s.status === "running");

export const thumbnailUrl = (job: Job) =>
  job.is_youtube ? `https://i.ytimg.com/vi/${job.id}/hqdefault.jpg` : null;

/** Streams a local video to the server (XHR: fetch has no upload progress). Resolves with the new job. */
export function uploadVideo(file: File, onProgress: (f: number) => void): { done: Promise<Job>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const done = new Promise<Job>((resolve, reject) => {
    xhr.open("PUT", `/api/shorts/uploads/${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: string } & Partial<Job> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not json */
      }
      if (xhr.status < 300) resolve(body as Job);
      else reject(new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed: connection lost"));
    xhr.onabort = () => reject(new Error("Upload cancelled"));
    xhr.send(file);
  });
  return { done, abort: () => xhr.abort() };
}
