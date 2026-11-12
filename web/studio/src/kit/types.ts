export type StageStatus = "pending" | "running" | "done" | "failed" | "planned";

export interface Stage {
  key: string;
  label: string;
  status: StageStatus;
  progress: number | null;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  required: boolean;
}

/** 0-1 across all stages (running stages count by their own progress). */
export function stagesProgress(stages: Stage[]): number {
  const real = stages.filter((s) => s.status !== "planned");
  if (!real.length) return 0;
  const sum = real.reduce((a, s) => a + (s.status === "done" ? 1 : s.status === "running" ? (s.progress ?? 0) : 0), 0);
  return sum / real.length;
}
