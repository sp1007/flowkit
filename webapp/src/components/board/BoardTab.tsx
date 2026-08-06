import { useEffect, useState } from "react";
import {
  api,
  boardApi,
  sheetRefEntityIds,
  type BoardPanel,
  type BoardSheet,
  type Project,
  type Scene,
} from "../../api/client";
import type { EditorTarget } from "../nodeeditor/NodeEditor";
import Thumb from "../Thumb";
import Lightbox from "../common/Lightbox";
import { useConfirm } from "../common/Confirm";
import { creditGuard, CREDIT_COST } from "../../lib/credits";
import { useJobs, useJobWatcher } from "../../jobs/JobsContext";

// Tab Storyboard: mỗi TRANG là MỘT lượt sinh ảnh chứa 4/6 panel. Tất cả vẽ chung một lượt nên
// bối cảnh, ánh sáng, trang phục và nét vẽ không thể lệch — khác tab Illustrators sinh ảnh rời.
//
// Trang KHÔNG bị cắt: chính bức ảnh nguyên vẹn (badge số tròn + caption vẽ sẵn bên trong) đi
// thẳng sang tab Shots làm reference duy nhất cho một clip.

export default function BoardTab({
  project,
  onEdit,
}: {
  project: Project;
  onEdit?: (t: EditorTarget) => void;
}) {
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [sheets, setSheets] = useState<BoardSheet[]>([]);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<BoardSheet | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [promptOf, setPromptOf] = useState<{ id: string; text: string } | null>(null);
  const confirm = useConfirm();
  const { jobFor } = useJobs();

  const reload = async () => setSheets((await boardApi.listProject(project.id)).sheets);

  useEffect(() => {
    (async () => {
      setScenes((await api.listScenes(project.id)).scenes);
      await reload();
    })().catch((e) => setErr(e.message));
  }, [project.id]);

  // Dùng chung type job "storyboard" với tab Illustrators nên banner tiến độ hoạt động như cũ.
  const job = jobFor("storyboard");
  useJobWatcher("storyboard", {
    onAdvance: reload,
    onDone: (j) => {
      reload();
      if (j.errors.length) setErr(`Sinh trang: ${j.done}/${j.total} xong, ${j.errors.length} lỗi.`);
    },
  });

  const mark = (id: string, on: boolean) =>
    setRunning((s) => {
      const n = new Set(s);
      on ? n.add(id) : n.delete(id);
      return n;
    });

  const run = async (id: string, fn: () => Promise<any>) => {
    mark(id, true);
    setErr(null);
    try {
      await fn();
      await reload();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      mark(id, false);
    }
  };

  const genSheet = async (sh: BoardSheet) => {
    if (!(await creditGuard(confirm, 1, CREDIT_COST.image, "Sinh trang storyboard"))) return;
    return run(sh.id, () => boardApi.generate(sh.id));
  };

  const genAll = async () => {
    const todo = sheets.filter((s) => !s.path);
    if (!todo.length) {
      setErr("Mọi trang đều đã có ảnh. Dùng ✦ trên từng trang để vẽ lại.");
      return;
    }
    if (!(await creditGuard(confirm, todo.length, CREDIT_COST.image, "Sinh trang storyboard"))) return;
    setErr(null);
    try {
      await boardApi.generateAll(project.id);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const removeSheet = async (sh: BoardSheet) => {
    if (
      !(await confirm({
        title: "Xoá trang?",
        message: `Cả ${sh.panels_list.length} panel của trang này sẽ mất. Ảnh đã sinh trên Flow không bị xoá.`,
        confirmText: "Xoá",
        danger: true,
      }))
    )
      return;
    await run(sh.id, () => boardApi.remove(sh.id));
  };

  const showPrompt = async (sh: BoardSheet) => {
    try {
      const r = await boardApi.promptPreview(sh.id);
      setPromptOf({ id: sh.id, text: r.prompt });
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const openEditor = (sh: BoardSheet) =>
    onEdit?.({
      kind: "sheet",
      id: sh.id,
      title: sh.title || `Trang ${sh.idx + 1}`,
      goal: "image",
      // Prompt trang = thân đã gửi đi, hoặc ghép từ các panel khi chưa vẽ lần nào.
      prompt:
        sh.prompt ||
        sh.panels_list
          .map((p, i) => `Panel ${i + 1}: ${p.description || p.caption || ""}`)
          .join("\n"),
      // Union entity của MỌI panel — thiếu cái này thì đồ thị mặc định không có node
      // "Nguồn ảnh" nào và node Tạo ảnh chạy trơ, không bám ảnh tham chiếu.
      refEntityIds: sheetRefEntityIds(sh),
      imageMediaId: sh.media_id,
      imageSrc: sh.path,
    });

  const byScene = (sid: string) => sheets.filter((s) => s.scene_id === sid);
  const drawn = sheets.filter((s) => s.path).length;
  const nPanels = project.sheet_panels || 6;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-neutral-800 px-4 py-2.5">
        <div className="text-sm text-neutral-400">
          {sheets.length} trang · {drawn} đã vẽ · {nPanels} panel/trang
        </div>
        <div className="flex-1" />
        <button
          onClick={genAll}
          disabled={!!job || !sheets.length}
          className="rounded-lg border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-800 disabled:opacity-40"
        >
          {job ? `Đang vẽ ${job.done}/${job.total}…` : "✦ Sinh mọi trang"}
        </button>
      </div>

      {err && (
        <div className="border-b border-red-900/50 bg-red-950/40 px-4 py-2 text-sm text-red-300">
          {err}
          <button className="ml-2 text-red-400 hover:text-red-200" onClick={() => setErr(null)}>
            ✕
          </button>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {scenes.map((sc) => (
          <section key={sc.id} className="mb-6">
            <div className="mb-2 flex items-center gap-2">
              <h3 className="text-sm font-medium text-neutral-300">
                {String(sc.idx + 1).padStart(2, "0")} · {sc.heading}
              </h3>
              <span className="text-xs text-neutral-600">{byScene(sc.id).length} trang</span>
              <div className="flex-1" />
              <button
                onClick={() => run(`sc-${sc.id}`, () => boardApi.autofill(sc.id))}
                disabled={running.has(`sc-${sc.id}`)}
                title={`AI chia scene này thành các trang, mỗi trang ${nPanels} panel`}
                className="rounded-lg border border-neutral-700 px-2.5 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
              >
                {running.has(`sc-${sc.id}`) ? "…" : "✨ Chia trang"}
              </button>
              <button
                onClick={() => run(`sc-${sc.id}`, () => boardApi.add(sc.id))}
                className="rounded-lg border border-neutral-700 px-2.5 py-1 text-xs hover:bg-neutral-800"
              >
                + Trang
              </button>
            </div>

            {!byScene(sc.id).length && (
              <div className="rounded-lg border border-dashed border-neutral-800 px-4 py-6 text-center text-sm text-neutral-600">
                Chưa có trang. Bấm “✨ Chia trang” để AI tách scene thành các trang {nPanels} panel.
              </div>
            )}

            <div className="space-y-3">
              {byScene(sc.id).map((sh) => (
                <SheetCard
                  key={sh.id}
                  sheet={sh}
                  busy={running.has(sh.id)}
                  open={expanded === sh.id}
                  onToggle={() => setExpanded(expanded === sh.id ? null : sh.id)}
                  onGen={() => genSheet(sh)}
                  onDelete={() => removeSheet(sh)}
                  onEditor={() => openEditor(sh)}
                  onPrompt={() => showPrompt(sh)}
                  onPreview={() => sh.path && setLightbox(sh)}
                  onPanelSaved={reload}
                />
              ))}
            </div>
          </section>
        ))}
      </div>

      {lightbox && (
        <Lightbox
          imageSrc={lightbox.path}
          title={lightbox.title}
          onClose={() => setLightbox(null)}
        />
      )}
      {promptOf && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-6"
          onClick={() => setPromptOf(null)}
        >
          <div
            className="max-h-[80vh] w-full max-w-3xl overflow-auto rounded-xl border border-neutral-700 bg-neutral-900 p-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium">Prompt gửi đi (y hệt)</span>
              <button
                onClick={() => navigator.clipboard?.writeText(promptOf.text)}
                className="rounded border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800"
              >
                Chép
              </button>
            </div>
            <pre className="whitespace-pre-wrap break-words text-[11px] leading-relaxed text-neutral-300">
              {promptOf.text}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function SheetCard({
  sheet,
  busy,
  open,
  onToggle,
  onGen,
  onDelete,
  onEditor,
  onPrompt,
  onPreview,
  onPanelSaved,
}: {
  sheet: BoardSheet;
  busy: boolean;
  open: boolean;
  onToggle: () => void;
  onGen: () => void;
  onDelete: () => void;
  onEditor: () => void;
  onPrompt: () => void;
  onPreview: () => void;
  onPanelSaved: () => void;
}) {
  const filled = sheet.panels_list.filter((p) => (p.description || "").trim()).length;
  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/40">
      <div className="flex items-center gap-2 border-b border-neutral-800 px-3 py-2">
        <span className="text-sm text-neutral-300">
          Trang {sheet.idx + 1} · {sheet.title}
        </span>
        <span className="text-xs text-neutral-600">
          {sheet.cols}×{sheet.rows} · {filled}/{sheet.panels_list.length} panel có mô tả
          {sheet.video_path ? " · đã có video" : ""}
        </span>
        <div className="flex-1" />
        <button
          onClick={onToggle}
          className="rounded-lg border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800"
        >
          {open ? "Thu gọn" : "✎ Panel"}
        </button>
        <button
          onClick={onPrompt}
          title="Xem prompt sẽ gửi đi (không tốn credit)"
          className="rounded-lg border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800"
        >
          ⌕
        </button>
        <button
          onClick={onEditor}
          title="Mở Node Editor cho trang này"
          className="rounded-lg border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800"
        >
          🎛
        </button>
        <button
          onClick={onGen}
          disabled={busy}
          title="Vẽ lại cả trang"
          className="rounded-lg border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
        >
          {busy ? "…" : "✦"}
        </button>
        <button
          onClick={onDelete}
          className="rounded-lg border border-neutral-700 px-2 py-1 text-xs text-neutral-400 hover:bg-neutral-800"
        >
          🗑
        </button>
      </div>

      <div className="p-3">
        <div className="relative">
          <div className="cursor-pointer" onClick={onPreview}>
            <Thumb src={sheet.path} alt={sheet.title} className="aspect-video w-full" />
          </div>
          {busy && (
            <div className="absolute inset-0 grid place-items-center rounded-xl bg-black/60 text-xs text-neutral-200">
              Đang vẽ trang…
            </div>
          )}
        </div>

        {open && (
          <div className="mt-3 space-y-2">
            {sheet.panels_list.map((p) => (
              <PanelRow key={p.id} panel={p} onSaved={onPanelSaved} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PanelRow({ panel, onSaved }: { panel: BoardPanel; onSaved: () => void }) {
  const [v, setV] = useState(panel);
  useEffect(() => setV(panel), [panel]);

  const save = async (patch: Partial<BoardPanel>) => {
    try {
      await boardApi.patchPanel(panel.id, patch);
      onSaved();
    } catch {
      /* lỗi hiện ở banner của tab khi reload */
    }
  };

  const field = (
    key: "caption" | "shot_size" | "lens" | "movement",
    ph: string,
    cls: string
  ) => (
    <input
      value={(v[key] as string) || ""}
      onChange={(e) => setV({ ...v, [key]: e.target.value })}
      onBlur={() => v[key] !== panel[key] && save({ [key]: v[key] })}
      placeholder={ph}
      className={`rounded border border-neutral-700 bg-neutral-950 px-1.5 py-1 text-[11px] outline-none focus:border-indigo-500 ${cls}`}
    />
  );

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950/40 p-2">
      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <span className="grid h-5 w-5 place-items-center rounded-full bg-neutral-200 text-[10px] font-semibold text-neutral-900">
          {panel.idx + 1}
        </span>
        {/* caption = dòng chữ THẬT SỰ được vẽ dưới panel trong ảnh */}
        {field("caption", "toàn cảnh", "w-24")}
        {field("shot_size", "Wide", "w-24")}
        {field("lens", "24mm", "w-16")}
        {field("movement", "tracking back", "w-32")}
      </div>
      <textarea
        value={v.description || ""}
        onChange={(e) => setV({ ...v, description: e.target.value })}
        onBlur={() => v.description !== panel.description && save({ description: v.description })}
        rows={2}
        placeholder="Hành động của panel này — {Tên} để bind ảnh tham chiếu"
        className="w-full resize-y rounded border border-neutral-700 bg-neutral-950 px-1.5 py-1 text-[11px] outline-none focus:border-indigo-500"
      />
    </div>
  );
}
