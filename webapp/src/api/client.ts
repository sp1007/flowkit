// Thin fetch wrapper for the Flow Studio backend (/api/studio/*).

export interface Project {
  id: string;
  title: string;
  flow_project_id: string | null;
  style: string;
  aspect_ratio: string;
  storytelling: number;
  thumb_media_key: string | null;
  idea: string | null;
  target_duration: number | null;
  script_raw: string | null;
  image_model?: string | null;
  video_model?: string | null;
  voice_id?: number | null;
  shot_duration?: number | null;
  tts_speed?: number | null;
  seed?: number | null;
  prompt_header?: string | null;
  prompt_footer?: string | null;
  culture_hint?: string | null;
  script_lang?: string | null;
  image_text_lang?: string | null;
  bgm_path?: string | null;
  bgm_volume?: number | null;
  bgm_duck?: number | null;
  status: string;
  updated_at: number;
}

export interface Candidate {
  media_id: string;
  primary_media_id: string;
  workflow_id?: string | null;
  web: string;
}

export interface MediaVersion {
  id: string;
  slot: string; // image | video
  media_id: string;
  path: string;
  created_at: number;
}

// Background batch job (§9). Mirrors agent/studio/jobs.py Job.to_dict().
export interface Job {
  id: string;
  project_id: string;
  type: "assets" | "storyboard" | "videos" | string;
  label: string;
  total: number;
  done: number;
  errors: { item: string; error: string }[];
  status: "running" | "done" | "error" | "cancelled";
  message: string;
  current: string;
  progress: number; // 0..1
  created_at: number;
  updated_at: number;
}

