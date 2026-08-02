import { useEffect, useState } from "react";
import {
  api,
  storyboard,
  shots as shotsApi,
  clips as clipsApi,
  framesPerClip,
  type Project,
  type Scene,
  type Shot,
} from "../../api/client";
import type { EditorTarget } from "../nodeeditor/NodeEditor";
import MediaCard from "../common/MediaCard";
import Lightbox from "../common/Lightbox";
import { useConfirm } from "../common/Confirm";
import { creditGuard, CREDIT_COST } from "../../lib/credits";
import { downloadFile, slugName, pad3 } from "../../lib/download";
import { useJobs, useJobWatcher } from "../../jobs/JobsContext";

// Một CLIP = nhóm frame storyboard liền nhau được render thành MỘT video (tối đa `nMax` =
// project.clip_frames). Frame không gộp là clip một frame. Vì thế số thẻ ở tab này KHÁC số
// frame bên Storyboard. Quy tắc phải khớp `agent/studio/clips.py`.
const groupClips = (list: Shot[], nMax: number): Shot[][] => {
  const out: Shot[][] = [];
  let cur: Shot[] = [];
  for (const s of list) {
    const last = cur[cur.length - 1];
    if (last && s.clip_id && last.clip_id === s.clip_id && cur.length < nMax) {
      cur.push(s);
      continue;
    }
    if (cur.length) out.push(cur);
    cur = [s];
  }
  if (cur.length) out.push(cur);
  return out;
};

const clipLabel = (group: Shot[]) => {
  const first = group[0];
  if (group.length === 1) return first.title;
  const last = group[group.length - 1];
  return `S${pad3(first.idx).slice(1)}–S${pad3(last.idx).slice(1)} · ${group.length} frame`;
};

// Which file a shot's ⬇ actually saves, and what to call it. The upscale is a SEPARATE file
// (<media_id>_upsampled.mp4) and is only the right one while it still belongs to the current
// video — re-rendering the shot leaves a stale upscale behind, which `upscale_media_id` catches.
const videoDownload = (sh: Shot, sceneIdx: number) => {
  const upscaled = !!sh.upscale_path && sh.upscale_media_id === sh.video_media_id;
  const url = (upscaled ? sh.upscale_path : sh.video_path) || null;
  if (!url) return null;
  const res = upscaled ? sh.upscale_res?.split("_").pop()?.toLowerCase() : "";
  // media_name là tên đã dùng trên Flow + khi export ảnh — giữ nguyên để ba nơi khớp nhau.
  const stem = sh.media_name || `sc${pad3(sceneIdx)}-s${pad3(sh.idx)}-${slugName(sh.title || sh.description || "")}`;
  return {
    url,
    name: `${stem}${res ? `-${res}` : ""}.mp4`,
    title: upscaled ? `Tải video ${res} (bản upscale)` : "Tải video (bản HD)",
  };
};

const parseRefs = (s: string | null): string[] => {
  try {
    return JSON.parse(s || "[]");
  } catch {
    return [];
  }
};

