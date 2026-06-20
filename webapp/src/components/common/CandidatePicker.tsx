import { useEffect, useState } from "react";
import { api, graphApi, type Candidate } from "../../api/client";

// Generate N candidate images, show them, let the user pick the best one → commit via
// apply-media (video-app.md §13#2). `kind` decides entity vs shot-frame.
export default function CandidatePicker({
  kind,
  id,
  title,
  n = 3,
  onApplied,
  onClose,
}: {
  kind: "entity" | "shot";
  id: string;
  title?: string;
  n?: number;
  onApplied: (record: any) => void;
  onClose: () => void;
}) {
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(true);
  const [applying, setApplying] = useState<string | null>(null);

  const load = () => {
    setCands(null);
    setErr(null);
    setBusy(true);
    const p = kind === "entity" ? api.entityCandidates(id, n) : api.shotCandidates(id, n);
    p.then((r) => setCands(r.candidates))
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(false));
  };
  useEffect(load, [kind, id, n]);

  const pick = async (c: Candidate) => {
    setApplying(c.media_id);
    setErr(null);
    try {
      const r = await graphApi.applyMedia(kind, id, c.media_id);
      onApplied(kind === "shot" ? r.shot : r.entity);
      onClose();
    } catch (e: any) {
      setErr(e.message);
      setApplying(null);
    }
  };

  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/60 p-6" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-neutral-800 bg-neutral-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-neutral-800 px-5 py-3">
          <h3 className="font-semibold">🎲 Chọn ảnh đẹp nhất{title ? ` — ${title}` : ""}</h3>
          <button
            onClick={load}
            disabled={busy}
            className="ml-auto rounded-md border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
          >
            ↻ Tạo lại
          </button>
          <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">✕</button>
        </div>

        <div className="flex-1 overflow-auto p-5">
          {err && <div className="mb-3 rounded-lg bg-rose-950/40 px-3 py-2 text-sm text-rose-300">{err}</div>}
          {busy && (
            <p className="text-sm text-indigo-300">
              <span className="mr-2 inline-block h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
              Đang tạo {n} ảnh ứng viên…
            </p>
          )}
          {cands && (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {cands.map((c) => (
                <button
                  key={c.media_id}
                  onClick={() => pick(c)}
                  disabled={!!applying}
                  className="group overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900/50 transition hover:border-indigo-500 disabled:opacity-50"
                >
                  <div className="relative">
                    <img src={c.web} alt="candidate" className="aspect-video w-full object-cover" />
                    <div className="absolute inset-0 grid place-items-center bg-black/0 transition group-hover:bg-black/50">
                      <span className="rounded-md bg-indigo-600 px-2 py-1 text-xs text-white opacity-0 transition group-hover:opacity-100">
                        {applying === c.media_id ? "Đang áp dụng…" : "✓ Chọn ảnh này"}
                      </span>
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
