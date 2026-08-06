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
  // Số frame storyboard tối đa gộp vào MỘT clip video (⚙ Cấu hình dự án, trần HARD_MAX_CLIP_FRAMES).
  clip_frames?: number | null;
  // Số panel trên MỘT trang storyboard (tab Storyboard). Chỉ 4 (lưới 2x2) hoặc 6 (3x2).
  sheet_panels?: number | null;
  tts_speed?: number | null;
  tts_gap?: number | null;
  tts_sentence_gap?: number | null;
  tts_edge_pad?: number | null;
  seed?: number | null;
  prompt_header?: string | null;
  prompt_footer?: string | null;
  culture_hint?: string | null;
  script_lang?: string | null;
  image_text_lang?: string | null;
  bgm_path?: string | null;
  bgm_volume?: number | null;
  bgm_duck?: number | null;
  // Tự tải thêm bản ảnh 2K/4K (theo tier) sau mỗi lần sinh ảnh storyboard.
  auto_hires?: number | null;
  // Tự upscale video (1080p/4K theo tier) sau mỗi lần render.
  auto_upscale_video?: number | null;
  // Mức upscale mong muốn; rỗng = kịch trần tier. Tier TWO chọn 1080p cho nhẹ/rẻ hơn 4K.
  upscale_res?: string | null;
  status: string;
  updated_at: number;
}

// Hệ quả của một lần lưu/sửa kịch bản. Scene được ĐỐI CHIẾU (giữ id → shot sống sót), nên
// cần nói rõ cái gì đã đổi: body_changed = scene còn đó nhưng nội dung khác ⇒ storyboard và
// lời đọc của nó đã cũ; removed/shots_removed = scene biến mất khỏi kịch bản, shot mất theo.
export interface ScriptChanges {
  kept: number;
  added: number;
  removed: number;
  shots_removed: number;
  body_changed: string[];
}

export interface Candidate {
  media_id: string;
  primary_media_id: string;
  workflow_id?: string | null;
  web: string;
}

// ─── Flow Music (flowmusic.app) ──────────────────────────────
export interface MusicSong {
  clip_id: string;
  operation_id?: string | null;
  title: string | null;
  audio_url: string;
  wav_url?: string | null;
  image_url?: string | null;
  duration_s: number | null;
  lyrics?: string | null;
}

export interface MusicConversation {
  id: string;
  created_at: string;
  title: string;
  last_message_at: string;
}

/** Một bài trong playlist nhạc của dự án (chế độ music video). */
export interface MusicTrack {
  id: string;
  project_id: string;
  idx: number;
  title: string;
  path: string;
  duration: number;
  source: string; // flowmusic | upload | copy
  audio_url: string | null;
  /** /studio-media/... — phát trực tiếp trong trình duyệt. */
  web_path: string | null;
}

/** Một bài nhạc ĐÃ TẢI VỀ trong kho studio (của bất kỳ dự án nào) — nguồn để dùng lại. */
export interface LibraryMusic {
  /** "bgm" = bài trộn chìm của một dự án; "track" = bài trong playlist music video. */
  kind: "bgm" | "track";
  project_id: string;
  project_title: string;
  title: string;
  /** Đường dẫn tuyệt đối — gửi lại nguyên si khi copy. */
  path: string;
  web_path: string | null;
  duration: number | null;
}

/** Playlist + đối chiếu thời lượng nhạc với thời lượng hình. */
export interface MusicStatus {
  tracks: MusicTrack[];
  gap: number;
  music_mode: boolean;
  music_duration: number;
  video_duration: number;
  video_measured: boolean;
  /** Hình còn thiếu bao nhiêu giây so với nhạc (sẽ lặp hình để bù khi ghép). */
  shortfall: number;
}

export type GenerateTrackResult =
  | (MusicStatus & { generated: MusicSong; conversation_id: string | null })
  | { pending_selection: true; conversation_id: string | null; songs: MusicSong[] };

