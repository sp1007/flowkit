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
  prompt_header?: string | null;
  prompt_footer?: string | null;
  culture_hint?: string | null;
  status: string;
  updated_at: number;
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
  extractEntities: (id: string) =>
    req<{ added: number; entities: Entity[] }>(`/projects/${id}/entities/extract`, {
      method: "POST",
    }),
  addEntity: (id: string, body: Partial<Entity>) =>
    req<Entity>(`/projects/${id}/entities`, { method: "POST", body: JSON.stringify(body) }),
  updateEntity: (eid: string, body: Partial<Entity>) =>
    req<Entity>(`/entities/${eid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteEntity: (eid: string) =>
    req<{ ok: boolean }>(`/entities/${eid}`, { method: "DELETE" }),
  generateEntity: (eid: string) =>
    req<Entity>(`/entities/${eid}/generate`, { method: "POST" }),
  setEntityImage: (eid: string, media_id: string) =>
    req<Entity>(`/entities/${eid}/image`, { method: "PUT", body: JSON.stringify({ media_id }) }),
  generateAllAssets: (id: string) =>
    req<{ requested: number; done: number; errors: any[] }>(
      `/projects/${id}/assets/generate-all`,
      { method: "POST" }
    ),
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
}

export const storyboard = {
  sceneShots: (sid: string) => req<{ shots: Shot[] }>(`/scenes/${sid}/shots`),
  autofill: (sid: string, n_frames?: number) =>
    req<{ shots: Shot[] }>(`/scenes/${sid}/storyboard/autofill`, {
      method: "POST",
      body: JSON.stringify({ n_frames: n_frames ?? null }),
    }),
  autofillAll: (pid: string, n_frames?: number) =>
    req<any>(`/projects/${pid}/storyboard/autofill-all`, {
      method: "POST",
      body: JSON.stringify({ n_frames: n_frames ?? null }),
    }),
  addShot: (sid: string) => req<Shot>(`/scenes/${sid}/shots`, { method: "POST" }),
  insertShot: (sid: string) => req<Shot>(`/shots/${sid}/insert`, { method: "POST" }),
  updateShot: (sid: string, body: Partial<Omit<Shot, "ref_entity_ids">> & { ref_entity_ids?: string[] }) =>
    req<Shot>(`/shots/${sid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteShot: (sid: string) => req<{ ok: boolean }>(`/shots/${sid}`, { method: "DELETE" }),
  genImage: (sid: string) => req<Shot>(`/shots/${sid}/image`, { method: "POST" }),
  genSceneAll: (sid: string) =>
    req<any>(`/scenes/${sid}/storyboard/generate-all`, { method: "POST" }),
  genProjectAll: (pid: string) =>
    req<any>(`/projects/${pid}/storyboard/generate-all`, { method: "POST" }),
};

export const shots = {
  genPrompts: (sid: string) => req<Shot>(`/shots/${sid}/prompts`, { method: "POST" }),
  genVideo: (sid: string) => req<Shot>(`/shots/${sid}/video`, { method: "POST" }),
  upscale: (sid: string) => req<Shot>(`/shots/${sid}/upscale`, { method: "POST" }),
  genAllVideos: (pid: string) =>
    req<any>(`/projects/${pid}/shots/generate-all`, { method: "POST" }),
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
    req<{ web_path: string; clips: number }>(`/projects/${pid}/export/davinci-xml`, {
      method: "POST",
    }),
};

export interface Scene {
  id: string;
  idx: number;
  heading: string;
  action: string;
}

export interface ScriptResult {
  script: string;
  scenes: Scene[];
  estimated_duration?: number;
}

// Thumbnail URL for a Flow media key (backend caches locally).
export const thumbUrl = (key: string) => `/api/studio/thumb/${key}`;

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
