export type StageStatus = "pending" | "running" | "done" | "failed" | "planned";

export interface Stage {
  key: string;
  label: string;
  status: StageStatus;
  progress: number | null;
  message: string | null;
  started_at: string | null;
  finished_at: string | null;
  eta_s?: number | null;
  weight?: number;
}

export interface Check {
  name: string;
  ok: boolean;
  detail: string;
  required: boolean;
}

/** 0-1 across all stages, each weighted by its typical share of the run (running stages count by their progress). */
export function stagesProgress(stages: Stage[]): number {
  const real = stages.filter((s) => s.status !== "planned");
  const total = real.reduce((a, s) => a + (s.weight ?? 1), 0);
  if (!total) return 0;
  const got = real.reduce(
    (a, s) => a + (s.weight ?? 1) * (s.status === "done" ? 1 : s.status === "running" ? (s.progress ?? 0) : 0),
    0,
  );
  return got / total;
}
