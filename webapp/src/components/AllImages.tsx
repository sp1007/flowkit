import { useEffect, useMemo, useState } from "react";
import { api, thumbUrl, type AllMediaItem } from "../api/client";
import Thumb from "./Thumb";
import Lightbox from "./common/Lightbox";

// Gallery of every image across all Flow projects, with name/project search.
export default function AllImages() {
  const [items, setItems] = useState<AllMediaItem[] | null>(null);
  const [q, setQ] = useState("");
  const [proj, setProj] = useState("");
  const [lightbox, setLightbox] = useState<AllMediaItem | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => {
    setItems(null);
    setErr(null);
    api.allFlowMedia().then((r) => setItems(r.media)).catch((e) => setErr(e.message));
  };
  useEffect(load, []);

  const projects = useMemo(
    () => Array.from(new Set((items || []).map((m) => m.project_title).filter(Boolean))).sort(),
    [items]
  );

  const filtered = (items || []).filter((m) => {
    if (proj && m.project_title !== proj) return false;
    if (!q.trim()) return true;
    return `${m.name} ${m.project_title}`.toLowerCase().includes(q.toLowerCase());
  });

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Tất cả ảnh</h1>
          <p className="text-sm text-neutral-400">
            {items === null ? "Đang quét các project Flow…" : `${filtered.length}/${items.length} ảnh`}
            {items !== null ? ` · ${projects.length} dự án` : ""}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Tìm theo tên ảnh / dự án…"
            className="w-64 rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"
          />
          <select
            value={proj}
            onChange={(e) => setProj(e.target.value)}
            className="w-52 rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"
          >
            <option value="">Tất cả dự án</option>
            {projects.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <button
            onClick={load}
            title="Tải lại"
            className="rounded-lg border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-800"
          >
            ↻
          </button>
        </div>
      </div>

      {err && (
        <div className="mb-4 rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">
          {err}
        </div>
      )}
      {items === null && (
        <div className="py-16 text-center text-sm text-neutral-500">Đang tải ảnh từ tất cả project…</div>
      )}
      {items !== null && !filtered.length && (
        <div className="rounded-2xl border border-dashed border-neutral-800 py-16 text-center text-neutral-500">
          Không có ảnh nào khớp.
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
        {filtered.map((m) => (
          <button
            key={m.flow_project_id + m.media_id}
            onClick={() => setLightbox(m)}
            className="group overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900/50 text-left transition hover:border-indigo-500"
          >
            <Thumb src={thumbUrl(m.media_id)} alt={m.name} rounded="rounded-none" className="aspect-square w-full" />
            <div className="p-2">
              <div className="truncate text-xs font-medium">{m.name || "—"}</div>
              <div className="truncate text-[11px] text-neutral-500">{m.project_title}</div>
            </div>
          </button>
        ))}
      </div>

      {lightbox && (
        <Lightbox
          imageSrc={thumbUrl(lightbox.media_id)}
          title={`${lightbox.name} · ${lightbox.project_title}`}
          onClose={() => setLightbox(null)}
        />
      )}
    </div>
  );
}