export type GenerateBgmResult =
  | (Project & { generated: MusicSong; conversation_id: string | null })
  | { pending_selection: true; conversation_id: string | null; songs: MusicSong[] };

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
  /** Tài khoản Google đang đăng nhập Flow; null = chưa xác định được. */
  account: FlowAccount | null;
}

/** Tài khoản Flow — chủ sở hữu của project + media sinh ra dưới nó. */
export interface FlowAccount {
  id: string;
  email: string | null;
  name: string | null;
  picture: string | null;
  paygate_tier?: string | null;
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
  listProjects: () => req<{ projects: Project[]; account: string | null }>("/projects"),
  accounts: () =>
    req<{
      current: FlowAccount | null;
      accounts: (FlowAccount & { projects: number })[];
      unowned_projects: number;
    }>("/accounts"),
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
  // Chép nhạc nền từ dự án khác (preset thiết lập mang theo bgm_path của dự án nguồn).
  copyBgm: (id: string, source: string, volume?: number) =>
    req<Project>(`/projects/${id}/bgm/copy`, {
      method: "POST",
      body: JSON.stringify({ source, volume }),
    }),
  // Sinh nhạc nền bằng Flow Music. Ra đúng 1 bản → server tự set làm bgm luôn (trả Project
  // đầy đủ + `generated`); ra 2 bản (A/B) → server KHÔNG tự chọn, trả `pending_selection`
  // kèm cả 2 để nghe thử rồi gọi `selectBgm` với bản đã ưng.
  generateBgm: (id: string, prompt: string, conversationId?: string | null, volume?: number) =>
    req<GenerateBgmResult>(`/projects/${id}/bgm/generate`, {
      method: "POST",
      body: JSON.stringify({ prompt, conversation_id: conversationId ?? null, volume }),
    }),
  // ── Playlist nhạc (music video) ────────────────────────────
  // Nhiều bài phát nối tiếp, cách nhau `gap` giây; tổng thời lượng playlist quyết định độ dài
  // video (hình được lặp cho phủ kín khi ghép). Khác hẳn bgm ở trên — một bài chìm dưới lời đọc.
  musicStatus: (id: string) => req<MusicStatus>(`/projects/${id}/music`),
  musicSettings: (id: string, body: { music_mode?: boolean; gap?: number }) =>
    req<MusicStatus>(`/projects/${id}/music/settings`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  uploadTrack: async (id: string, file: File, title?: string): Promise<MusicStatus> => {
    const fd = new FormData();
    fd.append("file", file);
    if (title) fd.append("title", title);
    const res = await fetch(`/api/studio/projects/${id}/music/upload`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  },
  // Sinh 1 bài bằng Flow Music rồi thêm vào playlist. Ra 2 bản A/B → `pending_selection`,
  // nghe thử rồi gọi `addTrack` với bản đã ưng.
  generateTrack: (id: string, prompt: string, conversationId?: string | null) =>
    req<GenerateTrackResult>(`/projects/${id}/music/generate`, {
      method: "POST",
      body: JSON.stringify({ prompt, conversation_id: conversationId ?? null }),
    }),
  addTrack: (id: string, audioUrl: string, title?: string | null) =>
    req<MusicStatus>(`/projects/${id}/music/add`, {
      method: "POST",
      body: JSON.stringify({ audio_url: audioUrl, title }),
    }),
  // Nhạc đã tải về của MỌI dự án — dùng lại thay vì sinh/tải lại (mất 30–70s + 1 lượt).
  libraryMusic: () => req<{ music: LibraryMusic[] }>("/library/music"),
  // Chép 1 bài trong kho studio vào playlist dự án này (bản sao riêng, xoá không ảnh hưởng nhau).
  copyTrack: (id: string, source: string, title?: string | null) =>
    req<MusicStatus>(`/projects/${id}/music/copy`, {
      method: "POST",
      body: JSON.stringify({ source, title }),
    }),
  reorderTracks: (id: string, ids: string[]) =>
    req<MusicStatus>(`/projects/${id}/music/reorder`, {
      method: "POST",
      body: JSON.stringify({ ids }),
    }),
  renameTrack: (tid: string, title: string) =>
    req<MusicStatus>(`/music-tracks/${tid}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteTrack: (tid: string) => req<MusicStatus>(`/music-tracks/${tid}`, { method: "DELETE" }),

  // Áp 1 bài đã biết audio_url (1 trong 2 bản A/B, hoặc bài cũ trong "Bài đã tạo") làm bgm.
  selectBgm: (id: string, audioUrl: string, volume?: number) =>
    req<Project>(`/projects/${id}/bgm/select`, {
      method: "POST",
      body: JSON.stringify({ audio_url: audioUrl, volume }),
    }),
  // Every image in the project's Flow project (for the Node Editor "Nguồn ảnh" picker).
  projectImages: (id: string) => req<{ media: FlowMedia[] }>(`/projects/${id}/images`),
  // Upload an image from the user's machine → Flow (gets a media_id) + local cache.
  uploadImage: async (
    id: string,
    file: File
  ): Promise<{ media_id: string; web: string; name: string }> => {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/studio/projects/${id}/upload-image`, { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    return res.json();
  },
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
  // `ref_media` đi lên dưới dạng MẢNG (server tự json hoá), khác cột chuỗi trong Entity.
  updateEntity: (
    eid: string,
    body: Partial<Omit<Entity, "ref_media">> & { ref_media?: EntityRefMedia[] }
  ) => req<Entity>(`/entities/${eid}`, { method: "PATCH", body: JSON.stringify(body) }),
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
  syncProjectMedia: (pid: string) =>
    req<SyncMediaResult>(`/projects/${pid}/sync-media`, { method: "POST" }),
  // Saved project-settings presets (server-side, reusable across projects).
  listSettingsPresets: () => req<{ presets: SettingsPreset[] }>(`/settings-presets`),
  saveSettingsPreset: (name: string, settings: Record<string, any>) =>
    req<{ presets: SettingsPreset[] }>(`/settings-presets`, {
      method: "POST",
      body: JSON.stringify({ name, settings }),
    }),
  deleteSettingsPreset: (id: string) =>
    req<{ presets: SettingsPreset[] }>(`/settings-presets/${id}`, { method: "DELETE" }),
};

export interface SettingsPreset {
  id: string;
  name: string;
  settings: Record<string, any>;
  created_at?: string;
}

export interface SyncMediaResult {
  flow_media: number;
  total_removed: number;
  removed: {
    entities: string[];
    shot_images: string[];
    shot_videos: string[];
    extra_views: number;
    history: number;
  };
}

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
  // Location entities: JSON list of extra angle views ({media_id, primary_media_id, path}).
  extra_media?: string | null;
  // Ảnh MẪU (đầu VÀO) để ✦ bám theo khi sinh ảnh — JSON list [{media_id, name?}]. Khác
  // `media_id` (ảnh kết quả) và `extra_media` (góc phụ sinh thêm của location).
  ref_media?: string | null;
}

/** Một ảnh mẫu đính vào entity. `name` trở thành handle `{name}` bind trong prompt. */
export interface EntityRefMedia {
  media_id: string;
  name?: string;
}

export const parseRefMedia = (s: string | null | undefined): EntityRefMedia[] => {
  try {
    const v = JSON.parse(s || "[]");
    return Array.isArray(v) ? v.filter((m) => m && m.media_id) : [];
  } catch {
    return [];
  }
};

export interface Shot {
  id: string;
  scene_id: string;
  idx: number;
  title: string;
  description: string | null;
  ref_entity_ids: string | null;
  image_media_id: string | null;
  image_path: string | null;
  // Bản 2K/4K tải qua upsampleImage (chỉ dùng khi dựng video / export DaVinci — app vẫn
  // hiển thị image_path cho nhẹ). image_hires_media_id ≠ image_media_id ⇒ bản cũ, phải tải lại.
  image_hires_path?: string | null;
  image_hires_media_id?: string | null;
  image_hires_res?: string | null;
  video_media_id?: string | null;
  video_path: string | null;
  // Bản upscale 1080p/4K của video. upscale_media_id ≠ video_media_id ⇒ bản cũ.
  upscale_path?: string | null;
  upscale_media_id?: string | null;
  upscale_res?: string | null;
  visual_prompt: string | null;
  motion_prompt: string | null;
  video_model: string | null;
  duration: number;
  status: string;
  // Storytelling (§2.6): this beat's spoken slice + its share of the scene audio.
  narrator_text?: string | null;
  narration_duration?: number | null;
  start_time?: number | null;
  // Tên chuẩn "sc001-s01-mô-tả" — dùng chung ở DB, trên Flow và khi export.
  media_name?: string | null;
  // Một câu: frame này nối tiếp frame trước thế nào (chuỗi liên tục của storyboard).
  continuity?: string | null;
  // Gộp clip: các frame liền nhau cùng clip_id render thành MỘT video, nằm trên frame
  // clip_idx = 0. NULL = frame đứng một mình.
  clip_id?: string | null;
  clip_idx?: number | null;
}

export const storyboard = {
  sceneShots: (sid: string) => req<{ shots: Shot[] }>(`/scenes/${sid}/shots`),
  projectShots: (pid: string) => req<{ shots: Shot[] }>(`/projects/${pid}/shots`),
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
  // Content-align the source prose to scenes (force) so each scene reads the part that matches
  // its location — fixes narration landing in the wrong scene. Then rebuild "Dựng theo lời đọc".
  alignSource: (pid: string) =>
    req<{ scenes: Scene[] }>(`/projects/${pid}/align-source`, { method: "POST" }),
  // Split ONE over-long scene into ~90s sub-scenes (same location) so each gets a coherent shot
  // plan. Clears the scene's shots (rebuild after). Sub-scenes inherit the parent's location.
  splitScene: (sid: string) =>
    req<{ scenes: Scene[]; split_into: number }>(`/scenes/${sid}/split`, { method: "POST" }),
  // Re-TTS ONLY a scene's narration from its existing shots' narrator_text and re-time the
  // shots + captions — keeps images/prompts/refs. Use to apply new TTS settings (gap/edge-pad)
  // without re-generating images. 502 if TTS is down (old audio kept).
  rebuildSceneAudio: (sid: string) =>
    req<{ shots: Shot[]; scene_duration: number; narration_path: string | null; measured: boolean }>(
      `/scenes/${sid}/rebuild-audio`, { method: "POST" }),
  // Bulk version of rebuildSceneAudio: re-TTS + re-time EVERY scene that has shots, keeping
  // images. Runs as a background job (§9, type "audio") since TTS + alignment are slow.
  rebuildProjectAudio: (pid: string) =>
    req<{ job_id: string; total: number }>(
      `/projects/${pid}/rebuild-audio`, { method: "POST" }),
  updateShot: (sid: string, body: Partial<Omit<Shot, "ref_entity_ids">> & { ref_entity_ids?: string[] }) =>
    req<Shot>(`/shots/${sid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteShot: (sid: string) => req<{ ok: boolean }>(`/shots/${sid}`, { method: "DELETE" }),
  genImage: (sid: string) => req<Shot>(`/shots/${sid}/image`, { method: "POST" }),
  genSceneAll: (sid: string) =>
    req<{ job_id: string; total: number }>(`/scenes/${sid}/storyboard/generate-all`, { method: "POST" }),
  genProjectAll: (pid: string) =>
    req<{ job_id: string; total: number }>(`/projects/${pid}/storyboard/generate-all`, { method: "POST" }),
  // Ảnh 2K/4K (upsampleImage). Trần độ phân giải theo tier: ONE → 2K, TWO → 4K.
  hiresStatus: (pid: string) =>
    req<{ tier: string; resolution: string; label: string; total: number; done: number; missing: number }>(
      `/projects/${pid}/hires/status`),
  genHires: (sid: string, force = false) =>
    req<Shot>(`/shots/${sid}/hires?force=${force}`, { method: "POST" }),
  genProjectHires: (pid: string, force = false) =>
    req<{ job_id: string; total: number; resolution: string }>(
      `/projects/${pid}/hires/generate-all?force=${force}`, { method: "POST" }),
};

export const shots = {
  genPrompts: (sid: string) => req<Shot>(`/shots/${sid}/prompts`, { method: "POST" }),
  genVideo: (sid: string) => req<Shot>(`/shots/${sid}/video`, { method: "POST" }),
  // Bỏ trống resolution → server lấy theo tier (ONE → 1080p, TWO → 4K).
  upscale: (sid: string, force = false) =>
    req<Shot>(`/shots/${sid}/upscale?force=${force}`, { method: "POST" }),
  upscaleStatus: (pid: string) =>
    req<{ tier: string; resolution: string; label: string; total: number; done: number;
          missing: number; skipped_chained: number;
          choices: { value: string; label: string }[] }>(`/projects/${pid}/upscale/status`),
  upscaleAll: (pid: string, force = false) =>
    req<{ job_id: string; total: number; resolution: string }>(
      `/projects/${pid}/upscale/generate-all?force=${force}`, { method: "POST" }),
  genAllVideos: (pid: string) =>
    req<{ job_id: string; total: number }>(`/projects/${pid}/shots/generate-all`, { method: "POST" }),
  narration: (sid: string, language = "Vietnamese") =>
    req<Shot>(`/shots/${sid}/narration`, { method: "POST", body: JSON.stringify({ language }) }),
};

// Clip = nhóm frame storyboard liền nhau được render thành MỘT video. Vì thế số thẻ ở tab
// Shots ≠ số frame ở tab Storyboard.
//
// TRẦN CỨNG do model quy định (clip dài nhất chỉ 10s) — phải khớp `clips.HARD_MAX_CLIP_FRAMES`.
// Số thực dùng là `project.clip_frames`; đọc qua `framesPerClip()` để dự án cũ (chưa có cột)
// và giá trị rác đều rơi về mặc định thay vì làm vỡ cách gom.
export const HARD_MAX_CLIP_FRAMES = 6;

export const framesPerClip = (p: { clip_frames?: number | null } | null | undefined) =>
  Math.max(1, Math.min(HARD_MAX_CLIP_FRAMES, Number(p?.clip_frames) || HARD_MAX_CLIP_FRAMES));

export const clips = {
  autogroupProject: (pid: string, framesPerClip?: number) =>
    req<{ clips: number }>(`/projects/${pid}/clips/autogroup`, {
      method: "POST",
      body: JSON.stringify({ frames_per_clip: framesPerClip ?? null }),
    }),
  autogroupScene: (sid: string, framesPerClip?: number) =>
    req<{ clips: number; shots: Shot[] }>(`/scenes/${sid}/clips/autogroup`, {
      method: "POST",
      body: JSON.stringify({ frames_per_clip: framesPerClip ?? null }),
    }),
  group: (shotIds: string[]) =>
    req<{ shots: Shot[] }>("/clips/group", {
      method: "POST",
      body: JSON.stringify({ shot_ids: shotIds }),
    }),
  ungroup: (leadId: string) =>
    req<{ shots: Shot[] }>(`/clips/${leadId}/ungroup`, { method: "POST" }),
  // ✨ Viết prompt timeline đi xuyên các frame: "[00:00] mở ở (frame 1), máy lùi dần…"
  genPrompt: (leadId: string) => req<Shot>(`/clips/${leadId}/prompt`, { method: "POST" }),
  genVideo: (leadId: string) => req<Shot>(`/clips/${leadId}/video`, { method: "POST" }),
  genAll: (pid: string) =>
    req<{ job_id: string; total: number }>(`/projects/${pid}/clips/generate-all`, {
      method: "POST",
    }),
};

export type GraphKind = "shot" | "entity" | "sheet";

const graphBase = (kind: GraphKind) =>
  kind === "shot" ? "shots" : kind === "entity" ? "entities" : "sheets";

// `goal` distinguishes a shot's two graphs: "video" (shots tab) vs "image" (storyboard).
// Một TRANG storyboard chỉ có MỘT graph (ảnh trang) nên không nhận `goal`.
const graphUrl = (
  kind: GraphKind,
  id: string,
  suffix: "" | "/run",
  goal?: "image" | "video"
) => {
  const url = `/${graphBase(kind)}/${id}/graph${suffix}`;
  return goal === "video" && kind === "shot" ? `${url}?goal=video` : url;
};

export const graphApi = {
  get: (kind: GraphKind, id: string, goal?: "image" | "video") =>
    req<{ graph: any }>(graphUrl(kind, id, "", goal)),
  run: (
    kind: GraphKind,
    id: string,
    graph: any,
    goal?: "image" | "video",
    onlyNode?: string,
    propagate = false
  ) =>
    kind === "sheet"
      ? // Trang nhận only_node/propagate qua query string (body chỉ có `graph`).
        req<any>(
          `/sheets/${id}/graph/run` +
            (onlyNode
              ? `?only_node=${encodeURIComponent(onlyNode)}${propagate ? "&propagate=true" : ""}`
              : ""),
          { method: "POST", body: JSON.stringify({ graph }) }
        )
      : req<any>(graphUrl(kind, id, "/run", goal), {
          method: "POST",
          body: JSON.stringify({ graph, only_node: onlyNode, propagate }),
        }),
  save: (kind: GraphKind, id: string, graph: any, goal?: "image" | "video") =>
    req<any>(graphUrl(kind, id, "", goal), {
      method: "PUT",
      body: JSON.stringify({ graph }),
    }),
  // Commit a media (e.g. a per-node quick-gen result) to the shot/entity/sheet.
  applyMedia: (kind: GraphKind, id: string, media_id: string, ext = "png") =>
    req<any>(`/${graphBase(kind)}/${id}/apply-media`, {
      method: "POST",
      body: JSON.stringify({ media_id, ext }),
    }),
  // Reusable node-graph presets (templates), shared across shots/assets.
  listTemplates: () => req<{ templates: GraphTemplate[] }>(`/graph-templates`),
  saveTemplate: (name: string, graph: any, goal?: string) =>
    req<{ templates: GraphTemplate[] }>(`/graph-templates`, {
      method: "POST",
      body: JSON.stringify({ name, graph, goal }),
    }),
  deleteTemplate: (id: string) =>
    req<{ templates: GraphTemplate[] }>(`/graph-templates/${id}`, { method: "DELETE" }),
};

// ─── Tab Storyboard: TRANG 4/6 panel ────────────────────────
// Một trang = MỘT lượt sinh ảnh = MỘT clip video. Trang KHÔNG bị cắt: chính bức ảnh nguyên vẹn
// (badge số tròn + caption vẽ sẵn trong ảnh) là reference duy nhất đưa cho Omni Flash r2v.

export interface BoardPanel {
  id: string;
  sheet_id: string;
  idx: number;                  // 0..N-1, trái→phải, trên→dưới
  caption: string;              // dòng tiếng Việt in DƯỚI panel trong ảnh ("toàn cảnh"…)
  shot_size: string;
  lens: string;
  movement: string;
  description: string;
  continuity?: string | null;
  /** JSON list các entity id panel này tham chiếu — nguồn của node "Nguồn ảnh" trong Node Editor. */
  ref_entity_ids?: string | null;
}

/** Union entity id của mọi panel, giữ thứ tự (location của scene luôn đứng đầu). */
export const sheetRefEntityIds = (sh: { panels_list?: BoardPanel[] }): string[] => {
  const out: string[] = [];
  for (const p of sh.panels_list || []) {
    let ids: string[] = [];
    try {
      ids = JSON.parse(p.ref_entity_ids || "[]");
    } catch {
      ids = [];
    }
    for (const i of ids) if (i && !out.includes(i)) out.push(i);
  }
  return out;
};

export interface BoardSheet {
  id: string;
  scene_id: string;
  idx: number;
  title: string;
  prompt?: string | null;
  panels: number;               // 4 hoặc 6
  cols: number;
  rows: number;
  path?: string | null;         // ảnh trang
  media_id?: string | null;
  status?: string | null;
  motion_prompt?: string | null;
  video_path?: string | null;
  video_media_id?: string | null;
  duration?: number | null;
  scene_idx?: number;
  scene_heading?: string;
  panels_list: BoardPanel[];
}

export const boardApi = {
  listProject: (pid: string) => req<{ sheets: BoardSheet[] }>(`/projects/${pid}/sheets`),
  listScene: (sid: string) => req<{ sheets: BoardSheet[] }>(`/scenes/${sid}/sheets`),
  add: (sceneId: string) => req<BoardSheet>(`/scenes/${sceneId}/sheets`, { method: "POST" }),
  autofill: (sceneId: string, nSheets?: number) =>
    req<{ sheets: BoardSheet[] }>(
      `/scenes/${sceneId}/sheets/autofill${nSheets ? `?n_sheets=${nSheets}` : ""}`,
      { method: "POST" }),
  // Chia trang cho MỌI scene. force=false bỏ qua scene đã có trang (chia lại là xoá sạch
  // panel của scene đó, kể cả mô tả đã sửa tay).
  autofillAll: (pid: string, nSheets?: number, force = false) =>
    req<{ requested: number; done: number; skipped: number; errors: any[] }>(
      `/projects/${pid}/sheets/autofill-all?force=${force}` +
        (nSheets ? `&n_sheets=${nSheets}` : ""),
      { method: "POST" }),
  patchSheet: (id: string, body: { title?: string; prompt?: string; motion_prompt?: string }) =>
    req<BoardSheet>(`/sheets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  patchPanel: (id: string, body: Partial<Omit<BoardPanel, "id" | "sheet_id" | "idx">>) =>
    req<BoardPanel>(`/panels/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  remove: (id: string) => req<{ ok: boolean }>(`/sheets/${id}`, { method: "DELETE" }),
  generate: (id: string) => req<BoardSheet>(`/sheets/${id}/generate`, { method: "POST" }),
  generateAll: (pid: string, force = false) =>
    req<{ job_id: string; total: number }>(
      `/projects/${pid}/sheets/generate-all?force=${force}`, { method: "POST" }),
  // Prompt y hệt lúc gửi đi — xem trước để đối chiếu, không tốn credit.
  promptPreview: (id: string) =>
    req<{ prompt: string; references: string[]; cast: string[] }>(`/sheets/${id}/prompt-preview`),
  genPrompt: (id: string) => req<BoardSheet>(`/sheets/${id}/prompt`, { method: "POST" }),
  genVideo: (id: string) => req<BoardSheet>(`/sheets/${id}/video`, { method: "POST" }),
  genAllVideos: (pid: string, force = false) =>
    req<{ job_id: string; total: number }>(
      `/projects/${pid}/sheets/video/generate-all?force=${force}`, { method: "POST" }),
};

export interface GraphTemplate {
  id: string;
  name: string;
  goal?: string | null;
  graph: { nodes: any[]; edges: any[] };
  created_at?: string;
}

/** Cách khâu ghép khớp hình vào playlist nhạc (chỉ có khi dự án bật music_mode). */
export interface MusicFit {
  duration: number;        // độ dài video sau khi khớp
  target: number;          // độ dài playlist nhạc
  source_duration: number; // độ dài hình trước khi lặp
  loops: number;           // số vòng lặp thêm; 0 = hình vốn đã đủ dài
  reencoded: boolean;
  soundtrack: string;
}

export const assemble = {
  build: (pid: string) =>
    req<{ web_path: string; clips: number; duration: number; music: MusicFit | null }>(
      `/projects/${pid}/assemble`,
      { method: "POST" }
    ),
  buildFromImages: (pid: string, kenBurns = true) =>
    req<{
      web_path: string; clips: number; duration: number; mode: string;
      music: MusicFit | null;
    }>(
      `/projects/${pid}/assemble-images?ken_burns=${kenBurns}`,
      { method: "POST" }
    ),
  exportSeo: (pid: string) =>
    req<{ metadata: any; srt: string; thumbnail: string | null }>(
      `/projects/${pid}/export`,
      { method: "POST" }
    ),
  // mode "images" = timeline từ shot của tab Illustrators (hành vi cũ, mặc định).
  // mode "video"  = timeline chỉ gồm video của các trang storyboard + audio.
  davinci: (pid: string, mode: "images" | "video" = "images") =>
    req<{ web_path: string; mode: "images" | "video"; clips: number;
          captions_srt: string | null; captions: number; bgm: boolean;
          missing: number; missing_titles: string[] }>(
      `/projects/${pid}/export/davinci-xml?mode=${mode}`, { method: "POST" }),
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
  // Storytelling: this scene's content-aligned verbatim slice of the source prose.
  source_segment?: string | null;
}

export interface ScriptResult {
  script: string;
  scenes: Scene[];
  estimated_duration?: number;
  changes?: ScriptChanges;
}

// Thumbnail URL for a Flow media key (backend caches locally). Pass the studio
// project id so the backend serves an already-downloaded copy instead of hitting Flow.
export const thumbUrl = (key: string, pid?: string) =>
  pid ? `/api/studio/thumb/${key}?pid=${pid}` : `/api/studio/thumb/${key}`;

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

// ─── Flow Music (flowmusic.app) — /api/music/* ──────────────
async function musicReq<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/music${path}`, {
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

function _clipToSong(c: any): MusicSong {
  return {
    clip_id: c.id,
    operation_id: c.op_id,
    title: c.title,
    audio_url: c.audio_url,
    wav_url: c.wav_url,
    image_url: c.image_url,
    duration_s: c.duration?.value ? parseFloat(c.duration.value) : null,
    lyrics: c.lyrics?.value?.text,
  };
}

export const musicApi = {
  status: () =>
    musicReq<{ connected: boolean; music_key_present: boolean; account: any }>("/status"),
  conversations: (limit = 30, offset = 0) =>
    musicReq<MusicConversation[]>(`/conversations?limit=${limit}&offset=${offset}`),
  deleteConversation: (conversationId: string) =>
    musicReq<{ ok: boolean }>(`/conversations/${conversationId}`, { method: "DELETE" }),
  /** Bài hát (tool-return audio__create_song) bên trong 1 conversation cũ, kèm audio_url —
   *  cần 2 lượt gọi: đọc conversation lấy clip_id, rồi /clips lấy chi tiết. */
  conversationSongs: async (conversationId: string): Promise<MusicSong[]> => {
    const convo = await musicReq<any>(`/conversations/${conversationId}`);
    const clipIds: string[] = [];
    for (const m of convo.messages || []) {
      for (const part of m.parts || []) {
        if (part.part_kind === "tool-return" && part.tool_name === "audio__create_song") {
          const c = part.content || {};
          if (c.status === "success") {
            if (c.clip_id) clipIds.push(c.clip_id);
            if (c.clip_id_b) clipIds.push(c.clip_id_b);
          }
        }
      }
    }
    if (!clipIds.length) return [];
    const res = await musicReq<{ clips: Record<string, any> }>("/clips", {
      method: "POST",
      body: JSON.stringify({ clip_ids: clipIds }),
    });
    return clipIds.map((id) => res.clips?.[id]).filter(Boolean).map(_clipToSong);
  },
};
