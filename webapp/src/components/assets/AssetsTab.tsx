import { useEffect, useState } from "react";
import { api, type Entity, type LibraryEntity, type Project } from "../../api/client";
import type { EditorTarget } from "../nodeeditor/NodeEditor";
import Thumb from "../Thumb";
import Lightbox from "../common/Lightbox";

const GROUPS: { type: Entity["type"]; label: string }[] = [
  { type: "character", label: "Nhân vật" },
  { type: "location", label: "Bối cảnh" },
  { type: "prop", label: "Đạo cụ" },
];

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export default function AssetsTab({
  project,
  onEdit,
}: {
  project: Project;
  onEdit?: (t: EditorTarget) => void;
}) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [gening, setGening] = useState<Set<string>>(new Set());
  const [lightbox, setLightbox] = useState<Entity | null>(null);
  const [picker, setPicker] = useState<
    { mode: "import" } | { mode: "link"; entity: Entity } | null
  >(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () =>
    api.listEntities(project.id).then((r) => setEntities(r.entities)).catch(() => {});
  useEffect(() => {
    load();
  }, [project.id]);

  const wrap = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    setErr(null);
    try {
      await fn();
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const genOne = async (e: Entity): Promise<boolean> => {
    setGening((s) => new Set(s).add(e.id));
    setErr(null);
    try {
      const updated = await api.generateEntity(e.id);
      setEntities((list) => list.map((x) => (x.id === e.id ? updated : x)));
      return true;
    } catch (ex: any) {
      setErr(ex.message);
      return false;
    } finally {
      setGening((s) => {
        const n = new Set(s);
        n.delete(e.id);
        return n;
      });
    }
  };

  // Auto gen on the client so each image shows its "Đang tạo…" overlay live and we
  // can report progress + which ones failed (backend already verifies + retries).
  const autoGen = async () => {
    const todo = entities.filter((e) => !e.image_path);
    if (!todo.length) {
      setErr("Tất cả asset đã có ảnh.");
      return;
    }
    setBusy("all");
    setErr(null);
    let okN = 0;
    const failed: string[] = [];
    for (let i = 0; i < todo.length; i++) {
      setProgress(`Đang tạo ${i + 1}/${todo.length}: ${todo[i].name}`);
      const ok = await genOne(todo[i]);
      ok ? okN++ : failed.push(todo[i].name);
      if (i < todo.length - 1) await sleep(2000 + Math.random() * 4000);
    }
    setProgress(null);
    setBusy(null);
    if (failed.length) setErr(`Xong ${okN}/${todo.length}. Lỗi: ${failed.join(", ")}`);
  };

  const addManual = async (type: Entity["type"]) => {
    const name = prompt(`Tên ${type}?`);
    if (!name) return;
    wrap("add", () => api.addEntity(project.id, { type, name }));
  };

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-6xl px-6 py-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Thư viện Asset</h2>
          <p className="text-sm text-neutral-400">
            Nhân vật, bối cảnh, đạo cụ — ảnh tham chiếu cho storyboard
          </p>
        </div>
        <div className="flex gap-2">
          <button
            disabled={!!busy}
            onClick={() => setPicker({ mode: "import" })}
            title="Dùng asset có sẵn từ dự án khác (thư viện chung)"
            className="rounded-lg border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-800 disabled:opacity-40"
          >
            📚 Dùng từ dự án khác
          </button>
          <button
            disabled={!!busy}
            onClick={() => wrap("extract", () => api.extractEntities(project.id))}
            className="rounded-lg border border-neutral-700 px-3 py-2 text-sm hover:bg-neutral-800 disabled:opacity-40"
          >
            {busy === "extract" ? "Đang trích…" : "Trích từ kịch bản"}
          </button>
          <button
            disabled={!!busy}
            onClick={autoGen}
            className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            {busy === "all" ? "Đang tạo…" : "✦ Auto gen"}
          </button>
        </div>
      </div>

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

      {GROUPS.map((g) => {
        const items = entities.filter((e) => e.type === g.type);
        return (
          <section key={g.type} className="mb-8">
            <div className="mb-3 flex items-center gap-2">
              <h3 className="text-sm font-medium uppercase tracking-wide text-neutral-400">
                {g.label}
              </h3>
              <span className="text-xs text-neutral-600">{items.length}</span>
              <button
                onClick={() => addManual(g.type)}
                className="ml-auto rounded-md px-2 py-1 text-xs text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200"
              >
                + Thêm
              </button>
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
              {items.map((e) => (
                <AssetCard
                  key={e.id}
                  entity={e}
                  generating={gening.has(e.id)}
                  onPreview={e.image_path ? () => setLightbox(e) : undefined}
                  onLink={() => setPicker({ mode: "link", entity: e })}
                  onGenerate={() => genOne(e)}
                  onCover={
                    e.media_id
                      ? () => wrap("cover", () => api.setCover(project.id, e.media_id!))
                      : undefined
                  }
                  onDelete={() => wrap("del", () => api.deleteEntity(e.id))}
                  onEdit={
                    onEdit
                      ? () =>
                          onEdit({
                            kind: "entity",
                            id: e.id,
                            title: e.name,
                            prompt: e.description || e.ref_prompt || e.name,
                            imageSrc: e.image_path,
                          })
                      : undefined
                  }
                />
              ))}
              {!items.length && (
                <div className="col-span-full rounded-xl border border-dashed border-neutral-800 py-8 text-center text-xs text-neutral-600">
                  Chưa có {g.label.toLowerCase()}.
                </div>
              )}
            </div>
          </section>
        );
      })}
      </div>

      {lightbox && (
        <Lightbox imageSrc={lightbox.image_path} title={lightbox.name} onClose={() => setLightbox(null)} />
      )}

      {picker && (
        <AssetPicker
          projectId={project.id}
          title={
            picker.mode === "link"
              ? `🔗 Tham chiếu vào "${picker.entity.name}"`
              : "📚 Asset từ dự án khác"
          }
          actionLabel={picker.mode === "link" ? "Tham chiếu" : "+ Dùng"}
          onClose={() => setPicker(null)}
          onPick={async (e) => {
            if (picker.mode === "link") {
              await api.linkEntity(picker.entity.id, e.id);
            } else {
              await api.importEntity(project.id, e.id);
            }
            await load();
          }}
        />
      )}
    </div>
  );
}

