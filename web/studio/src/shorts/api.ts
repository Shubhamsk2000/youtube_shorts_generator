import { ApiError, json, request } from "@kit/http";
import { stagesProgress, type Stage } from "@kit/types";

export { ApiError };
export type { Stage };
export type StageStatus = Stage["status"];
export type JobStatus = "queued" | "running" | "done" | "failed" | "interrupted";

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

export interface JobStats {
  clips: number;
  approved: number;
  rejected: number;
  exported: number;
  clip_seconds: number;
  picked_by: "gemini" | "heuristic" | null;
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
  stopping: boolean;
  stats: JobStats;
  progress: number;
  server_time: string;
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
  show_hook?: boolean;
  context?: string;
  loop?: boolean;
  description: string;
  hashtags: string[];
  heat: number | null;
  review: Review;
  ready: boolean;
  url: string | null;
  final_url: string | null;
  upload_text: string;
}

export interface ClipsData {
  source: "gemini" | "heuristic";
  model: string | null;
  note: string | null;
  candidates: number;
  rejected: Record<string, number>;
  clips: Clip[];
}

export type ClipEdit = Partial<Pick<Clip, "title" | "hook_text" | "show_hook" | "description" | "hashtags">>;

/** One window keyframe from the clip editor (source frame inside the clip). x null = follow the auto framing. */
export interface FrameKey {
  f: number;
  x: number | null;
  a: number;
}

export interface EditorShot {
  start: number;
  end: number;
  mode: "track" | "fit" | "center" | "split" | "manual";
  why: string;
  x: number[] | null;
  cams: number[][];
}

export interface EditorData {
  fps: number;
  n_frames: number;
  src_w: number;
  src_h: number;
  analysed: boolean;
  shots: EditorShot[];
  faces: { f: number; faces: number[][] }[];
  keys: FrameKey[];
  keep: [number, number][] | null;
  flash: [number, number] | null;
  layout: { out_w: number; out_h: number; fit_y: number; fit_dim: number; screen_layout: ScreenLayout; background: Background; max_aspect: number };
  hook: { enabled: boolean; auto: boolean; text: string; position: number; duration_s: number; size: number; max_chars_per_line: number };
  captions: { enabled: boolean; position: number; size: number };
  clip: Clip;
  prev: string | null;
  next: string | null;
  index: number;
  count: number;
  proxy_url: string;
}

export type ScreenLayout = "split" | "pip" | "track";
export type Background = "blur" | "tweet";

export interface Layout {
  screen_layout: ScreenLayout;
  cam_size: number | null; // null = auto (sized to the cam within cam_range)
  cam_range: [number, number];
  background: Background;
  smart_layout: boolean;
  custom: boolean; // this video has its own layout (else the defaults)
}

export interface SettingField {
  key: string;
  label: string;
  type: "bool" | "int" | "float" | "choice" | "color";
  help: string;
  min: number | null;
  max: number | null;
  step: number | null;
  unit: string;
  options: { value: string; label: string }[];
  applies: "new" | "render" | "export";
  value: unknown;
  default: unknown;
  custom: boolean;
}

export interface SettingsData {
  groups: { id: string; label: string; help: string; fields: SettingField[] }[];
}

export interface CardSettings {
  name: string;
  handle: string;
  theme: "dark" | "light";
  verified: boolean;
  footer: "date" | "none";
  has_avatar: boolean;
}

export interface StorageData {
  projects: { id: string; title: string; status: JobStatus; created_at: string; workspace_bytes: number; exports_bytes: number }[];
  uploads_bytes: number;
  total_bytes: number;
  disk_free_gb: number;
}

const S = "/api/shorts";
const J = (id: string) => `${S}/jobs/${encodeURIComponent(id)}`;

