import { Pipeline as KitPipeline } from "@kit/Pipeline";
import type { Job } from "../api";

export function Pipeline({ job }: { job: Job }) {
  return <KitPipeline stages={job.stages} />;
}
