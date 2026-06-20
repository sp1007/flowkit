import { useEffect, useRef, useState } from "react";
import {
  listVoices,
  addVoice,
  removeVoice,
  synthesize,
  fileToBase64,
  base64ToAudioUrl,
  type Voice,
} from "../../api/client";
import { useConfirm } from "../common/Confirm";

const SAMPLE = "Xin chào, đây là giọng đọc thử nghiệm cho dự án.";

// Manage OmniVoice voices: list, upload+create a clone, test (synthesize + play), remove.
export default function VoiceManager() {
  const [voices, setVoices] = useState<Voice[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [testText, setTestText] = useState(SAMPLE);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const confirm = useConfirm();

  const load = () => {
    setBusy("load");
    setErr(null);
    listVoices()
      .then(setVoices)
      .catch((e) => setErr(e.message))
      .finally(() => setBusy(null));
  };
  useEffect(load, []);

  const play = async (b64: string) => {
    const url = base64ToAudioUrl(b64);
    if (audioRef.current) {
      audioRef.current.src = url;
      await audioRef.current.play().catch(() => {});
    }
  };

  const test = async (voice_id: number) => {
    setBusy(`test-${voice_id}`);
    setErr(null);
    try {
      const r = await synthesize(testText.trim() || SAMPLE, voice_id);
      if (r.audio) await play(r.audio);
      else setErr("TTS không trả về audio (kiểm tra OmniVoice URL).");
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const create = async () => {
    if (!file) {
      setErr("Chọn file giọng (WAV/MP3) trước.");
      return;
    }
    setBusy("add");
    setErr(null);
    try {
      const b64 = await fileToBase64(file);
      await addVoice(b64, title.trim() || file.name.replace(/\.[^.]+$/, ""));
      setTitle("");
      setFile(null);
      load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  const del = async (v: Voice) => {
    const ok = await confirm({
      title: "Xoá giọng?",
      message: `Giọng "${v.title}" (id ${v.voice_id}) sẽ bị xoá khỏi OmniVoice.`,
      confirmText: "Xoá",
      danger: true,
    });
    if (!ok) return;
    setBusy(`del-${v.voice_id}`);
    setErr(null);
    try {
      await removeVoice(v.voice_id);
      load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">
          Giọng đọc (OmniVoice)
        </span>
        <button
          onClick={load}
          disabled={busy === "load"}
          className="ml-auto text-xs text-neutral-400 hover:text-neutral-200 disabled:opacity-40"
        >
          ↻ Tải lại
        </button>
      </div>

      {err && (
        <div className="rounded-lg bg-rose-950/40 px-3 py-2 text-xs text-rose-300">{err}</div>
      )}

      <input
        value={testText}
        onChange={(e) => setTestText(e.target.value)}
        placeholder="Câu mẫu để test giọng…"
        className={inp}
      />

      <div className="space-y-1.5">
        {voices === null && busy === "load" && (
          <p className="text-xs text-neutral-500">Đang tải danh sách giọng…</p>
        )}
        {voices !== null && !voices.length && (
          <p className="text-xs text-neutral-500">
            Chưa có giọng nào (hoặc OmniVoice chưa kết nối).
          </p>
        )}
        {(voices || []).map((v) => (
          <div
            key={v.voice_id}
            className="flex items-center gap-2 rounded-lg border border-neutral-800 bg-neutral-900/50 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-neutral-200">{v.title}</div>
              <div className="text-[11px] text-neutral-600">id {v.voice_id}</div>
            </div>
            <button
              onClick={() => test(v.voice_id)}
              disabled={!!busy}
              title="Nghe thử giọng"
              className="rounded-md border border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-800 disabled:opacity-40"
            >
              {busy === `test-${v.voice_id}` ? "…" : "▶ Test"}
            </button>
            <button
              onClick={() => del(v)}
              disabled={!!busy}
              title="Xoá giọng"
              className="rounded-md px-2 py-1 text-xs text-rose-400 hover:bg-rose-950/40 disabled:opacity-40"
            >
              {busy === `del-${v.voice_id}` ? "…" : "🗑"}
            </button>
          </div>
        ))}
      </div>

      <div className="rounded-lg border border-dashed border-neutral-800 p-3">
        <div className="mb-2 text-xs text-neutral-400">＋ Thêm giọng mới (clone)</div>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Tên giọng"
          className={`${inp} mb-2`}
        />
        <label className="mb-2 flex cursor-pointer items-center justify-center rounded-lg border border-neutral-700 px-3 py-2 text-xs text-neutral-400 hover:border-indigo-500 hover:text-neutral-200">
          {file ? `🎙 ${file.name}` : "Chọn file giọng mẫu (WAV/MP3)"}
          <input
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
        </label>
        <button
          onClick={create}
          disabled={busy === "add" || !file}
          className="w-full rounded-lg bg-indigo-600 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
        >
          {busy === "add" ? "Đang tạo giọng…" : "Tạo giọng từ file"}
        </button>
      </div>

      <audio ref={audioRef} className="hidden" />
    </div>
  );
}

const inp =
  "w-full rounded-lg border border-neutral-700 bg-neutral-950 px-2.5 py-1.5 text-sm outline-none focus:border-indigo-500";