export const api = {
  system: () => request<SystemInfo>(`${S}/system`, { timeoutMs: 20000 }),
  jobs: () => request<Job[]>(`${S}/jobs`),
  job: (id: string) => request<Job>(J(id)),
  create: (url: string) => request<Job>(`${S}/jobs`, { ...json("POST", { url }), timeoutMs: 90000 }),
  retry: (id: string) => request<Job>(`${J(id)}/retry`, json("POST")),
  stop: (id: string) => request<Job>(`${J(id)}/stop`, json("POST")),
  remove: (id: string, exports = false) => request<void>(`${J(id)}?exports=${exports}`, json("DELETE")),
  transcript: (id: string) => request<TranscriptData>(`${J(id)}/transcript`, { timeoutMs: 60000 }),
  clips: (id: string) => request<ClipsData>(`${J(id)}/clips`),
  editClip: (id: string, clipId: string, body: ClipEdit) =>
    request<{ clip: Clip; rerender: boolean }>(`${J(id)}/clips/${encodeURIComponent(clipId)}`, json("PATCH", body)),
  exportClips: (id: string, clipIds: string[] | null) => request<Job>(`${J(id)}/export`, json("POST", { clip_ids: clipIds })),
  stopExport: (id: string) => request<Job>(`${J(id)}/export/stop`, json("POST")),
  zipUrl: (id: string) => `${J(id)}/exports.zip`,
  rerender: (id: string) => request<Job>(`${J(id)}/rerender`, json("POST")),
  layout: (id: string) => request<Layout>(`${J(id)}/layout`),
  setLayout: (id: string, l: Pick<Layout, "screen_layout" | "cam_size" | "background" | "smart_layout">) =>
    request<Layout>(`${J(id)}/layout`, json("PUT", l)),
  editor: (id: string, clipId: string) => request<EditorData>(`${J(id)}/clips/${encodeURIComponent(clipId)}/editor`),
  warmProxy: (id: string, clipId: string) =>
    request<void>(`${J(id)}/clips/${encodeURIComponent(clipId)}/proxy`, { method: "POST", timeoutMs: 300000 }),
  setFraming: (id: string, clipId: string, keys: FrameKey[]) =>
    request<{ keys: FrameKey[]; rerender: boolean }>(`${J(id)}/clips/${encodeURIComponent(clipId)}/framing`, json("PUT", { keys })),
  reselect: (id: string) => request<Job>(`${J(id)}/reselect`, json("POST")),
  review: (id: string, clipId: string, review: Review) =>
    request<{ id: string; review: Review }>(`${J(id)}/clips/${encodeURIComponent(clipId)}/review`, json("PUT", { review })),
  reviews: (id: string, clipIds: string[], review: Review) =>
    request<{ updated: number }>(`${J(id)}/reviews`, json("PUT", { clip_ids: clipIds, review })),
  storage: () => request<StorageData>(`${S}/storage`, { timeoutMs: 120000 }),
  settings: () => request<SettingsData>(`${S}/settings`),
  saveSettings: (changes: Record<string, unknown>) => request<SettingsData>(`${S}/settings`, json("PUT", changes)),
  card: () => request<CardSettings>(`${S}/settings/card`),
  saveCard: (body: Partial<CardSettings> & { remove_avatar?: boolean }) => request<CardSettings>(`${S}/settings/card`, json("PUT", body)),
  uploadAvatar: (file: File) => request<CardSettings>(`${S}/settings/avatar`, { method: "PUT", body: file, timeoutMs: 60000 }),
  cardPreview: (text: string, v: number) => `${S}/settings/card/preview?text=${encodeURIComponent(text)}&v=${v}`,
};

export const isActive = (s: JobStatus) => s === "queued" || s === "running";
export const isExporting = (j: Job) => j.export?.status === "queued" || j.export?.status === "running";

/** 0..1, stages weighted by their typical share of a run (same formula as the server's `progress`). */
export function overallProgress(job: Job): number {
  return job.stages?.length ? stagesProgress(job.stages) : job.progress ?? 0;
}

export const currentStage = (job: Job) => job.stages.find((s) => s.status === "running");

/** Server clock minus browser clock, in ms (0 if unknown). */
export function clockSkew(job: Job | undefined, receivedAt: number | undefined): number {
  if (!job?.server_time || !receivedAt) return 0;
  const t = new Date(job.server_time).getTime();
  return isFinite(t) ? t - receivedAt : 0;
}

export const thumbnailUrl = (job: Job) => (job.is_youtube ? `https://i.ytimg.com/vi/${job.id}/hqdefault.jpg` : null);

export const jobHref = (id: string) => `/jobs/${encodeURIComponent(id)}`;
export const editorHref = (id: string, clipId: string) => `/jobs/${encodeURIComponent(id)}/clips/${encodeURIComponent(clipId)}`;

/** Streams a local video to the server (XHR: fetch has no upload progress). Resolves with the new job. */
export function uploadVideo(
  file: File,
  onProgress: (loaded: number, total: number) => void,
): { done: Promise<Job>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const done = new Promise<Job>((resolve, reject) => {
    xhr.open("PUT", `${S}/uploads/${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded, e.total);
    xhr.onload = () => {
      let body: { detail?: string } & Partial<Job> = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not json */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Job);
      else reject(new ApiError(typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`, xhr.status));
    };
    xhr.onerror = () => reject(new ApiError("Upload failed: the connection to Studio was lost", 0));
    xhr.onabort = () => reject(new ApiError("Upload cancelled", -1));
    xhr.send(file);
  });
  return { done, abort: () => xhr.abort() };
}
