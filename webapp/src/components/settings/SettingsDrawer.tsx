import { useEffect, useState } from "react";
import { api, getTtsConfig, setTtsConfig } from "../../api/client";
import VoiceManager from "./VoiceManager";

export default function SettingsDrawer({ onClose }: { onClose: () => void }) {
  const [opts, setOpts] = useState<any>(null);
  const [s, setS] = useState<Record<string, any>>({});
  const [ttsUrl, setTtsUrl] = useState("");
  const [fonts, setFonts] = useState<{ name: string; path: string }[]>([]);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.options().then(setOpts).catch((e) => setErr(e.message));
    api.getSettings().then(setS).catch(() => {});
    // Show the currently-saved OmniVoice URL so it doesn't look lost on reopen.
    getTtsConfig().then((c) => setTtsUrl(c.base_url || "")).catch(() => {});
    api.listFonts().then((r) => setFonts(r.fonts)).catch(() => {});
  }, []);

  const set = (k: string, v: any) => {
    setS((p) => ({ ...p, [k]: v }));
    setSaved(false);
  };

  const save = async () => {
    setErr(null);
    try {
      await api.putSettings(s);
      if (ttsUrl.trim()) await setTtsConfig(ttsUrl.trim());
      setSaved(true);
    } catch (e: any) {
      setErr(e.message);
    }
  };

  const agents = opts?.agents || [];
  const deps = [
    { ok: agents.some((a: any) => a.available), label: "AI agent (claude/agy)" },
    { ok: !!opts, label: "Studio API" },
  ];

  return (
    <div className="fixed inset-0 z-[80] flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="flex h-full w-[420px] flex-col bg-neutral-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
          <h2 className="font-semibold">⚙ Settings</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">✕</button>
        </div>

        <div className="flex-1 space-y-5 overflow-auto p-5">
          {err && <div className="rounded-lg bg-rose-950/40 px-3 py-2 text-sm text-rose-300">{err}</div>}

          <Field label="AI Agent">
            <select value={s.agent || "claude"} onChange={(e) => set("agent", e.target.value)} className={inp}>
              {(agents.length ? agents.map((a: any) => a.key) : ["claude", "antigravity"]).map((k: string) => (
                <option key={k} value={k}>{k}</option>
              ))}
            </select>
          </Field>

          <Field label="Image model">
            <select value={s.image_model || ""} onChange={(e) => set("image_model", e.target.value)} className={inp}>
              <option value="">(mặc định)</option>
              {(opts?.image_models || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>

          <Field label="Style mặc định">
            <input value={s.style || ""} onChange={(e) => set("style", e.target.value)}
              placeholder="vd: Cinematic, teal-orange, 35mm" className={inp} />
            <div className="mt-1.5 flex flex-wrap gap-1">
              {(opts?.style_presets || []).map((p: string) => (
                <button key={p} onClick={() => set("style", p)}
                  className="rounded bg-neutral-800 px-2 py-0.5 text-xs text-neutral-300 hover:bg-neutral-700">{p}</button>
              ))}
            </div>
          </Field>

          <Field label="OmniVoice base URL (TTS)">
            <input value={ttsUrl} onChange={(e) => setTtsUrl(e.target.value)}
              placeholder="https://xxxx.ngrok-free.app" className={inp} />
            <p className="mt-1 text-xs text-neutral-600">
              Đặt URL rồi “Lưu cấu hình” trước khi quản lý/test giọng bên dưới.
            </p>
          </Field>

          <div className="border-t border-neutral-800 pt-4">
            <VoiceManager />
          </div>

          <Field label="Font caption (vẽ chữ lên video)">
            <select value={s.caption_font || ""} onChange={(e) => set("caption_font", e.target.value)} className={inp}>
              <option value="">(tự dò theo hệ điều hành)</option>
              {fonts.map((f) => (
                <option key={f.path} value={f.path}>{f.name}</option>
              ))}
            </select>
          </Field>

          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-neutral-500">Trạng thái</div>
            <div className="space-y-1.5">
              {deps.map((d) => (
                <div key={d.label} className="flex items-center gap-2 text-sm">
                  <span className={`h-2 w-2 rounded-full ${d.ok ? "bg-emerald-400" : "bg-rose-500"}`} />
                  {d.label}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="border-t border-neutral-800 p-4">
          <button onClick={save}
            className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500">
            {saved ? "✓ Đã lưu" : "Lưu cấu hình"}
          </button>
        </div>
      </div>
    </div>
  );
}

const inp = "w-full rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs text-neutral-400">{label}</label>
      {children}
    </div>
  );
}