export default function ShotsTab({
  project,
  onEdit,
}: {
  project: Project;
  onEdit?: (t: EditorTarget) => void;
}) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [byScene, setByScene] = useState<Record<string, Shot[]>>({});
  const [sel, setSel] = useState<Shot | null>(null);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<Shot | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Các frame đang được tick để gộp thủ công (chỉ trong MỘT scene tại một thời điểm).
  const [picked, setPicked] = useState<string[]>([]);
  const confirm = useConfirm();
  const { jobFor } = useJobs();
  // Số frame tối đa mỗi clip — đổi ở ⚙ Cấu hình dự án. Hạ xuống là các nhóm đang có tự tách ra
  // theo (cả ở đây lẫn server), không cần gom lại bằng tay.
  const nMax = framesPerClip(project);

  const loadShots = async (sid: string) => {
    const r = await storyboard.sceneShots(sid);
    setByScene((m) => ({ ...m, [sid]: r.shots }));
  };

  const reloadAllShots = async () => {
    const sc = scenes.length ? scenes : (await api.listScenes(project.id)).scenes;
    for (const s of sc) await loadShots(s.id);
  };

  useEffect(() => {
    (async () => {
      const sc = (await api.listScenes(project.id)).scenes;
      setScenes(sc);
      for (const s of sc) await loadShots(s.id);
    })().catch((e) => setErr(e.message));
  }, [project.id]);

  // Refetch shots as the server video batch advances (§9) — videos fill in live.
  const videoJob = jobFor("videos");
  useJobWatcher("videos", {
    onAdvance: reloadAllShots,
    onDone: (j) => {
      reloadAllShots();
      if (j.errors.length) setErr(`Auto gen video: ${j.done}/${j.total} xong, ${j.errors.length} lỗi.`);
    },
  });

  const setShot = (u: Shot) => {
    setByScene((m) => ({
      ...m,
      [u.scene_id]: (m[u.scene_id] || []).map((x) => (x.id === u.id ? u : x)),
    }));
    if (sel?.id === u.id) setSel(u);
  };

  const mark = (id: string, on: boolean) =>
    setRunning((s) => {
      const n = new Set(s);
      on ? n.add(id) : n.delete(id);
      return n;
    });

  // Render MỘT clip. Nhóm nhiều frame đi qua /clips/{lead}/video (Omni Flash, mỗi frame là một
  // reference mang tên `{sc001-s01-…}` của chính nó); clip một frame vẫn dùng đường cũ.
  const genClip = async (group: Shot[]): Promise<boolean> => {
    const lead = group[0];
    const noImage = group.filter((s) => !s.image_path);
    if (noImage.length) {
      setErr(`${noImage.length}/${group.length} frame chưa có ảnh — tạo ở Storyboard trước`);
      return false;
    }
    mark(lead.id, true);
    setErr(null);
    try {
      setShot(group.length > 1 ? await clipsApi.genVideo(lead.id) : await shotsApi.genVideo(lead.id));
      return true;
    } catch (e: any) {
      setErr(e.message);
      return false;
    } finally {
      mark(lead.id, false);
    }
  };

  // Render all clips (every frame has an image, no video yet) as a server-side background job
  // (§9): survives tab close, throttled + verified server-side, streams to the banner.
  const genAll = async () => {
    const groups = scenes.flatMap((sc) => groupClips(byScene[sc.id] || [], nMax));
    const todo = groups.filter((g) => g.every((s) => s.image_media_id) && !g[0].video_path);
    if (!todo.length) {
      setErr("Không có clip nào (đủ ảnh, chưa có video) để render.");
      return;
    }
    if (!(await creditGuard(confirm, todo.length, CREDIT_COST.video, "Render video"))) return;
    setErr(null);
    try {
      await clipsApi.genAll(project.id);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  // Gộp tự động toàn dự án: các frame liền nhau trong cùng scene được xếp vào clip ≤ nMax frame.
  // Frame đã có lời đọc đo được (kể chuyện) tự chiếm trọn một clip nên không bị gộp nhầm.
  const autogroup = async () => {
    if (
      !(await confirm({
        title: "Gộp frame thành clip?",
        message:
          `Các frame liền nhau trong cùng scene sẽ được xếp vào clip tối đa ${nMax} frame — ` +
          "mỗi clip render MỘT lần, model tự dựng đoạn chuyển tiếp giữa các frame. Cách gộp cũ bị ghi đè.",
        confirmText: "Gộp",
      }))
    )
      return;
    setBusy(true);
    setProgress("Đang gộp clip…");
    try {
      const r = await clipsApi.autogroupProject(project.id);
      await reloadAllShots();
      setProgress(null);
      setErr(null);
      if (!r.clips) setErr("Không gộp được clip nào — mỗi frame đã tự chiếm trọn thời lượng.");
    } catch (e: any) {
      setErr(e.message);
      setProgress(null);
    } finally {
      setBusy(false);
    }
  };

  const togglePick = (sh: Shot) =>
    setPicked((p) => {
      // Đổi scene → bỏ lựa chọn cũ (clip không vắt qua scene).
      const same = p.length
        ? scenes.some((sc) => (byScene[sc.id] || []).some((x) => x.id === p[0] && x.scene_id === sh.scene_id))
        : true;
      const base = same ? p : [];
      return base.includes(sh.id) ? base.filter((x) => x !== sh.id) : [...base, sh.id];
    });

  const groupPicked = async () => {
    setErr(null);
    try {
      const r = await clipsApi.group(picked);
      setByScene((m) => ({ ...m, [r.shots[0].scene_id]: r.shots }));
      setPicked([]);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const ungroup = async (lead: Shot) => {
    setErr(null);
    try {
      const r = await clipsApi.ungroup(lead.id);
      setByScene((m) => ({ ...m, [lead.scene_id]: r.shots }));
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <div className="flex h-full">
      <div className="min-w-0 flex-1 overflow-auto px-6 py-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">Cinematic Shots</h2>
            <p className="text-sm text-neutral-500">
              Mỗi thẻ là một CLIP — gộp tối đa {nMax} frame storyboard vào một video
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={busy || !!videoJob}
              onClick={autogroup}
              title={`Xếp các frame liền nhau vào clip ≤ ${nMax} frame`}
              className="rounded-lg border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-800 disabled:opacity-40"
            >
              ⛓ Gộp tự động
            </button>
            <button
              disabled={busy || !!videoJob}
              onClick={genAll}
              className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              {videoJob ? `Đang render ${videoJob.done}/${videoJob.total}…` : "✦ Auto gen video"}
            </button>
          </div>
        </div>
        {picked.length > 0 && (
          <div className="mb-4 flex items-center justify-between rounded-lg border border-indigo-800 bg-indigo-950/40 px-3 py-2 text-sm text-indigo-300">
            <span>
              Đã chọn {picked.length} frame
              {picked.length > nMax && ` — tối đa ${nMax} frame một clip`}
            </span>
            <div className="flex gap-2">
              <button onClick={() => setPicked([])} className="hover:text-indigo-100">
                Bỏ chọn
              </button>
              <button
                disabled={picked.length < 2 || picked.length > nMax}
                onClick={groupPicked}
                className="rounded-md bg-indigo-600 px-2.5 py-1 text-white hover:bg-indigo-500 disabled:opacity-40"
              >
                ⛓ Gộp thành 1 clip
              </button>
            </div>
          </div>
        )}
        {progress && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-indigo-800 bg-indigo-950/40 px-3 py-2 text-sm text-indigo-300">
            <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
            {progress}
          </div>
        )}
        {err && (
          <div className="mb-4 rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
            {err}
          </div>
        )}
        {scenes.map((sc) => {
          const list = byScene[sc.id] || [];
          const groups = groupClips(list, nMax);
          return (
            <section key={sc.id} className="mb-8">
              <h3 className="mb-3 text-sm font-medium text-neutral-200">
                <span className="mr-1.5 text-neutral-500">{String(sc.idx + 1).padStart(2, "0")}</span>
                {sc.heading}
                <span className="ml-2 text-xs font-normal text-neutral-500">
                  {list.length} frame → {groups.length} clip
                </span>
              </h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {groups.map((group) => {
                  const lead = group[0];
                  const dl = videoDownload(lead, sc.idx);
                  return (
                    <div key={lead.id}>
                      <MediaCard
                        imageSrc={lead.image_path}
                        videoSrc={lead.video_path}
                        title={clipLabel(group)}
                        index={lead.idx}
                        subtitle={
                          lead.video_path
                            ? `▶ video${group.length > 1 ? ` · ${group.length} frame` : ""}`
                            : lead.status
                        }
                        downloadUrl={dl?.url}
                        downloadName={dl?.name}
                        downloadTitle={dl?.title}
                        selected={sel?.id === lead.id}
                        busy={running.has(lead.id)}
                        busyLabel="Đang render…"
                        onClick={() => setSel(lead)}
                        onPreview={lead.video_path || lead.image_path ? () => setLightbox(lead) : undefined}
                        onEdit={
                          onEdit
                            ? () =>
                                onEdit({
                                  kind: "shot",
                                  goal: "video",
                                  id: lead.id,
                                  title: clipLabel(group),
                                  // Video prompt = motion (the action) + visual context for the i2v model.
                                  prompt:
                                    [lead.motion_prompt, lead.visual_prompt].filter(Boolean).join("\n\n") ||
                                    lead.description ||
                                    lead.title,
                                  refEntityIds: parseRefs(lead.ref_entity_ids),
                                  // Các frame CÙNG CLIP đứng trước (chúng là reference thật của
                                  // clip này), rồi tới các frame khác trong scene.
                                  refShotImages: [
                                    ...group.slice(1),
                                    ...list.filter((x) => !group.some((g) => g.id === x.id)),
                                  ]
                                    .filter((x) => x.image_media_id && x.image_path)
                                    .map((x) => ({
                                      media_id: x.image_media_id!,
                                      web: x.image_path!,
                                      label: `S${String(x.idx + 1).padStart(2, "0")} ${(x.title || x.description || "").slice(0, 30)}`.trim(),
                                    })),
                                  imageMediaId: lead.image_media_id,
                                  imageSrc: lead.image_path,
                                  videoSrc: lead.video_path,
                                })
                            : undefined
                        }
                        actions={
                          <>
                            {group.length > 1 && (
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  ungroup(lead);
                                }}
                                title="Tách clip thành từng frame"
                                className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-neutral-700"
                              >
                                ✂
                              </button>
                            )}
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                genClip(group);
                              }}
                              title={group.length > 1 ? `Render clip ${group.length} frame` : "Render video"}
                              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-indigo-600"
                            >
                              ⚡
                            </button>
                          </>
                        }
                      />
                      {/* Dải frame của clip: bấm để tick gộp/bỏ gộp thủ công. */}
                      <div className="mt-1 flex gap-1">
                        {group.map((sh, i) => (
                          <button
                            key={sh.id}
                            onClick={() => togglePick(sh)}
                            title={`${sh.media_name || sh.title}${sh.continuity ? ` — ${sh.continuity}` : ""}`}
                            className={`relative h-9 flex-1 overflow-hidden rounded border ${
                              picked.includes(sh.id)
                                ? "border-indigo-500 ring-1 ring-indigo-500"
                                : "border-neutral-800 hover:border-neutral-600"
                            }`}
                          >
                            {sh.image_path ? (
                              <img src={sh.image_path} className="h-full w-full object-cover" />
                            ) : (
                              <span className="grid h-full w-full place-items-center bg-neutral-900 text-[10px] text-neutral-600">
                                ∅
                              </span>
                            )}
                            <span className="absolute left-0.5 top-0.5 rounded bg-black/70 px-1 text-[9px] text-neutral-200">
                              {group.length > 1 ? i + 1 : sh.idx + 1}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
                {!list.length && (
                  <div className="col-span-full rounded-xl border border-dashed border-neutral-800 py-6 text-center text-xs text-neutral-600">
                    Chưa có frame — làm Storyboard trước.
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {sel && (
        <ShotPanel
          shot={sel}
          project={project}
          members={groupClips(byScene[sel.scene_id] || [], nMax).find((g) => g[0].id === sel.id) || [sel]}
          sceneIdx={Math.max(0, scenes.findIndex((s) => s.id === sel.scene_id))}
          running={running.has(sel.id)}
          onClose={() => setSel(null)}
          onChange={setShot}
          onGenVideo={() =>
            genClip(groupClips(byScene[sel.scene_id] || [], nMax).find((g) => g[0].id === sel.id) || [sel])
          }
        />
      )}
      {lightbox && (() => {
        const dl = videoDownload(
          lightbox,
          Math.max(0, scenes.findIndex((s) => s.id === lightbox.scene_id))
        );
        return (
          <Lightbox
            imageSrc={lightbox.image_path}
            videoSrc={lightbox.video_path}
            title={lightbox.title}
            downloadUrl={dl?.url}
            downloadName={dl?.name}
            onClose={() => setLightbox(null)}
          />
        );
      })()}
    </div>
  );
}

function ShotPanel({
  shot,
  project,
  members,
  sceneIdx,
  running,
  onClose,
  onChange,
  onGenVideo,
}: {
  shot: Shot;
  project: Project;
  members: Shot[];
  sceneIdx: number;
  running: boolean;
  onClose: () => void;
  onChange: (s: Shot) => void;
  onGenVideo: () => void;
}) {
  const [visual, setVisual] = useState(shot.visual_prompt ?? "");
  const [motion, setMotion] = useState(shot.motion_prompt ?? "");
  const [aiBusy, setAiBusy] = useState(false);
  const [upBusy, setUpBusy] = useState(false);
  const [upErr, setUpErr] = useState<string | null>(null);
  // Trần upscale phụ thuộc tier (ONE → 1080p, TWO → 4K) → hỏi server thay vì cứng "4K".
  const [upLabel, setUpLabel] = useState("");
  const isClip = members.length > 1;

  useEffect(() => {
    setVisual(shot.visual_prompt ?? "");
    setMotion(shot.motion_prompt ?? "");
    setUpErr(null);
  }, [shot.id]);

  useEffect(() => {
    shotsApi.upscaleStatus(project.id).then((r) => setUpLabel(r.label)).catch(() => {});
  }, [project.id]);

  // Video ghép cục bộ từ nhiều clip (chained) không upscale được — Flow chỉ nhận một media.
  const chained = !!shot.video_path && !shot.video_path.startsWith("/media/");
  const upscaled = !!shot.upscale_path && shot.upscale_media_id === shot.video_media_id;

  const save = async () =>
    onChange(await storyboard.updateShot(shot.id, { visual_prompt: visual, motion_prompt: motion }));

  // Clip gộp cần prompt TIMELINE gọi token {sc001-s01-…} của từng frame; frame đơn dùng
  // prompt thường.
  const aiPrompts = async () => {
    setAiBusy(true);
    try {
      onChange(isClip ? await clipsApi.genPrompt(shot.id) : await shotsApi.genPrompts(shot.id));
    } finally {
      setAiBusy(false);
    }
  };

  const upscale = async () => {
    setUpBusy(true);
    setUpErr(null);
    try {
      onChange(await shotsApi.upscale(shot.id, upscaled));
    } catch (e: any) {
      setUpErr(e.message);
    } finally {
      setUpBusy(false);
    }
  };

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-neutral-800 bg-neutral-950/50">
      <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-2.5">
        <span className="truncate text-sm font-medium">
          {isClip ? `Clip · ${members.length} frame` : shot.title}
        </span>
        <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">✕</button>
      </div>
      <div className="flex-1 space-y-4 overflow-auto p-4">
        <div className="overflow-hidden rounded-lg border border-neutral-800 bg-black">
          {shot.video_path ? (
            <video src={shot.video_path} controls className="aspect-video w-full" />
          ) : shot.image_path ? (
            <img src={shot.image_path} className="aspect-video w-full object-cover" />
          ) : (
            <div className="grid aspect-video w-full place-items-center text-xs text-neutral-600">
              chưa có ảnh
            </div>
          )}
        </div>
        {isClip && (
          <div>
            <label className="mb-1 block text-xs text-neutral-400">
              Các frame clip đi qua (theo thứ tự)
            </label>
            <ol className="space-y-1.5">
              {members.map((m, i) => (
                <li key={m.id} className="text-xs">
                  {/* Token reference THẬT của frame — copy nguyên si vào prompt thì Flow mới
                      bind ảnh đó vào đúng khoảnh khắc (cùng cơ chế {handle} của Node Editor). */}
                  <button
                    onClick={() => navigator.clipboard?.writeText(`{${m.media_name || ""}}`)}
                    title="Chép token này vào prompt"
                    className="mb-0.5 block max-w-full truncate rounded bg-neutral-800 px-1.5 font-mono text-[11px] text-indigo-300 hover:bg-neutral-700"
                  >
                    {`{${m.media_name || `frame-${i + 1}`}}`}
                  </button>
                  <span className="min-w-0 text-neutral-400">
                    <span className="block truncate text-neutral-300">{m.title}</span>
                    {m.continuity && <span className="block">{m.continuity}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
        <div className="flex items-center justify-between text-xs text-neutral-400">
          <span>Model: {project.video_model || "Veo i2v"}</span>
          <span>{isClip ? `${members.length} frame` : `${shot.duration}s`}</span>
        </div>
        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs text-neutral-400">
              {isClip ? "Prompt timeline của clip" : "Visual / Motion prompt"}
            </label>
            <button
              onClick={aiPrompts}
              disabled={aiBusy}
              title={isClip ? "Viết timeline đi xuyên các frame" : "Viết visual + motion prompt"}
              className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-40"
            >
              {aiBusy ? "…" : "✨ AI"}
            </button>
          </div>
          {!isClip && (
            <textarea
              value={visual}
              onChange={(e) => setVisual(e.target.value)}
              onBlur={save}
              placeholder="Visual prompt"
              className="mb-2 h-20 w-full resize-none rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
            />
          )}
          <textarea
            value={motion}
            onChange={(e) => setMotion(e.target.value)}
            onBlur={save}
            placeholder={
              isClip
                ? `[00:00] mở ở {${members[0].media_name || "sc001-s01-…"}}, máy lùi dần sang {${
                    members[1]?.media_name || "sc001-s02-…"
                  }}…`
                : "Motion prompt"
            }
            className={`w-full resize-none rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500 ${
              isClip ? "h-44" : "h-20"
            }`}
          />
          {isClip && (
            <p className="mt-1 text-[11px] text-neutral-500">
              Prompt phải gọi ĐỦ {members.length} token <code>{"{sc…}"}</code> ở trên — dấu ngoặc
              nhọn là thứ DUY NHẤT Flow bind ảnh vào (như <code>{"{handle}"}</code> của Node
              Editor). Thiếu token nào thì lúc render server tự viết lại prompt.
            </p>
          )}
        </div>
      </div>
      <div className="space-y-2 border-t border-neutral-800 p-3">
        <button
          onClick={onGenVideo}
          disabled={running || members.some((m) => !m.image_path)}
          className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          {running ? "Đang render…" : isClip ? `Render clip (${members.length} frame)` : "Generate Video"}
        </button>
        {shot.video_path && (
          <>
            {(() => {
              const dl = videoDownload(shot, sceneIdx);
              return dl ? (
                <button
                  onClick={() => downloadFile(dl.url, dl.name)}
                  title={`${dl.title} → ${dl.name}`}
                  className="w-full rounded-lg border border-emerald-800/70 py-2 text-sm text-emerald-300 hover:bg-emerald-950/40"
                >
                  ⬇ {dl.title}
                </button>
              ) : null;
            })()}
            <button
              onClick={upscale}
              disabled={upBusy || chained}
              title={
                chained
                  ? "Shot ghép từ nhiều clip — Flow không upscale được video ghép cục bộ"
                  : `Render lại video ở ${upLabel || "độ phân giải cao"} (tốn credit)`
              }
              className="w-full rounded-lg border border-neutral-700 py-2 text-sm hover:bg-neutral-800 disabled:opacity-40"
            >
              {upBusy
                ? "Đang upscale…"
                : chained
                  ? "Không upscale được (chained)"
                  : upscaled
                    ? `↻ Upscale lại ${shot.upscale_res?.split("_").pop()?.toLowerCase() ?? ""}`
                    : `Upscale ${upLabel || "…"}`}
            </button>
            {upErr && <p className="text-xs text-rose-400">{upErr}</p>}
          </>
        )}
      </div>
    </aside>
  );
}