function AssetCard({
  entity,
  generating,
  onPreview,
  onLink,
  onGenerate,
  onCover,
  onDelete,
  onEdit,
}: {
  entity: Entity;
  generating: boolean;
  onPreview?: () => void;
  onLink?: () => void;
  onGenerate: () => void;
  onCover?: () => void;
  onDelete: () => void;
  onEdit?: () => void;
}) {
  return (
    <div className="group overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900/50">
      <div className="relative">
        <div
          className={onPreview ? "cursor-zoom-in" : undefined}
          onClick={onPreview}
        >
          <Thumb
            src={entity.image_path}
            alt={entity.name}
            rounded="rounded-none"
            className="aspect-video w-full"
          />
        </div>
        {generating && (
          <div className="absolute inset-0 grid place-items-center bg-black/60 text-sm text-neutral-200">
            <span className="animate-pulse">Đang tạo ảnh…</span>
          </div>
        )}
        <div className="absolute right-1.5 top-1.5 flex gap-1 opacity-0 transition group-hover:opacity-100">
          {onPreview && (
            <button
              onClick={onPreview}
              title="Phóng to"
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-neutral-700"
            >
              ⤢
            </button>
          )}
          <button
            onClick={onGenerate}
            disabled={generating}
            title="Gen nhanh"
            className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-indigo-600"
          >
            ⚡
          </button>
          {onLink && (
            <button
              onClick={onLink}
              title="Tham chiếu ảnh từ asset dự án khác (giữ nguyên tên)"
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-sky-600"
            >
              🔗
            </button>
          )}
          {onEdit && (
            <button
              onClick={onEdit}
              title="Edit (node editor)"
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-neutral-700"
            >
              ✎
            </button>
          )}
          {onCover && (
            <button
              onClick={onCover}
              title="Đặt làm ảnh đại diện dự án"
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-amber-600"
            >
              ★
            </button>
          )}
          <button
            onClick={onDelete}
            title="Xóa"
            className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-rose-600"
          >
            🗑
          </button>
        </div>
      </div>
      <div className="p-2">
        <div className="truncate text-sm font-medium">{entity.name}</div>
        {entity.description && (
          <p className="mt-0.5 line-clamp-2 text-xs text-neutral-500">{entity.description}</p>
        )}
      </div>
    </div>
  );
}

