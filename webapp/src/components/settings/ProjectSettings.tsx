import { useEffect, useRef, useState } from "react";
import {
  api,
  listVoices,
  synthesize,
  base64ToAudioUrl,
  projectExportUrl,
  type Project,
  type Voice,
} from "../../api/client";

// Per-project settings: prompt header/footer (always prepended/appended to every
// image & video prompt), culture hint, style, and the image model.
export default function ProjectSettings({
  project,
  onClose,
  onSaved,
}: {
  project: Project;
  onClose: () => void;
  onSaved: (p: Project) => void;
}) {
  const [opts, setOpts] = useState<any>(null);
  const [s, setS] = useState({
    style: project.style ?? "",
    script_lang: project.script_lang ?? "Vietnamese",
    image_text_lang: project.image_text_lang ?? "Vietnamese",
    culture_hint: project.culture_hint ?? "",
    prompt_header: project.prompt_header ?? "",
    prompt_footer: project.prompt_footer ?? "",
    image_model: project.image_model ?? "",
    aspect_ratio: project.aspect_ratio ?? "VIDEO_ASPECT_RATIO_LANDSCAPE",
    video_model: project.video_model ?? "",
  });
  const [shotDuration, setShotDuration] = useState<number>(project.shot_duration ?? 8);
  const [storytelling, setStorytelling] = useState<boolean>(!!project.storytelling);
  const [seed, setSeed] = useState<number>(project.seed ?? 0);
  const [bgmPath, setBgmPath] = useState(project.bgm_path ?? null);
  const [bgmVol, setBgmVol] = useState(project.bgm_volume ?? 0.18);
  const [bgmDuck, setBgmDuck] = useState<boolean>(project.bgm_duck == null ? true : !!project.bgm_duck);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState<number>(project.voice_id ?? 0);
  const [ttsSpeed, setTtsSpeed] = useState<number>(project.tts_speed ?? 1.0);
  const [ttsGap, setTtsGap] = useState<number>(project.tts_gap ?? 0.4);
  const [ttsSentenceGap, setTtsSentenceGap] = useState<number>(project.tts_sentence_gap ?? 0.3);
  const [testing, setTesting] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    api.options().then(setOpts).catch(() => {});
    listVoices().then(setVoices).catch(() => {});
  }, []);

  const testVoice = async () => {
    setTesting(true);
    setErr(null);
    try {
      const r = await synthesize("Xin chào, đây là giọng đọc của dự án.", voiceId, ttsSpeed);
      if (r.audio && audioRef.current) {
        audioRef.current.src = base64ToAudioUrl(r.audio);
        await audioRef.current.play().catch(() => {});
      } else setErr("TTS không trả về audio (kiểm tra OmniVoice URL trong Settings).");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setTesting(false);
    }
  };

  const set = (k: keyof typeof s, v: string) => setS((p) => ({ ...p, [k]: v }));

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.updateProject(project.id, {
        ...s,
        bgm_volume: bgmVol,
        bgm_duck: bgmDuck,
        voice_id: voiceId,
        shot_duration: shotDuration,
        storytelling,
        tts_speed: ttsSpeed,
        tts_gap: ttsGap,
        tts_sentence_gap: ttsSentenceGap,
        seed,
      });
      onSaved(updated);
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const onPickBgm = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.uploadBgm(project.id, file, bgmVol);
      setBgmPath(updated.bgm_path ?? null);
      onSaved(updated);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const removeBgm = async () => {
    setBusy(true);
    setErr(null);
    try {
      const updated = await api.clearBgm(project.id);
      setBgmPath(null);
      onSaved(updated);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  // ── Export / import REUSABLE settings (not project content) so the same setup can be
  // applied to other projects without redoing it by hand. BGM file isn't included (it's media).
  const STR_KEYS = ["style", "script_lang", "image_text_lang", "culture_hint",
    "prompt_header", "prompt_footer", "image_model", "aspect_ratio", "video_model"] as const;
  const NUM_KEYS = ["shot_duration", "seed", "bgm_volume", "voice_id",
    "tts_speed", "tts_gap", "tts_sentence_gap"] as const;
  const BOOL_KEYS = ["storytelling", "bgm_duck"] as const;

  const exportSettings = () => {
    const payload: any = {
      _type: "flowkit-project-settings", version: 1, ...s,
      shot_duration: shotDuration, storytelling, seed, bgm_volume: bgmVol, bgm_duck: bgmDuck,
      voice_id: voiceId, tts_speed: ttsSpeed, tts_gap: ttsGap, tts_sentence_gap: ttsSentenceGap,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `flowkit-settings-${(project.title || "project").replace(/[^\w-]+/g, "_")}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const importSettings = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setErr(null);
    setMsg(null);
    try {
      const obj = JSON.parse(await file.text());
      if (!obj || typeof obj !== "object") throw new Error("File không hợp lệ");
      const fields: any = {};
      for (const k of STR_KEYS) if (typeof obj[k] === "string") fields[k] = obj[k];
      for (const k of NUM_KEYS) if (typeof obj[k] === "number") fields[k] = obj[k];
      for (const k of BOOL_KEYS) if (typeof obj[k] === "boolean") fields[k] = obj[k];
      if (!Object.keys(fields).length) throw new Error("Không có thiết lập hợp lệ trong file");
      const u = await api.updateProject(project.id, fields);
      // reflect the applied values back into the form
      setS((p) => ({
        style: u.style ?? p.style, script_lang: u.script_lang ?? p.script_lang,
        image_text_lang: u.image_text_lang ?? p.image_text_lang, culture_hint: u.culture_hint ?? p.culture_hint,
        prompt_header: u.prompt_header ?? p.prompt_header, prompt_footer: u.prompt_footer ?? p.prompt_footer,
        image_model: u.image_model ?? p.image_model, aspect_ratio: u.aspect_ratio ?? p.aspect_ratio,
        video_model: u.video_model ?? p.video_model,
      }));
      if (u.shot_duration != null) setShotDuration(u.shot_duration);
      if (u.storytelling != null) setStorytelling(!!u.storytelling);
      if (u.seed != null) setSeed(u.seed);
      if (u.bgm_volume != null) setBgmVol(u.bgm_volume);
      if (u.bgm_duck != null) setBgmDuck(!!u.bgm_duck);
      if (u.voice_id != null) setVoiceId(u.voice_id);
      if (u.tts_speed != null) setTtsSpeed(u.tts_speed);
      if (u.tts_gap != null) setTtsGap(u.tts_gap);
      if (u.tts_sentence_gap != null) setTtsSentenceGap(u.tts_sentence_gap);
      onSaved(u);
      setMsg(`Đã nhập & áp dụng ${Object.keys(fields).length} thiết lập.`);
    } catch (e: any) {
      setErr("Nhập thiết lập lỗi: " + (e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const bgmName = bgmPath ? bgmPath.replace(/\\/g, "/").split("/").pop() : null;

  return (
    <div className="fixed inset-0 z-[80] flex justify-end bg-black/50" onClick={onClose}>
      <div
        className="flex h-full w-[440px] flex-col bg-neutral-950 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-800 px-5 py-3">
          <h2 className="font-semibold">⚙ Cấu hình dự án</h2>
          <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">✕</button>
        </div>

        <div className="flex-1 space-y-5 overflow-auto p-5">
          {err && <div className="rounded-lg bg-rose-950/40 px-3 py-2 text-sm text-rose-300">{err}</div>}
          {msg && <div className="rounded-lg bg-emerald-950/40 px-3 py-2 text-sm text-emerald-300">{msg}</div>}

          <Field label="Style (luôn được đưa lên đầu mỗi prompt)">
            <input value={s.style} onChange={(e) => set("style", e.target.value)}
              placeholder="vd: chibi ghibli, watercolor" className={inp} />
          </Field>

          <Field label="Ngôn ngữ kịch bản / lời thoại / lời đọc">
            <input value={s.script_lang} onChange={(e) => set("script_lang", e.target.value)}
              placeholder="Tiếng Việt" className={inp} />
            <p className="mt-1 text-xs text-neutral-600">
              Kịch bản, hội thoại, lời đọc (voiceover) và SEO sẽ viết bằng ngôn ngữ này (mặc định
              Tiếng Việt). Áp dụng cho các lần sinh/sửa kịch bản sau.
            </p>
          </Field>

          <Field label="Ngôn ngữ chữ viết/vẽ trong ảnh">
            <input value={s.image_text_lang} onChange={(e) => set("image_text_lang", e.target.value)}
              placeholder="Tiếng Việt" className={inp} />
            <p className="mt-1 text-xs text-neutral-600">
              Mọi chữ/biển/nhãn hiện trong ảnh sẽ viết bằng ngôn ngữ này (mặc định Tiếng Việt). Từ đặc
              thù ngôn ngữ khác (vd thuật ngữ/nhãn hiệu tiếng Anh) được giữ nguyên.
            </p>
          </Field>

          <Field label="Culture hint (tự nhận từ kịch bản — phong cách văn hoá)">
            <textarea value={s.culture_hint} onChange={(e) => set("culture_hint", e.target.value)}
              placeholder="vd: Vietnamese folk tale, traditional Vietnamese architecture, áo dài…"
              className={`${inp} h-20 resize-none`} />
            <p className="mt-1 text-xs text-neutral-600">
              Giữ hình ảnh đúng với gốc câu chuyện (truyện VN ra phong cách VN, truyện Nhật ra Nhật…).
            </p>
          </Field>

          <Field label="Prompt header (chèn vào ĐẦU mỗi prompt ảnh/video)">
            <textarea value={s.prompt_header} onChange={(e) => set("prompt_header", e.target.value)}
              placeholder="vd: always output in Vietnamese" className={`${inp} h-16 resize-none`} />
          </Field>

          <Field label="Prompt footer (chèn vào CUỐI mỗi prompt ảnh/video)">
            <textarea value={s.prompt_footer} onChange={(e) => set("prompt_footer", e.target.value)}
              placeholder="vd: super detailed, aspect ratio 16:9, cinematic lighting, 8k, sharp focus"
              className={`${inp} h-16 resize-none`} />
          </Field>

          <Field label="Image model">
            <select value={s.image_model} onChange={(e) => set("image_model", e.target.value)} className={inp}>
              <option value="">(mặc định)</option>
              {(opts?.image_models || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
            </select>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Khung hình">
              <select value={s.aspect_ratio} onChange={(e) => set("aspect_ratio", e.target.value)} className={inp}>
                <option value="VIDEO_ASPECT_RATIO_LANDSCAPE">16:9 ngang</option>
                <option value="VIDEO_ASPECT_RATIO_PORTRAIT">9:16 dọc</option>
              </select>
            </Field>
            <Field label="Độ dài shot (giây)">
              <input type="number" min={1} max={10} value={shotDuration}
                onChange={(e) => setShotDuration(Math.min(10, Math.max(1, Number(e.target.value) || 8)))}
                className={inp} />
            </Field>
          </div>

          <Field label="Video model">
            <select value={s.video_model} onChange={(e) => set("video_model", e.target.value)} className={inp}>
              <option value="">(mặc định)</option>
              {(opts?.video_models?.veo_tiers || []).length > 0 && (
                <optgroup label="Veo (i2v)">
                  {(opts?.video_models?.veo_tiers || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
                </optgroup>
              )}
              {(opts?.video_models?.omni_flash_durations || []).length > 0 && (
                <optgroup label="Omni Flash (r2v)">
                  {(opts?.video_models?.omni_flash_durations || []).map((m: string) => <option key={m} value={m}>{m}</option>)}
                </optgroup>
              )}
            </select>
          </Field>

          <label className="flex items-center gap-2 text-sm text-neutral-300">
            <input type="checkbox" checked={storytelling}
              onChange={(e) => setStorytelling(e.target.checked)}
              className="h-4 w-4 accent-indigo-500" />
            Chế độ Storytelling (giọng đọc dẫn dắt, đọc nguyên văn nội dung gốc)
          </label>

          <Field label="🔒 Seed (khóa để tái lập ảnh giống hệt)">
            <input type="number" min={0} value={seed}
              onChange={(e) => setSeed(Math.max(0, Number(e.target.value) || 0))}
              placeholder="0 = ngẫu nhiên" className={inp} />
            <p className="mt-1 text-xs text-neutral-600">
              Đặt số &gt; 0 để mọi lần tạo ảnh dùng cùng seed → tái tạo giống nhau (cùng prompt/ref).
              0 hoặc trống = ngẫu nhiên. (Tạo nhiều mẫu 🎲 vẫn random để có lựa chọn.)
            </p>
          </Field>

          <Field label="🎙 Giọng đọc (lồng tiếng dự án)">
            <div className="flex gap-2">
              <select
                value={voiceId}
                onChange={(e) => setVoiceId(Number(e.target.value))}
                className={inp}
              >
                <option value={0}>Mặc định (id 0)</option>
                {voices.map((v) => (
                  <option key={v.voice_id} value={v.voice_id}>
                    {v.title} (id {v.voice_id})
                  </option>
                ))}
              </select>
              <button
                onClick={testVoice}
                disabled={testing}
                title="Nghe thử giọng đã chọn"
                className="shrink-0 rounded-lg border border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-800 disabled:opacity-40"
              >
                {testing ? "…" : "▶ Test"}
              </button>
            </div>
            <div className="mt-2 flex items-center gap-3">
              <span className="text-xs text-neutral-500">Tốc độ đọc</span>
              <input type="range" min={0.5} max={1.5} step={0.05} value={ttsSpeed}
                onChange={(e) => setTtsSpeed(parseFloat(e.target.value))}
                className="flex-1 accent-indigo-500" />
              <span className="w-10 text-right text-xs tabular-nums text-neutral-400">
                {ttsSpeed.toFixed(2)}×
              </span>
            </div>
            <div className="mt-2 flex items-center gap-3">
              <span className="text-xs text-neutral-500">Nghỉ giữa đoạn</span>
              <input type="range" min={0} max={2} step={0.05} value={ttsGap}
                onChange={(e) => setTtsGap(parseFloat(e.target.value))}
                className="flex-1 accent-indigo-500" />
              <span className="w-10 text-right text-xs tabular-nums text-neutral-400">
                {ttsGap.toFixed(2)}s
              </span>
            </div>
            <p className="mt-1 text-xs text-neutral-600">
              Khoảng lặng nghỉ lấy hơi giữa các đoạn/shot. Đặt ≈1.0s (24 frame) nếu dùng
              cross-dissolve để hiệu ứng nằm trọn trong khoảng lặng. Cần "Dựng theo lời đọc" lại.
            </p>
            <div className="mt-2 flex items-center gap-3">
              <span className="text-xs text-neutral-500">Nghỉ giữa câu</span>
              <input type="range" min={0} max={1} step={0.05} value={ttsSentenceGap}
                onChange={(e) => setTtsSentenceGap(parseFloat(e.target.value))}
                className="flex-1 accent-indigo-500" />
              <span className="w-10 text-right text-xs tabular-nums text-neutral-400">
                {ttsSentenceGap.toFixed(2)}s
              </span>
            </div>
            <p className="mt-1 text-xs text-neutral-600">
              Mỗi câu được đọc riêng và chèn khoảng lặng này ở mỗi dấu chấm/câu, để giọng đọc
              ngừng nghỉ tự nhiên thay vì đọc liền tù tì. Cần "Dựng theo lời đọc" lại.
            </p>
            <p className="mt-1 text-xs text-neutral-600">
              Quản lý / thêm giọng trong ⚙ Settings. Cần đặt OmniVoice URL để test.
            </p>
            <audio ref={audioRef} className="hidden" />
          </Field>

          <Field label="🎵 Nhạc nền (tự trộn dưới giọng đọc khi ghép video)">
            {bgmName ? (
              <div className="flex items-center justify-between rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm">
                <span className="truncate text-neutral-200">🎵 {bgmName}</span>
                <button onClick={removeBgm} disabled={busy}
                  className="ml-2 shrink-0 text-rose-400 hover:text-rose-300 disabled:opacity-40">
                  Gỡ
                </button>
              </div>
            ) : (
              <label className="flex cursor-pointer items-center justify-center rounded-lg border border-dashed border-neutral-700 px-3 py-3 text-sm text-neutral-400 hover:border-indigo-500 hover:text-neutral-200">
                {busy ? "Đang tải…" : "＋ Chọn file nhạc (mp3, wav, m4a…)"}
                <input type="file" accept="audio/*" className="hidden"
                  onChange={(e) => onPickBgm(e.target.files?.[0])} />
              </label>
            )}
            <div className="mt-2 flex items-center gap-3">
              <span className="text-xs text-neutral-500">Âm lượng nhạc</span>
              <input type="range" min={0} max={0.6} step={0.02} value={bgmVol}
                onChange={(e) => setBgmVol(parseFloat(e.target.value))}
                className="flex-1 accent-indigo-500" />
              <span className="w-10 text-right text-xs tabular-nums text-neutral-400">
                {Math.round(bgmVol * 100)}%
              </span>
            </div>
            <label className="mt-2 flex items-center gap-2 text-sm text-neutral-300">
              <input type="checkbox" checked={bgmDuck}
                onChange={(e) => setBgmDuck(e.target.checked)}
                className="h-4 w-4 accent-indigo-500" />
              Tự giảm nhạc khi có giọng đọc (ducking)
            </label>
            <p className="mt-1 text-xs text-neutral-600">
              Giọng đọc giữ nguyên âm lượng. Bật ducking: nhạc tự nhỏ lại lúc đang đọc và to lên ở
              khoảng lặng. Tắt: nhạc giữ mức cố định ở trên. Bỏ trống file → không chèn nhạc.
            </p>
          </Field>
        </div>

        <div className="space-y-2 border-t border-neutral-800 p-4">
          <div className="flex gap-2">
            <button
              onClick={exportSettings}
              title="Tải các THIẾT LẬP của dự án (style, prompt header/footer, model, TTS, BGM volume…) thành .json để tái dùng cho dự án khác"
              className="flex-1 rounded-lg border border-neutral-700 py-2 text-center text-sm text-neutral-300 hover:bg-neutral-800"
            >
              ⤓ Xuất thiết lập
            </button>
            <label
              title="Nạp thiết lập từ file .json và áp dụng ngay cho dự án này (không đụng tới nội dung/kịch bản/ảnh)"
              className="flex-1 cursor-pointer rounded-lg border border-neutral-700 py-2 text-center text-sm text-neutral-300 hover:bg-neutral-800"
            >
              ⤒ Nhập thiết lập
              <input type="file" accept="application/json,.json" className="hidden" disabled={busy}
                onChange={(e) => { importSettings(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
          </div>
          <a
            href={projectExportUrl(project.id)}
            download
            className="block rounded-lg border border-neutral-700 py-2 text-center text-sm text-neutral-300 hover:bg-neutral-800"
            title="Tải dự án (rows DB + media) thành .zip để sao lưu / chuyển máy"
          >
            ⬇ Xuất dự án (.zip)
          </a>
          <button onClick={save} disabled={busy}
            className="w-full rounded-lg bg-indigo-600 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40">
            {busy ? "Đang lưu…" : "Lưu cấu hình dự án"}
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
