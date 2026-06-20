import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, type Job } from "../api/client";

// Realtime batch progress (§9) via short-interval polling of GET /jobs. We deliberately
// poll instead of using a WebSocket: the dev Vite proxy mangles ws upgrades (ECONNRESET /
// 426) and these batches are slow (image/TTS take seconds), so a ~1.2s poll is plenty and
// works identically in dev and prod over the plain /api HTTP proxy. The server is the
// source of truth, so a batch keeps running (and stays visible) across tab switches and
// full page reloads.
type Ctx = {
  jobs: Job[];
  jobFor: (type: string) => Job | undefined;
  cancel: (id: string) => void;
};

const JobsCtx = createContext<Ctx>({ jobs: [], jobFor: () => undefined, cancel: () => {} });

const FAST_MS = 1200; // a job is running → poll snappily
const IDLE_MS = 4000; // nothing running → poll lazily to catch newly-started jobs

export function JobsProvider({ projectId, children }: { projectId: string; children: ReactNode }) {
  const [map, setMap] = useState<Record<string, Job>>({});
  const mapRef = useRef(map);
  mapRef.current = map;

  useEffect(() => {
    setMap({});
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      if (stopped) return;
      let anyRunning = false;
      try {
        const r = await api.listJobs(projectId);
        if (stopped) return;
        const next: Record<string, Job> = {};
        for (const j of r.jobs) next[j.id] = j;
        // Keep just-finished jobs that the server already reaped around briefly so the
        // banner can show the final state, but drop them once they age out.
        for (const [id, j] of Object.entries(mapRef.current)) {
          if (!next[id] && j.status !== "running" && j.updated_at * 1000 > Date.now() - 15000) {
            next[id] = j;
          }
        }
        setMap(next);
        anyRunning = r.jobs.some((j) => j.status === "running");
      } catch {
        /* transient — keep last known state, retry next tick */
      }
      if (!stopped) timer = setTimeout(tick, anyRunning ? FAST_MS : IDLE_MS);
    };

    tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId]);

  const jobs = Object.values(map)
    .filter((j) => j.project_id === projectId)
    .sort((a, b) => a.created_at - b.created_at);

  // The most recent running job of a type (what a tab's "auto gen" button tracks).
  const jobFor = (type: string) =>
    [...jobs].reverse().find((j) => j.type === type && j.status === "running");

  const cancel = (id: string) => {
    api.cancelJob(id).catch(() => {});
  };

  return <JobsCtx.Provider value={{ jobs, jobFor, cancel }}>{children}</JobsCtx.Provider>;
}

export const useJobs = () => useContext(JobsCtx);

// Helper: run `onAdvance` whenever a job of `type` makes progress, and `onDone`
// when it finishes. Used by tabs to refetch their rows as a batch proceeds.
export function useJobWatcher(
  type: string,
  handlers: { onAdvance?: () => void; onDone?: (job: Job) => void }
) {
  const { jobs } = useJobs();
  const job = [...jobs].reverse().find((j) => j.type === type);
  const lastProgress = useRef<number>(-1);
  const lastStatus = useRef<string>("");

  useEffect(() => {
    if (!job) return;
    const seen = job.done + job.errors.length;
    if (seen !== lastProgress.current) {
      lastProgress.current = seen;
      handlers.onAdvance?.();
    }
    if (job.status !== "running" && job.status !== lastStatus.current) {
      handlers.onDone?.(job);
    }
    lastStatus.current = job.status;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.done, job?.errors.length, job?.status]);

  return job;
}
