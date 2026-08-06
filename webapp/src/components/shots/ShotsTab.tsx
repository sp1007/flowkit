import { useEffect, useState } from "react";
import {
  api,
  boardApi,
  sheetRefEntityIds,
  type BoardSheet,
  type Project,
  type Scene,
} from "../../api/client";
import type { EditorTarget } from "../nodeeditor/NodeEditor";
import MediaCard from "../common/MediaCard";
import Lightbox from "../common/Lightbox";
import { useConfirm } from "../common/Confirm";
import { creditGuard, CREDIT_COST } from "../../lib/credits";
import { downloadFile, pad3 } from "../../lib/download";
import { useJobs, useJobWatcher } from "../../jobs/JobsContext";

// Nguồn của tab này là TRANG storyboard của tab Storyboard, không phải `shot` của Illustrators.
//
// Một trang = MỘT clip. Cả trang (4/6 panel, có badge số tròn và caption vẽ sẵn trong ảnh) là
// reference DUY NHẤT cho Omni Flash r2v; badge số là thứ chỉ cho model biết panel nào là panel
// nào — nó thay cho token `{sc001-s01-…}` mà clip nhiều-ảnh bên Illustrators phải dùng.
//
// Vì thế ở đây không còn nút gộp/tách clip: nhóm đã cố định là các panel của trang.

const sheetLabel = (sh: BoardSheet) =>
  `Trang ${sh.idx + 1}${sh.title ? ` · ${sh.title}` : ""}`;

const videoName = (sh: BoardSheet, sceneIdx: number) =>
  `sc${pad3(sceneIdx + 1)}-page${String(sh.idx + 1).padStart(2, "0")}.mp4`;

