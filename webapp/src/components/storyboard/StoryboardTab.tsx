import { useEffect, useState } from "react";
import {
  api,
  storyboard,
  storyboardExportUrl,
  type Entity,
  type Project,
  type Scene,
  type Shot,
} from "../../api/client";
import type { EditorTarget } from "../nodeeditor/NodeEditor";
import MediaCard from "../common/MediaCard";
import Lightbox from "../common/Lightbox";
import CandidatePicker from "../common/CandidatePicker";
import { useConfirm } from "../common/Confirm";
import { creditGuard, CREDIT_COST } from "../../lib/credits";

const slug = (s: string) =>
  (s || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[\\/:*?"<>|\r\n\t]+/g, "")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "shot";

const pad3 = (n: number) => String(n + 1).padStart(3, "0");

// Trigger a browser download of a same-origin file with a chosen filename.
function downloadFile(url: string, filename: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

const parseRefs = (s: string | null): string[] => {
  try {
    return JSON.parse(s || "[]");
  } catch {
    return [];
  }
};

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export default function StoryboardTab({
  project,
  onEdit,
  onCoverSet,
}: {
  project: Project;
  onEdit?: (t: EditorTarget) => void;
  onCoverSet?: (mediaId: string) => void;
}) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [entities, setEntities] = useState<Entity[]>([]);
  const [shotsByScene, setShotsByScene] = useState<Record<string, Shot[]>>({});
  const [sel, setSel] = useState<Shot | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [gening, setGening] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<Shot | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<Shot | null>(null);
  const confirm = useConfirm();

  const setAsCover = async (shot: Shot) => {
    if (!shot.image_media_id) return;
    setErr(null);
    try {
      await api.setCover(project.id, shot.image_media_id);
      onCoverSet?.(shot.image_media_id);
      setNotice(`Đã đặt "${shot.title}" làm ảnh đại diện dự án`);
      setTimeout(() => setNotice(null), 2500);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const loadShots = async (sid: string) => {
    const r = await storyboard.sceneShots(sid);
    setShotsByScene((m) => ({ ...m, [sid]: r.shots }));
  };

  useEffect(() => {
    (async () => {
      const sc = (await api.listScenes(project.id)).scenes;
      setScenes(sc);
      setEntities((await api.listEntities(project.id)).entities);
      for (const s of sc) await loadShots(s.id);
    })().catch((e) => setErr(e.message));
  }, [project.id]);

  const setShot = (updated: Shot) => {
    setShotsByScene((m) => ({
      ...m,
      [updated.scene_id]: (m[updated.scene_id] || []).map((x) =>
        x.id === updated.id ? updated : x
      ),
    }));
    if (sel?.id === updated.id) setSel(updated);
  };

  const genImage = async (shot: Shot): Promise<boolean> => {
    setGening((s) => new Set(s).add(shot.id));
    setErr(null);
    try {
      setShot(await storyboard.genImage(shot.id));
      return true;
    } catch (e: any) {
      setErr(e.message);
      return false;
    } finally {
      setGening((s) => {
        const n = new Set(s);
        n.delete(shot.id);
        return n;
      });
    }
  };

  // Generate a list of frames one-by-one on the client → live per-frame "Đang tạo…"
  // overlay + progress; backend verifies the image was created and retries policy blocks.
  const genSequential = async (label: string, todo: Shot[]) => {
    if (!todo.length) {
      setErr("Mọi frame đã có ảnh.");
      return;
    }
    if (!(await creditGuard(confirm, todo.length, CREDIT_COST.image, "Tạo ảnh storyboard"))) return;
    setBusy(label);
    setErr(null);
    let okN = 0;
    const failed: string[] = [];
    for (let i = 0; i < todo.length; i++) {
      setProgress(`Đang tạo ${i + 1}/${todo.length}: ${todo[i].title}`);
      const ok = await genImage(todo[i]);
      ok ? okN++ : failed.push(todo[i].title);
      if (i < todo.length - 1) await sleep(2000 + Math.random() * 4000);
    }
    setProgress(null);
    setBusy(null);
    if (failed.length) setErr(`Xong ${okN}/${todo.length}. Lỗi: ${failed.join(", ")}`);
  };

  const autofill = async (sid: string) => {
    setBusy("autofill:" + sid);
    setErr(null);
    try {
      const r = await storyboard.autofill(sid);
      setShotsByScene((m) => ({ ...m, [sid]: r.shots }));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const sceneAll = async (sid: string) => {
    const todo = (shotsByScene[sid] || []).filter((s) => !s.image_path);
    await genSequential("all:" + sid, todo);
  };

  const reloadAll = async () => {
    for (const s of scenes) await loadShots(s.id);
  };

  const autofillAll = async () => {
    setBusy("autofill-all");
    setErr(null);
    try {
      await storyboard.autofillAll(project.id);
      await reloadAll();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  // Storytelling (§2.6): TTS each scene as ONE continuous read first, then map beats.
  const buildBeats = async () => {
    const ok = await confirm({
      title: "Dựng shots theo lời đọc?",
      message:
        "Mỗi scene được đọc (TTS) liền mạch MỘT lần để giữ cảm xúc, rồi cắt thành beat " +
        "(1 cảnh) bám đúng thời điểm audio; từ khoá quan trọng được canh giờ để hiện chữ " +
        "lên video. Cần BẬT OmniVoice (TTS); nếu tắt sẽ ước lượng theo số từ.\n\n" +
        "Thao tác này XOÁ các shot hiện tại.",
      confirmText: "Dựng theo lời đọc",
      danger: true,
    });
    if (!ok) return;
    setBusy("beats");
    setErr(null);
    try {
      const r = await storyboard.buildBeats(project.id);
      await reloadAll();
      setNotice(
        `Đã dựng ${r.shots} shot cho ${r.done}/${r.requested} scene (~${r.total_duration}s, ` +
          `${r.measured ? "đo bằng TTS thật" : "ước lượng — bật TTS để chính xác"}).`
      );
      setTimeout(() => setNotice(null), 6000);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  // Rebuild the shot list for EVERY scene from the script (force) — deletes existing
  // shots (incl. manual edits) and re-splits via AI. Confirm because it's destructive.
  const rebuildAll = async () => {
    const ok = await confirm({
      title: "Dựng lại tất cả shots từ kịch bản?",
      message:
        "Thao tác này XOÁ các shot hiện tại (kể cả prompt/ảnh đã chỉnh tay) và để AI tách lại từ script.",
      confirmText: "Dựng lại tất cả",
      danger: true,
    });
    if (!ok) return;
    setBusy("rebuild-all");
    setErr(null);
    try {
      const r = await storyboard.autofillAll(project.id, undefined, true);
      await reloadAll();
      setNotice(`Đã dựng lại shots cho ${r.done}/${r.requested} scene`);
      setTimeout(() => setNotice(null), 3000);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const projectAll = async () => {
    const todo = scenes.flatMap((sc) => (shotsByScene[sc.id] || []).filter((s) => !s.image_path));
    await genSequential("gen-all", todo);
  };

  return (
    <div className="flex h-full">
      <div className="min-w-0 flex-1 overflow-auto px-6 py-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-xl font-semibold">Storyboard</h2>
            <p className="text-sm text-neutral-500">Ảnh từng frame theo scene</p>
          </div>
          <div className="flex gap-2">
            <button
              disabled={!!busy || !scenes.length}
              onClick={autofillAll}
              title="Autofill các scene CHƯA có shot (bỏ qua scene đã có)"
              className="rounded-lg border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-800 disabled:opacity-40"
            >
              {busy === "autofill-all" ? "Đang autofill…" : "✨ Autofill all"}
            </button>
            <button
              disabled={!!busy || !scenes.length}
              onClick={rebuildAll}
              title="Dựng lại shots từ kịch bản cho MỌI scene (xóa shot cũ)"
              className="rounded-lg border border-amber-700/60 px-3 py-2 text-sm text-amber-300 hover:bg-amber-950/40 disabled:opacity-40"
            >
              {busy === "rebuild-all" ? "Đang dựng lại…" : "↻ Dựng lại tất cả"}
            </button>
            {!!project.storytelling && (
              <button
                disabled={!!busy || !scenes.length}
                onClick={buildBeats}
                title="Storytelling: dựng shots theo độ dài lời đọc (beat audio-driven)"
                className="rounded-lg border border-violet-700/60 px-3 py-2 text-sm text-violet-300 hover:bg-violet-950/40 disabled:opacity-40"
              >
                {busy === "beats" ? "Đang dựng beat…" : "🎙 Dựng theo lời đọc"}
              </button>
            )}
            <button
              disabled={!!busy || !scenes.length}
              onClick={projectAll}
              title="Tạo ảnh cho mọi frame chưa có ảnh trong dự án"
              className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              {busy === "gen-all" ? "Đang tạo…" : "✦ Auto gen all"}
            </button>
            <button
              onClick={() => downloadFile(storyboardExportUrl(project.id), "storyboard.zip")}
              title="Tải toàn bộ ảnh storyboard (.zip, scXXX-sXXX-mô-tả.png)"
              className="rounded-lg border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-800"
            >
              ⬇ Export ảnh
            </button>
          </div>
        </div>
        {progress && (
          <div className="mb-4 flex items-center gap-2 rounded-lg border border-indigo-800 bg-indigo-950/40 px-3 py-2 text-sm text-indigo-300">
            <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
            {progress}
          </div>
        )}
        {notice && (
          <div className="mb-4 rounded-lg border border-emerald-800 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-300">
            ★ {notice}
          </div>
        )}
        {err && (
          <div className="mb-4 rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
            {err}
          </div>
        )}
        {!scenes.length && (
          <div className="rounded-xl border border-dashed border-neutral-800 py-12 text-center text-sm text-neutral-500">
            Chưa có scene — tạo kịch bản ở tab Script trước.
          </div>
        )}
        {scenes.map((sc) => {
          const shots = shotsByScene[sc.id] || [];
          return (
            <section key={sc.id} className="mb-8">
              <div className="mb-3 flex items-center gap-3">
                <h3 className="text-sm font-medium text-neutral-200">
                  <span className="mr-1.5 text-neutral-500">{String(sc.idx + 1).padStart(2, "0")}</span>
                  {sc.heading}
                </h3>
                <div className="ml-auto flex gap-2">
                  <button
                    disabled={!!busy}
                    onClick={() => autofill(sc.id)}
                    className="rounded-md border border-neutral-700 px-2.5 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
                  >
                    {busy === "autofill:" + sc.id ? "…" : "✨ Autofill"}
                  </button>
                  <button
                    disabled={!!busy || !shots.length}
                    onClick={() => sceneAll(sc.id)}
                    className="rounded-md bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
                  >
                    {busy === "all:" + sc.id ? "…" : "✦ Auto gen"}
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {shots.map((sh) => (
                  <MediaCard
                    key={sh.id}
                    imageSrc={sh.image_path}
                    title={sh.title}
                    index={sh.idx}
                    subtitle={sh.description}
                    selected={sel?.id === sh.id}
                    busy={gening.has(sh.id)}
                    busyLabel="Đang tạo ảnh…"
                    onClick={() => setSel(sh)}
                    onPreview={sh.image_path ? () => setLightbox(sh) : undefined}
                    onEdit={
                      onEdit
                        ? () =>
                            onEdit({
                              kind: "shot",
                              goal: "image",
                              id: sh.id,
                              title: sh.title,
                              prompt: sh.description || sh.visual_prompt || sh.title,
                              refEntityIds: parseRefs(sh.ref_entity_ids),
                              imageMediaId: sh.image_media_id,
                              imageSrc: sh.image_path,
                              videoSrc: sh.video_path,
                            })
                        : undefined
                    }
                    actions={
                      <>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            genImage(sh);
                          }}
                          title="Gen nhanh"
                          className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-indigo-600"
                        >
                          ⚡
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setCandidate(sh);
                          }}
                          title="Tạo nhiều mẫu rồi chọn ảnh đẹp nhất"
                          className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-indigo-600"
                        >
                          🎲
                        </button>
                        {sh.image_path && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              downloadFile(
                                sh.image_path!,
                                `sc${pad3(sc.idx)}-s${pad3(sh.idx)}-${slug(sh.description || sh.title)}.png`
                              );
                            }}
                            title="Tải ảnh này"
                            className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-emerald-600"
                          >
                            ⬇
                          </button>
                        )}
                        {sh.image_media_id && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setAsCover(sh);
                            }}
                            title="Đặt làm ảnh đại diện dự án"
                            className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-amber-600"
                          >
                            ★
                          </button>
                        )}
                        <button
                          onClick={async (e) => {
                            e.stopPropagation();
                            await storyboard.deleteShot(sh.id);
                            loadShots(sc.id);
                          }}
                          title="Xóa"
                          className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-rose-600"
                        >
                          🗑
                        </button>
                      </>
                    }
                  />
                ))}
                <button
                  onClick={async () => {
                    await storyboard.addShot(sc.id);
                    loadShots(sc.id);
                  }}
                  className="aspect-video rounded-xl border border-dashed border-neutral-700 text-2xl text-neutral-600 hover:border-neutral-500 hover:text-neutral-400"
                >
                  +
                </button>
              </div>
            </section>
          );
        })}
      </div>

      {sel && (
        <FramePanel
          shot={sel}
          entities={entities}
          onClose={() => setSel(null)}
          onChange={setShot}
          onGenerate={() => genImage(sel)}
          onCover={() => setAsCover(sel)}
          generating={gening.has(sel.id)}
        />
      )}

      {lightbox && (
        <Lightbox imageSrc={lightbox.image_path} title={lightbox.title} onClose={() => setLightbox(null)} />
      )}

      {candidate && (
        <CandidatePicker
          kind="shot"
          id={candidate.id}
          title={candidate.title}
          onApplied={(updated) =>
            setShotsByScene((m) => ({
              ...m,
              [updated.scene_id]: (m[updated.scene_id] || []).map((x) =>
                x.id === updated.id ? updated : x
              ),
            }))
          }
          onClose={() => setCandidate(null)}
        />
      )}
    </div>
  );
}

function FramePanel({
  shot,
  entities,
  onClose,
  onChange,
  onGenerate,
  onCover,
  generating,
}: {
  shot: Shot;
  entities: Entity[];
  onClose: () => void;
  onChange: (s: Shot) => void;
  onGenerate: () => void;
  onCover: () => void;
  generating: boolean;
}) {
  const [title, setTitle] = useState(shot.title);
  const [desc, setDesc] = useState(shot.description ?? "");
  const refIds: string[] = (() => {
    try {
      return JSON.parse(shot.ref_entity_ids || "[]");
    } catch {
      return [];
    }
  })();
  const [refs, setRefs] = useState<string[]>(refIds);

  useEffect(() => {
    setTitle(shot.title);
    setDesc(shot.description ?? "");
    try {
      setRefs(JSON.parse(shot.ref_entity_ids || "[]"));
    } catch {
      setRefs([]);
    }
  }, [shot.id]);

  const save = async () => {
    onChange(
      await storyboard.updateShot(shot.id, { title, description: desc, ref_entity_ids: refs })
    );
  };

  const toggleRef = (id: string) =>
    setRefs((r) => (r.includes(id) ? r.filter((x) => x !== id) : [...r, id]));

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-neutral-800 bg-neutral-950/50">
      <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-2.5">
        <span className="text-sm font-medium">Frame</span>
        <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">✕</button>
      </div>
      <div className="flex-1 space-y-4 overflow-auto p-4">
        <div>
          <label className="mb-1 block text-xs text-neutral-400">Tiêu đề</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={save}
            className="w-full rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs text-neutral-400">Mô tả (dùng {"{Tên}"} để gắn ref)</label>
          <textarea
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            onBlur={save}
            className="h-28 w-full resize-none rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
          />
        </div>
        <div>
          <label className="mb-1.5 block text-xs text-neutral-400">Reference Assets (≤10)</label>
          <div className="space-y-1">
            {entities.map((e) => (
              <label
                key={e.id}
                className="flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-1 text-sm hover:bg-neutral-800"
              >
                <input
                  type="checkbox"
                  checked={refs.includes(e.id)}
                  onChange={() => toggleRef(e.id)}
                  onBlur={save}
                  className="h-3.5 w-3.5 accent-indigo-500"
                />
                <span className={`h-1.5 w-1.5 rounded-full ${e.media_id ? "bg-emerald-400" : "bg-neutral-600"}`} />
                <span className="truncate text-neutral-300">{e.name}</span>
                <span className="ml-auto text-xs text-neutral-600">{e.type}</span>
              </label>
            ))}
            {!entities.length && <p className="text-xs text-neutral-600">Chưa có asset.</p>}
          </div>
        </div>
      </div>
      <div className="space-y-2 border-t border-neutral-800 p-3">
        <button
          onClick={async () => {
            await save();
            onGenerate();
          }}
          disabled={generating}
          className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          {generating ? "Đang tạo ảnh…" : "Create image"}
        </button>
        <button
          onClick={onCover}
          disabled={!shot.image_media_id}
          title={shot.image_media_id ? "" : "Tạo ảnh cho frame này trước"}
          className="w-full rounded-lg border border-amber-700/60 py-2 text-sm text-amber-300 hover:bg-amber-950/40 disabled:opacity-40"
        >
          ★ Đặt làm ảnh đại diện dự án
        </button>
      </div>
    </aside>
  );
}