// WebSocket URL for the realtime job feed (/api/studio/ws), same-origin in prod,
// proxied by Vite in dev.
export function studioWsUrl(): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/studio/ws`;
}

export interface FlowProject {
  flow_project_id: string;
  title: string;
  thumb_media_key: string | null;
  creation_time: string | null;
}

export interface Health {
  status: string;
  extension_connected: boolean;
  ffmpeg: boolean;
  tts: boolean;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/studio${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<Health>("/health"),
  options: () => req<any>("/options"),
  credits: () => req<any>("/credits"),
  listProjects: () => req<{ projects: Project[] }>("/projects"),
  flowProjects: () => req<{ projects: FlowProject[] }>("/flow-projects"),
  createProject: (body: any) =>
    req<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: string, body: any) =>
    req<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  uploadBgm: async (id: string, file: File, volume?: number): Promise<Project> => {
    const fd = new FormData();
    fd.append("file", file);
    if (volume != null) fd.append("volume", String(volume));
    // no JSON Content-Type — let the browser set the multipart boundary
    const res = await fetch(`/api/studio/projects/${id}/bgm`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  },
  clearBgm: (id: string) =>
    req<Project>(`/projects/${id}/bgm`, { method: "DELETE" }),
  importProjectZip: async (file: File): Promise<Project> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/studio/projects/import-zip", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  },
  setCover: (id: string, media_id: string) =>
    req<{ project: Project; flow_updated: boolean }>(`/projects/${id}/cover`, {
      method: "PUT",
      body: JSON.stringify({ media_id }),
    }),
  deleteProject: (id: string) =>
    req<{ ok: boolean }>(`/projects/${id}`, { method: "DELETE" }),
  getSettings: () => req<Record<string, any>>("/settings"),
  putSettings: (body: Record<string, any>) =>
    req<Record<string, any>>("/settings", { method: "PUT", body: JSON.stringify(body) }),
  listFonts: () =>
    req<{ fonts: { name: string; path: string }[]; current: string }>("/fonts"),

  getProject: (id: string) => req<Project>(`/projects/${id}`),
  listScenes: (id: string) => req<{ scenes: Scene[] }>(`/projects/${id}/scenes`),
  generateScript: (id: string, idea: string, target_duration: number | null) =>
    req<ScriptResult>(`/projects/${id}/script/generate`, {
      method: "POST",
      body: JSON.stringify({ idea, target_duration }),
    }),
  saveScript: (id: string, script: string) =>
    req<ScriptResult>(`/projects/${id}/script`, {
      method: "PUT",
      body: JSON.stringify({ script }),
    }),
  scriptChat: (id: string, instruction: string) =>
    req<ScriptResult>(`/projects/${id}/script/chat`, {
      method: "POST",
      body: JSON.stringify({ instruction }),
    }),

  listEntities: (id: string) => req<{ entities: Entity[] }>(`/projects/${id}/entities`),
  extractEntities: (id: string, replace = false) =>
    req<{ added: number; entities: Entity[] }>(
      `/projects/${id}/entities/extract${replace ? "?replace=true" : ""}`,
      { method: "POST" }
    ),
  addEntity: (id: string, body: Partial<Entity>) =>
    req<Entity>(`/projects/${id}/entities`, { method: "POST", body: JSON.stringify(body) }),
  updateEntity: (eid: string, body: Partial<Entity>) =>
    req<Entity>(`/entities/${eid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteEntity: (eid: string) =>
    req<{ ok: boolean }>(`/entities/${eid}`, { method: "DELETE" }),
  generateEntity: (eid: string) =>
    req<Entity>(`/entities/${eid}/generate`, { method: "POST" }),
  // Generate N candidate images (no commit) → pick one → applyMedia (§13#2).
  entityCandidates: (eid: string, n = 3) =>
    req<{ candidates: Candidate[] }>(`/entities/${eid}/candidates`, {
      method: "POST",
      body: JSON.stringify({ n }),
    }),
  shotCandidates: (sid: string, n = 3) =>
    req<{ candidates: Candidate[] }>(`/shots/${sid}/candidates`, {
      method: "POST",
      body: JSON.stringify({ n }),
    }),
  // Media version history (§13#8): list past versions + restore one.
  entityHistory: (eid: string) =>
    req<{ history: MediaVersion[] }>(`/entities/${eid}/history`),
  shotHistory: (sid: string, slot = "image") =>
    req<{ history: MediaVersion[] }>(`/shots/${sid}/history?slot=${slot}`),
  restoreEntityHistory: (eid: string, hid: string) =>
    req<Entity>(`/entities/${eid}/history/${hid}/restore`, { method: "POST" }),
  restoreShotHistory: (sid: string, hid: string) =>
    req<Shot>(`/shots/${sid}/history/${hid}/restore`, { method: "POST" }),
  setEntityImage: (eid: string, media_id: string) =>
    req<Entity>(`/entities/${eid}/image`, { method: "PUT", body: JSON.stringify({ media_id }) }),
  generateAllAssets: (id: string) =>
    req<{ job_id: string; total: number }>(
      `/projects/${id}/assets/generate-all`,
      { method: "POST" }
    ),
  // Background jobs (§9): list active + cancel.
  listJobs: (project_id?: string) =>
    req<{ jobs: Job[] }>(`/jobs${project_id ? `?project_id=${project_id}` : ""}`),
  cancelJob: (jid: string) =>
    req<{ ok: boolean }>(`/jobs/${jid}/cancel`, { method: "POST" }),
  libraryEntities: (excludeProject?: string) =>
    req<{ entities: LibraryEntity[] }>(
      `/library/entities${excludeProject ? `?exclude_project=${excludeProject}` : ""}`
    ),
  importEntity: (pid: string, source_entity_id: string) =>
    req<Entity>(`/projects/${pid}/entities/import`, {
      method: "POST",
      body: JSON.stringify({ source_entity_id }),
    }),
  linkEntity: (eid: string, source_entity_id: string) =>
    req<Entity>(`/entities/${eid}/link`, {
      method: "POST",
      body: JSON.stringify({ source_entity_id }),
    }),
  flowProjectMedia: (flowId: string) =>
    req<{ media: FlowMedia[] }>(`/flow-projects/${flowId}/media`),
  allFlowMedia: () =>
    req<{ media: AllMediaItem[]; projects: number }>(`/library/all-media`),
  importMedia: (pid: string, body: { media_id: string; name?: string; type?: string }) =>
    req<Entity>(`/projects/${pid}/entities/import-media`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

export interface FlowMedia {
  media_id: string;
  name: string;
  kind: string;
}

export interface AllMediaItem extends FlowMedia {
  project_title: string;
  flow_project_id: string;
}

export interface LibraryEntity extends Entity {
  project_title: string;
}

export interface Entity {
  id: string;
  project_id: string;
  type: "character" | "location" | "prop";
  name: string;
  description: string | null;
  ref_prompt: string | null;
  media_id: string | null;
  image_path: string | null;
}

export interface Shot {
  id: string;
  scene_id: string;
  idx: number;
  title: string;
  description: string | null;
  ref_entity_ids: string | null;
  image_media_id: string | null;
  image_path: string | null;
  video_path: string | null;
  visual_prompt: string | null;
  motion_prompt: string | null;
  video_model: string | null;
  duration: number;
  status: string;
  // Storytelling (§2.6): this beat's spoken slice + its share of the scene audio.
  narrator_text?: string | null;
  narration_duration?: number | null;
  start_time?: number | null;
}

export const storyboard = {
  sceneShots: (sid: string) => req<{ shots: Shot[] }>(`/scenes/${sid}/shots`),
  // Storytelling (§2.6): build beats + TTS for ONE scene (re-run a scene the project-wide
  // pass missed) as a background job (§9) so the UI doesn't block on slow TTS.
  buildSceneBeats: (sid: string, measure = true) =>
    req<{ job_id: string; total: number }>(
      `/scenes/${sid}/beats-job`,
      { method: "POST", body: JSON.stringify({ measure }) }
    ),
  // Vary camera angles of existing shots (rewrites description/visual/motion only — keeps
  // narration & audio, no TTS). Background job (§9). Then regenerate images.
  revaryScene: (sid: string) =>
    req<{ job_id: string; total: number }>(`/scenes/${sid}/revary-job`, { method: "POST" }),
  revaryProject: (pid: string) =>
    req<{ job_id: string; total: number }>(`/projects/${pid}/revary`, { method: "POST" }),
  autofill: (sid: string, n_frames?: number) =>
    req<{ shots: Shot[] }>(`/scenes/${sid}/storyboard/autofill`, {
      method: "POST",
      body: JSON.stringify({ n_frames: n_frames ?? null }),
    }),
  // force=true rebuilds shots even for scenes that already have them (deletes & re-splits).
  autofillAll: (pid: string, n_frames?: number, force = false) =>
    req<any>(`/projects/${pid}/storyboard/autofill-all${force ? "?force=true" : ""}`, {
      method: "POST",
      body: JSON.stringify({ n_frames: n_frames ?? null }),
    }),
  // Storytelling (§2.6): TTS each scene as one continuous read, then map beats onto it.
  // measure=true uses real TTS durations (needs OmniVoice up); false estimates from words.
  buildBeats: (pid: string, language = "Vietnamese", measure = true) =>
    req<{ job_id: string; total: number }>(
      `/projects/${pid}/voiceover`,
      { method: "POST", body: JSON.stringify({ language, measure }) }
    ),
  addShot: (sid: string) => req<Shot>(`/scenes/${sid}/shots`, { method: "POST" }),
  insertShot: (sid: string) => req<Shot>(`/shots/${sid}/insert`, { method: "POST" }),
  reorderShots: (sid: string, order: string[]) =>
    req<{ shots: Shot[] }>(`/scenes/${sid}/shots/reorder`, {
      method: "POST",
      body: JSON.stringify({ order }),
    }),
  reorderScenes: (pid: string, order: string[]) =>
    req<{ scenes: Scene[] }>(`/projects/${pid}/scenes/reorder`, {
      method: "POST",
      body: JSON.stringify({ order }),
    }),
  updateShot: (sid: string, body: Partial<Omit<Shot, "ref_entity_ids">> & { ref_entity_ids?: string[] }) =>
    req<Shot>(`/shots/${sid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteShot: (sid: string) => req<{ ok: boolean }>(`/shots/${sid}`, { method: "DELETE" }),
  genImage: (sid: string) => req<Shot>(`/shots/${sid}/image`, { method: "POST" }),
  genSceneAll: (sid: string) =>
    req<{ job_id: string; total: number }>(`/scenes/${sid}/storyboard/generate-all`, { method: "POST" }),
  genProjectAll: (pid: string) =>
    req<{ job_id: string; total: number }>(`/projects/${pid}/storyboard/generate-all`, { method: "POST" }),
};

export const shots = {
  genPrompts: (sid: string) => req<Shot>(`/shots/${sid}/prompts`, { method: "POST" }),
  genVideo: (sid: string) => req<Shot>(`/shots/${sid}/video`, { method: "POST" }),
  upscale: (sid: string) => req<Shot>(`/shots/${sid}/upscale`, { method: "POST" }),
  genAllVideos: (pid: string) =>
    req<{ job_id: string; total: number }>(`/projects/${pid}/shots/generate-all`, { method: "POST" }),
  narration: (sid: string, language = "Vietnamese") =>
    req<Shot>(`/shots/${sid}/narration`, { method: "POST", body: JSON.stringify({ language }) }),
};

// `goal` distinguishes a shot's two graphs: "video" (shots tab) vs "image" (storyboard).
const graphUrl = (
  kind: "shot" | "entity",
  id: string,
  suffix: "" | "/run",
  goal?: "image" | "video"
) => {
  const url = `/${kind === "shot" ? "shots" : "entities"}/${id}/graph${suffix}`;
  return goal === "video" ? `${url}?goal=video` : url;
};

export const graphApi = {
  get: (kind: "shot" | "entity", id: string, goal?: "image" | "video") =>
    req<{ graph: any }>(graphUrl(kind, id, "", goal)),
  run: (
    kind: "shot" | "entity",
    id: string,
    graph: any,
    goal?: "image" | "video",
    onlyNode?: string
  ) =>
    req<any>(graphUrl(kind, id, "/run", goal), {
      method: "POST",
      body: JSON.stringify({ graph, only_node: onlyNode }),
    }),
  save: (kind: "shot" | "entity", id: string, graph: any, goal?: "image" | "video") =>
    req<any>(graphUrl(kind, id, "", goal), {
      method: "PUT",
      body: JSON.stringify({ graph }),
    }),
  // Commit a media (e.g. a per-node quick-gen result) to the shot/entity.
  applyMedia: (kind: "shot" | "entity", id: string, media_id: string, ext = "png") =>
    req<any>(`/${kind === "shot" ? "shots" : "entities"}/${id}/apply-media`, {
      method: "POST",
      body: JSON.stringify({ media_id, ext }),
    }),
};

export const assemble = {
  build: (pid: string) =>
    req<{ web_path: string; clips: number; duration: number }>(
      `/projects/${pid}/assemble`,
      { method: "POST" }
    ),
  buildFromImages: (pid: string, kenBurns = true) =>
    req<{ web_path: string; clips: number; duration: number; mode: string }>(
      `/projects/${pid}/assemble-images?ken_burns=${kenBurns}`,
      { method: "POST" }
    ),
  exportSeo: (pid: string) =>
    req<{ metadata: any; srt: string; thumbnail: string | null }>(
      `/projects/${pid}/export`,
      { method: "POST" }
    ),
  davinci: (pid: string) =>
    req<{ web_path: string; clips: number; captions_srt: string | null; captions: number }>(`/projects/${pid}/export/davinci-xml`, {
      method: "POST",
    }),
};

export interface Scene {
  id: string;
  idx: number;
  heading: string;
  action: string;
  // Storytelling: the scene's measured TTS narration (null = not built / estimate-only).
  narration_path?: string | null;
  narration_duration?: number | null;
  narration_text?: string | null;
}

export interface ScriptResult {
  script: string;
  scenes: Scene[];
  estimated_duration?: number;
}

// Thumbnail URL for a Flow media key (backend caches locally).
export const thumbUrl = (key: string) => `/api/studio/thumb/${key}`;

// Direct URL to download a project backup (.zip: DB rows + media).
export const projectExportUrl = (pid: string) =>
  `/api/studio/projects/${pid}/export-zip`;

// Direct URL to download all storyboard images of a project as a .zip.
export const storyboardExportUrl = (pid: string) =>
  `/api/studio/projects/${pid}/storyboard/export`;

// OmniVoice base URL config lives on the tts router (not /studio).
export async function getTtsConfig(): Promise<{ base_url: string }> {
  const res = await fetch("/api/tts/config");
  if (!res.ok) throw new Error("Không đọc được OmniVoice URL");
  return res.json();
}

export async function setTtsConfig(base_url: string): Promise<any> {
  const res = await fetch("/api/tts/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base_url }),
  });
  if (!res.ok) throw new Error("Không đặt được OmniVoice URL");
  return res.json();
}

// ─── Voices (OmniVoice TTS) ──────────────────────────────────
export interface Voice {
  voice_id: number;
  title: string;
  desciption?: string; // OmniVoice spelling
}

async function ttsReq<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/tts${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

/** Normalize OmniVoice's list response (array or {voices:[…]}, varied key names). */
export async function listVoices(): Promise<Voice[]> {
  const raw = await ttsReq<any>("/voices");
  const arr: any[] = Array.isArray(raw) ? raw : raw?.voices || raw?.data || [];
  return arr.map((v, i) => ({
    voice_id: Number(v.voice_id ?? v.id ?? v.index ?? i),
    title: String(v.title ?? v.name ?? v.voice ?? `Voice ${v.voice_id ?? i}`),
    desciption: v.desciption ?? v.description ?? "",
  }));
}

export const addVoice = (voice: string, title: string, desciption?: string) =>
  ttsReq<any>("/voices", {
    method: "POST",
    body: JSON.stringify({ voice, title, desciption }),
  });

export const removeVoice = (voice_id: number) =>
  ttsReq<any>("/voices/remove", {
    method: "POST",
    body: JSON.stringify({ voice_id }),
  });

/** Synthesize speech → returns base64 audio (WAV). */
export const synthesize = (text: string, voice_id = 0, speed = 1.0) =>
  ttsReq<{ audio: string; status?: string; msg?: string }>("/synthesize", {
    method: "POST",
    body: JSON.stringify({ text, voice_id, speed }),
  });

/** Read a File as a bare base64 string (no data: prefix) for voice upload. */
export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",").pop() || "");
    r.onerror = () => reject(new Error("Không đọc được file"));
    r.readAsDataURL(file);
  });
}

/** base64 (WAV) → playable object URL. */
export function base64ToAudioUrl(b64: string, mime = "audio/wav"): string {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: mime }));
}