export default function ShotsTab({
  project,
  onEdit,
}: {
  project: Project;
  onEdit?: (t: EditorTarget) => void;
}) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [sheets, setSheets] = useState<BoardSheet[]>([]);
  const [sel, setSel] = useState<BoardSheet | null>(null);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<BoardSheet | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const confirm = useConfirm();
  const { jobFor } = useJobs();

  const reload = async () => {
    const r = await boardApi.listProject(project.id);
    setSheets(r.sheets);
    setSel((s) => (s ? r.sheets.find((x) => x.id === s.id) || null : null));
  };

  useEffect(() => {
    (async () => {
      setScenes((await api.listScenes(project.id)).scenes);
      await reload();
    })().catch((e) => setErr(e.message));
  }, [project.id]);

  const videoJob = jobFor("videos");
  useJobWatcher("videos", {
    onAdvance: reload,
    onDone: (j) => {
      reload();
      if (j.errors.length)
        setErr(`Auto gen video: ${j.done}/${j.total} xong, ${j.errors.length} lỗi.`);
    },
  });

  const mark = (id: string, on: boolean) =>
    setRunning((s) => {
      const n = new Set(s);
      on ? n.add(id) : n.delete(id);
      return n;
    });

  const upsert = (u: BoardSheet) => {
    setSheets((list) => list.map((x) => (x.id === u.id ? { ...x, ...u } : x)));
    setSel((s) => (s && s.id === u.id ? { ...s, ...u } : s));
  };

  const genOne = async (sh: BoardSheet) => {
    if (!sh.media_id) {
      setErr("Trang chưa có ảnh — vẽ ở tab Storyboard trước");
      return;
    }
    mark(sh.id, true);
    setErr(null);
    try {
      upsert(await boardApi.genVideo(sh.id));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      mark(sh.id, false);
    }
  };

  const genAll = async () => {
    const todo = sheets.filter((s) => s.media_id && !s.video_path);
    if (!todo.length) {
      setErr("Không có trang nào (đã có ảnh, chưa có video) để render.");
      return;
    }
    if (!(await creditGuard(confirm, todo.length, CREDIT_COST.video, "Render video"))) return;
    setErr(null);
    try {
      await boardApi.genAllVideos(project.id);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const byScene = (sid: string) => sheets.filter((s) => s.scene_id === sid);
  const sceneIdxOf = (sid: string) => Math.max(0, scenes.findIndex((s) => s.id === sid));

  return (
    <div className="flex h-full">
      <div className="min-w-0 flex-1 overflow-auto px-6 py-5">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold">Cinematic Shots</h2>
            <p className="text-sm text-neutral-500">
              Mỗi thẻ là một CLIP — một trang storyboard thành một video liên tục
            </p>
          </div>
          <button
            disabled={!!videoJob}
            onClick={genAll}
            className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            {videoJob ? `Đang render ${videoJob.done}/${videoJob.total}…` : "✦ Auto gen video"}
          </button>
        </div>

        {err && (
          <div className="mb-4 rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
            {err}
            <button className="ml-2 text-rose-400 hover:text-rose-200" onClick={() => setErr(null)}>
              ✕
            </button>
          </div>
        )}

        {scenes.map((sc) => {
          const list = byScene(sc.id);
          return (
            <section key={sc.id} className="mb-8">
              <h3 className="mb-3 text-sm font-medium text-neutral-200">
                <span className="mr-1.5 text-neutral-500">{String(sc.idx + 1).padStart(2, "0")}</span>
                {sc.heading}
                <span className="ml-2 text-xs font-normal text-neutral-500">
                  {list.length} trang → {list.length} clip
                </span>
              </h3>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {list.map((sh) => (
                  <div key={sh.id}>
                    <MediaCard
                      imageSrc={sh.path}
                      videoSrc={sh.video_path}
                      title={sheetLabel(sh)}
                      index={sh.idx}
                      subtitle={
                        sh.video_path ? `▶ video · ${sh.panels_list.length} panel` : sh.status
                      }
                      downloadUrl={sh.video_path}
                      downloadName={videoName(sh, sc.idx)}
                      downloadTitle="Tải video (bản HD)"
                      selected={sel?.id === sh.id}
                      busy={running.has(sh.id)}
                      busyLabel="Đang render…"
                      onClick={() => setSel(sh)}
                      onPreview={sh.video_path || sh.path ? () => setLightbox(sh) : undefined}
                      onEdit={
                        onEdit
                          ? () =>
                              onEdit({
                                kind: "sheet",
                                goal: "image",
                                id: sh.id,
                                title: sheetLabel(sh),
                                prompt: sh.prompt || "",
                                // Xem BoardTab: thiếu cái này thì đồ thị mặc định không có
                                // node "Nguồn ảnh" và node Tạo ảnh chạy trơ.
                                refEntityIds: sheetRefEntityIds(sh),
                                imageMediaId: sh.media_id,
                                imageSrc: sh.path,
                              })
                          : undefined
                      }
                      actions={
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            genOne(sh);
                          }}
                          title="Render clip từ trang này"
                          className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-indigo-600"
                        >
                          ⚡
                        </button>
                      }
                    />
                    {/* Các panel mà clip đi qua, theo thứ tự — chỉ để đối chiếu, chúng nằm
                        TRONG cùng một bức ảnh chứ không phải file rời. */}
                    <div className="mt-1 flex gap-1">
                      {sh.panels_list.map((p) => (
                        <div
                          key={p.id}
                          title={`${p.caption || ""} — ${p.description || ""}`}
                          className="flex h-6 flex-1 items-center justify-center gap-1 overflow-hidden rounded border border-neutral-800 bg-neutral-900 px-1"
                        >
                          <span className="grid h-3.5 w-3.5 shrink-0 place-items-center rounded-full bg-neutral-200 text-[8px] font-semibold text-neutral-900">
                            {p.idx + 1}
                          </span>
                          <span className="truncate text-[9px] text-neutral-500">
                            {p.caption || "—"}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                {!list.length && (
                  <div className="col-span-full rounded-xl border border-dashed border-neutral-800 py-6 text-center text-xs text-neutral-600">
                    Chưa có trang — làm tab Storyboard trước.
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {sel && (
        <SheetPanel
          sheet={sel}
          project={project}
          sceneIdx={sceneIdxOf(sel.scene_id)}
          running={running.has(sel.id)}
          onClose={() => setSel(null)}
          onChange={upsert}
          onGenVideo={() => genOne(sel)}
        />
      )}
      {lightbox && (
        <Lightbox
          imageSrc={lightbox.path}
          videoSrc={lightbox.video_path}
          title={sheetLabel(lightbox)}
          downloadUrl={lightbox.video_path}
          downloadName={videoName(lightbox, sceneIdxOf(lightbox.scene_id))}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}

function SheetPanel({
  sheet,
  project,
  sceneIdx,
  running,
  onClose,
  onChange,
  onGenVideo,
}: {
  sheet: BoardSheet;
  project: Project;
  sceneIdx: number;
  running: boolean;
  onClose: () => void;
  onChange: (s: BoardSheet) => void;
  onGenVideo: () => void;
}) {
  const [motion, setMotion] = useState(sheet.motion_prompt ?? "");
  const [aiBusy, setAiBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setMotion(sheet.motion_prompt ?? "");
    setErr(null);
  }, [sheet.id, sheet.motion_prompt]);

  const save = async () => {
    if (motion === (sheet.motion_prompt ?? "")) return;
    try {
      onChange(await boardApi.patchSheet(sheet.id, { motion_prompt: motion }));
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const aiPrompt = async () => {
    setAiBusy(true);
    setErr(null);
    try {
      onChange(await boardApi.genPrompt(sheet.id));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setAiBusy(false);
    }
  };

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-neutral-800 bg-neutral-950/50">
      <div className="flex items-center justify-between border-b border-neutral-800 px-4 py-2.5">
        <span className="truncate text-sm font-medium">{sheetLabel(sheet)}</span>
        <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">
          ✕
        </button>
      </div>
      <div className="flex-1 space-y-4 overflow-auto p-4">
        <div className="overflow-hidden rounded-lg border border-neutral-800 bg-black">
          {sheet.video_path ? (
            <video src={sheet.video_path} controls className="aspect-video w-full" />
          ) : sheet.path ? (
            <img src={sheet.path} className="aspect-video w-full object-cover" />
          ) : (
            <div className="grid aspect-video w-full place-items-center text-xs text-neutral-600">
              chưa có ảnh
            </div>
          )}
        </div>

        <div>
          <label className="mb-1 block text-xs text-neutral-400">
            Các panel clip đi qua (theo thứ tự)
          </label>
          <ol className="space-y-1.5">
            {sheet.panels_list.map((p) => (
              <li key={p.id} className="flex gap-1.5 text-xs">
                <span className="mt-0.5 grid h-4 w-4 shrink-0 place-items-center rounded-full bg-neutral-200 text-[9px] font-semibold text-neutral-900">
                  {p.idx + 1}
                </span>
                <span className="min-w-0 text-neutral-400">
                  <span className="block truncate text-neutral-300">
                    {p.caption || p.shot_size || `Panel ${p.idx + 1}`}
                  </span>
                  {p.description && <span className="block">{p.description}</span>}
                </span>
              </li>
            ))}
          </ol>
        </div>

        <div className="flex items-center justify-between text-xs text-neutral-400">
          <span>Model: {project.video_model || "Veo i2v"}</span>
          <span>{sheet.duration ? `${sheet.duration}s` : `${sheet.panels_list.length} panel`}</span>
        </div>

        <div>
          <div className="mb-1 flex items-center justify-between">
            <label className="text-xs text-neutral-400">Prompt timeline của clip</label>
            <button
              onClick={aiPrompt}
              disabled={aiBusy || !sheet.media_id}
              title="Viết timeline đi xuyên các panel"
              className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-40"
            >
              {aiBusy ? "…" : "✨ AI"}
            </button>
          </div>
          <textarea
            value={motion}
            onChange={(e) => setMotion(e.target.value)}
            onBlur={save}
            placeholder="[00:00] … [00:03] … [00:05] …"
            className="h-44 w-full resize-none rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500"
          />
          <p className="mt-1 text-[11px] text-neutral-500">
            Đi qua panel 1→{sheet.panels_list.length} trong MỘT cú máy liên tục. Gọi panel bằng
            chữ thường (<code>panel 3</code>), <b>đừng</b> bọc trong <code>{"{}"}</code> — cả
            trang chỉ là MỘT reference nên không có gì để bind. Mốc thời gian chia theo hành động,
            không chia đều; cách chuyển giữa các panel để AI tự chọn, miễn là hợp lý về vật lý.
          </p>
        </div>
        {err && <p className="text-xs text-rose-400">{err}</p>}
      </div>
      <div className="space-y-2 border-t border-neutral-800 p-3">
        <button
          onClick={onGenVideo}
          disabled={running || !sheet.media_id}
          className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          {running ? "Đang render…" : `Render clip (${sheet.panels_list.length} panel)`}
        </button>
        {sheet.video_path && (
          <button
            onClick={() => downloadFile(sheet.video_path!, videoName(sheet, sceneIdx))}
            className="w-full rounded-lg border border-emerald-800/70 py-2 text-sm text-emerald-300 hover:bg-emerald-950/40"
          >
            ⬇ Tải video (bản HD)
          </button>
        )}
      </div>
    </aside>
  );
}
