import type { Check, Stage } from "@kit/types";
import { stagesProgress } from "@kit/types";

const A = "/api/anime";

export type JobStatus = "queued" | "running" | "done" | "failed" | "interrupted";
export type Style = "clip" | "montage" | "song";
export type Audio = "auto" | "eng" | "jpn";
export type Mood = "hype" | "epic" | "sad" | "chill" | "funny";

export const STYLE_LABEL: Record<Style, string> = { clip: "Single scenes", montage: "Montage", song: "Song edit (AMV)" };
export const MOODS: Mood[] = ["hype", "epic", "sad", "chill", "funny"];

export interface Options {
  style: Style;
  count: number;
  audio: Audio;
  captions: boolean;
}

export interface ExportState {
  status: "queued" | "running" | "done" | "failed";
  progress: number | null;
  message: string | null;
  dir: string | null;
  files: Record<string, string>;
  queue: string[];
  current: string | null;
}

export interface Job {
  id: string;
  source: string;
  title: string;
  duration: number | null;
  status: JobStatus;
  error: string | null;
  task: "pipeline" | "render" | null;
  created_at: string;
  updated_at: string;
  stages: Stage[];
  options: Options;
  audio_lang: string | null;
  export: ExportState | null;
}

export type Transition = "cut" | "flash" | "zoom" | "whip" | "fade" | "glitch";

export interface Segment {
  start: number;
  end: number;
  duration: number;
  transition_in: Transition;
  effects: string[];
  punch_at: number | null;
  intensity: number | null;
  out_start: number | null;
}

export interface SongRef {
  id: string;
  title: string;
  start: number;
  end: number;
  bpm: number;
  mood: string;
  auto: boolean;
}

export interface SongOption {
  id: string;
  title: string;
  mood: Mood;
  bpm: number;
  why: string;
}

export type Review = "approved" | "rejected" | null;

export interface Pick {
  id: string;
  rank: number;
  style: Style;
  score: number;
  kind: string;
  title: string;
  tweet: string;
  description: string;
  hashtags: string[];
  why: string;
  watched: boolean;
  duration: number;
  segments: Segment[];
  song: SongRef | null;
  song_options: SongOption[];
  review: Review;
  ready: boolean;
  url: string | null;
  final_url: string | null;
}

export interface PicksData {
  style: Style;
  source: "gemini" | "heuristic";
  model: string | null;
  note: string | null;
  picks: Pick[];
  episode: {
    audio: string | null;
    subs: string | null;
    lines: number;
    excluded: { start: number; end: number; what: string }[];
  };
}

export interface SystemInfo {
  ready: boolean;
  disk_free_gb: number;
  ram_available_gb: number;
  checks: Check[];
  episodes_dir: string;
}

export interface Episode {
  name: string;
  size_mb: number;
  job_id: string | null;
}

export interface CardSettings {
  name: string;
  handle: string;
  theme: "dark" | "light";
  verified: boolean;
  footer: "date" | "none";
  has_avatar: boolean;
}

export interface Settings {
  card: CardSettings;
  defaults: Options;
}

export interface Track {
  id: string;
  title: string;
  duration: number;
  bpm: number;
  mood: Mood;
  mood_source: "folder" | "gemini" | "guess";
  genre: string;
  uses: number;
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(await errorText(res));
  return res.status === 204 ? (undefined as T) : res.json();
}

async function errorText(res: Response): Promise<string> {
  try {
    const j = await res.json();
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch {
    /* not json */
  }
  return `${res.status} ${res.statusText}`;
}

const enc = encodeURIComponent;
const trackPath = (id: string) => id.split("/").map(enc).join("/");

export const api = {
  system: () => req<SystemInfo>("GET", `${A}/system`),
  episodes: () => req<{ dir: string; episodes: Episode[] }>("GET", `${A}/episodes`),
  jobs: () => req<Job[]>("GET", `${A}/jobs`),
  job: (id: string) => req<Job>("GET", `${A}/jobs/${enc(id)}`),
  create: (source: string, options: Options) => req<Job>("POST", `${A}/jobs`, { source, options }),
  retry: (id: string) => req<Job>("POST", `${A}/jobs/${enc(id)}/retry`),
  reselect: (id: string, body: { style?: Style; count?: number }) => req<Job>("POST", `${A}/jobs/${enc(id)}/reselect`, body),
  setCaptions: (id: string, captions: boolean) => req<Job>("PUT", `${A}/jobs/${enc(id)}/options`, { captions }),
  remove: (id: string) => req<void>("DELETE", `${A}/jobs/${enc(id)}`),
  picks: (id: string) => req<PicksData>("GET", `${A}/jobs/${enc(id)}/picks`),
  editPick: (id: string, pid: string, body: { tweet?: string; title?: string; review?: Review | "none"; song?: string }) =>
    req<{ ok: boolean }>("PUT", `${A}/jobs/${enc(id)}/picks/${enc(pid)}`, body),
  export: (id: string, pick_ids: string[] | null) => req<Job>("POST", `${A}/jobs/${enc(id)}/export`, { pick_ids }),
  settings: () => req<Settings>("GET", `${A}/settings`),
  saveCard: (body: Partial<CardSettings> & { remove_avatar?: boolean }) => req<Settings>("PUT", `${A}/settings/card`, body),
  uploadAvatar: async (file: File) => {
    const res = await fetch(`${A}/settings/avatar`, { method: "PUT", body: file });
    if (!res.ok) throw new Error(await errorText(res));
    return (await res.json()) as Settings;
  },
  cardPreview: (text: string, v: number) => `${A}/settings/card/preview?text=${enc(text)}&v=${v}`,
  music: () => req<{ dir: string; tracks: Track[] }>("GET", `${A}/music`),
  setMood: (id: string, mood: Mood) => req<{ id: string; mood: Mood }>("PUT", `${A}/music/mood/${trackPath(id)}`, { mood }),
  deleteTrack: (id: string) => req<void>("DELETE", `${A}/music/${trackPath(id)}`),
  trackUrl: (id: string) => `${A}/music/file/${trackPath(id)}`,
};

/** Streams a file to the server; XHR because fetch has no upload progress. */
function upload<T>(url: string, file: File, onProgress: (f: number) => void): { done: Promise<T>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const done = new Promise<T>((resolve, reject) => {
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let body: { detail?: unknown } = {};
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* not json */
      }
      if (xhr.status < 300) resolve(body as T);
      else reject(new Error(typeof body.detail === "string" ? body.detail : `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed: connection lost"));
    xhr.onabort = () => reject(new Error("Upload cancelled"));
    xhr.send(file);
  });
  return { done, abort: () => xhr.abort() };
}

export const uploadEpisode = (file: File, onProgress: (f: number) => void) =>
  upload<Episode>(`${A}/episodes/${enc(file.name)}`, file, onProgress);

export const uploadTrack = (file: File, mood: Mood | null, onProgress: (f: number) => void) =>
  upload<Track>(`${A}/music/${enc(file.name)}${mood ? `?mood=${mood}` : ""}`, file, onProgress);

export const isActive = (j: Job) => j.status === "queued" || j.status === "running";
export const exporting = (j: Job) => j.export?.status === "queued" || j.export?.status === "running";
export const overallProgress = (j: Job) => stagesProgress(j.stages);
export const currentStage = (j: Job) => j.stages.find((s) => s.status === "running") ?? null;
