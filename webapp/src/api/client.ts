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
};

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
  addShot: (sid: string) => req<Shot>(`/scenes/${sid}/shots`, { method: "POST" }),
  insertShot: (sid: string) => req<Shot>(`/shots/${sid}/insert`, { method: "POST" }),
  updateShot: (sid: string, body: Partial<Shot> & { ref_entity_ids?: string[] }) =>
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

export const assemble = {
  build: (pid: string) =>
    req<{ web_path: string; clips: number; duration: number }>(
      `/projects/${pid}/assemble`,
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