const TYPE_LABEL: Record<string, string> = {
  character: "Nhân vật",
  location: "Bối cảnh",
  prop: "Đạo cụ",
};

// Picker to reuse an asset from ANY other project (shared asset library).
// `onPick` decides what happens (import as new entity, or link onto an existing one).
function AssetPicker({
  projectId,
  title,
  actionLabel,
  onClose,
  onPick,
}: {
  projectId: string;
  title: string;
  actionLabel: string;
  onClose: () => void;
  onPick: (e: LibraryEntity) => Promise<void> | void;
}) {
  const [items, setItems] = useState<LibraryEntity[] | null>(null);
  const [q, setQ] = useState("");
  const [importing, setImporting] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api
      .libraryEntities(projectId)
      .then((r) => setItems(r.entities))
      .catch((e) => setErr(e.message));
  }, [projectId]);

  const filtered = (items || []).filter((e) => {
    const s = `${e.name} ${e.project_title} ${e.type}`.toLowerCase();
    return s.includes(q.toLowerCase());
  });

  const doImport = async (e: LibraryEntity) => {
    setImporting(e.id);
    setErr(null);
    try {
      await onPick(e);
      onClose();
    } catch (ex: any) {
      setErr(ex.message);
    } finally {
      setImporting(null);
    }
  };

  // group by source project
  const byProject: Record<string, LibraryEntity[]> = {};
  for (const e of filtered) (byProject[e.project_title] ??= []).push(e);

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-neutral-800 px-5 py-3">
          <h3 className="font-semibold">{title}</h3>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Tìm theo tên / dự án…"
            className="ml-auto w-64 rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"
          />
          <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">✕</button>
        </div>

        <div className="flex-1 overflow-auto p-5">
          {err && <div className="mb-3 rounded-lg bg-rose-950/40 px-3 py-2 text-sm text-rose-300">{err}</div>}
          {items === null && <p className="text-sm text-neutral-500">Đang tải…</p>}
          {items !== null && !filtered.length && (
            <p className="text-sm text-neutral-500">Không có asset nào ở dự án khác.</p>
          )}
          {Object.entries(byProject).map(([proj, list]) => (
            <section key={proj} className="mb-6">
              <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">{proj}</h4>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
                {list.map((e) => (
                  <button
                    key={e.id}
                    onClick={() => doImport(e)}
                    disabled={!!importing}
                    className="group overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900/50 text-left transition hover:border-indigo-500 disabled:opacity-50"
                  >
                    <div className="relative">
                      <Thumb src={e.image_path} alt={e.name} rounded="rounded-none" className="aspect-video w-full" />
                      <div className="absolute inset-0 grid place-items-center bg-black/0 transition group-hover:bg-black/50">
                        <span className="rounded-md bg-indigo-600 px-2 py-1 text-xs text-white opacity-0 transition group-hover:opacity-100">
                          {importing === e.id ? "Đang xử lý…" : actionLabel}
                        </span>
                      </div>
                    </div>
                    <div className="p-2">
                      <div className="truncate text-sm font-medium">{e.name}</div>
                      <div className="text-xs text-neutral-500">{TYPE_LABEL[e.type] || e.type}</div>
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
