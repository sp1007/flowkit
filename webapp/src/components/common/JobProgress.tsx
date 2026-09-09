import { useEffect, useState } from "react";
import { useJobs } from "../../jobs/JobsContext";
import type { Job } from "../../api/client";

// Floating banner of active/just-finished batch jobs for the open project (§9).
// Survives tab switches and page reloads because state lives on the server.
const LABEL: Record<string, string> = {
  assets: "Asset",
  storyboard: "Storyboard",
  videos: "Video",
  beats: "Lời đọc + beats",
  revary: "Đa dạng góc máy",
};

// Server chuyển job sang `cancelled` trong khoảng một giây kể từ lúc nhận lệnh (đã đo trên
// job thật đang chạy giữa một lô 4 ảnh). Quá ngần này mà vẫn `running` thì lệnh không tới.
const STOP_GRACE = 8000;

const mmss = (sec: number) => {
  const s = Math.max(0, Math.round(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// Đồng hồ chạy TẠI CHỖ giữa hai nhịp của server: server phát lại mỗi 5s, còn cái này nhích
// mỗi giây từ mốc nhận được gần nhất. Một bước AI/WhisperX kéo 15-20 phút mà banner đứng im
// thì trông y như app treo — đây là thứ nói "vẫn đang chạy".
function useTick(active: boolean): number {
  const [, set] = useState(0);
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => set((n) => n + 1), 1000);
    return () => clearInterval(t);
  }, [active]);
  return Date.now();
}

function statusTone(j: Job): string {
  if (j.status === "running") return "border-indigo-700 bg-indigo-950/70";
  if (j.status === "error") return "border-rose-800 bg-rose-950/70";
  if (j.status === "cancelled") return "border-amber-800 bg-amber-950/70";
  return "border-emerald-800 bg-emerald-950/70";
}

export default function JobProgress() {
  const { jobs, cancel } = useJobs();
  // Job nào đã bấm Dừng, và bấm lúc NÀO. Việc dọn dẹp phía server mất một hai giây (đóng
  // lượt gọi Flow đang dở), và nút im lìm trong lúc đó là thứ khiến người ta bấm đi bấm
  // lại rồi kết luận là nút hỏng.
  //
  // Nhưng cờ này KHÔNG được ở mãi. Đo trên server: bấm dừng thì job chuyển `cancelled`
  // trong khoảng một giây, kể cả đang giữa một lô 4 ảnh. Nên quá STOP_GRACE mà job vẫn
  // `running` nghĩa là lệnh dừng KHÔNG tới nơi — trả nút lại cho người dùng bấm tiếp,
  // đừng khoá nó ở chữ "Đang dừng…" vĩnh viễn (chỉ tải lại trang mới thoát ra được).
  const [stopping, setStopping] = useState<Record<string, number>>({});
  const [stopErr, setStopErr] = useState<Record<string, string>>({});
  const running = jobs.some((j) => j.status === "running");
  const now = useTick(running || Object.keys(stopping).length > 0);
  // Show running jobs + briefly-lingering finished ones (server reaps after a while).
  const visible = jobs.filter((j) => j.status === "running" || j.updated_at * 1000 > now - 20000);
  if (!visible.length) return null;

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[80] flex w-80 flex-col gap-2">
      {visible.map((j) => {
        const pct = Math.round(j.progress * 100);
        // Chỉ coi là "đang dừng" trong hạn STOP_GRACE. Quá hạn mà job vẫn chạy thì lệnh
        // đã rơi đâu đó — mở khoá nút thay vì để người dùng kẹt.
        const waiting = !!stopping[j.id] && now - stopping[j.id] < STOP_GRACE;
        const seen = j.done + j.errors.length;
        // Đồng hồ của item: mốc server gửi + phần trôi từ lúc nhận gói tin đó.
        const elapsed = (j.item_elapsed ?? 0) + Math.max(0, now / 1000 - j.updated_at);
        // Không nhận được nhịp nào quá 20s = có gì đó không ổn (server chết / mất WebSocket).
        const stale = j.status === "running" && now / 1000 - j.updated_at > 20;
        return (
          <div
            key={j.id}
            className={`pointer-events-auto rounded-xl border px-3 py-2.5 text-sm shadow-xl ${statusTone(j)}`}
          >
            <div className="flex items-center gap-2">
              {j.status === "running" && (
                <span
                  title={stale ? "Chưa nhận nhịp nào >20s — kiểm tra server/WebSocket" : "Đang chạy"}
                  className={`h-2 w-2 shrink-0 rounded-full ${
                    stale ? "bg-amber-400" : "animate-pulse bg-indigo-400"
                  }`}
                />
              )}
              <span className="truncate font-medium text-neutral-100">
                {LABEL[j.type] || j.type}: {j.label || `${j.total} mục`}
              </span>
              {j.status === "running" && (
                <button
                  disabled={waiting}
                  title={stopErr[j.id] || (waiting ? "Đã gửi lệnh dừng, đang đợi server" : "Dừng job")}
                  onClick={async () => {
                    setStopping((s) => ({ ...s, [j.id]: Date.now() }));
                    setStopErr((e) => { const n = { ...e }; delete n[j.id]; return n; });
                    try {
                      await cancel(j.id);
                    } catch (e: any) {
                      // Trả nút lại NGAY và nói vì sao. Lỗi hay gặp: job không còn trong bộ
                      // nhớ server (đã restart backend) → 404.
                      setStopping((s) => { const n = { ...s }; delete n[j.id]; return n; });
                      setStopErr((er) => ({ ...er, [j.id]: e?.message || "Không gửi được lệnh dừng" }));
                    }
                  }}
                  className="ml-auto rounded bg-black/30 px-1.5 py-0.5 text-[11px] text-neutral-300 hover:bg-black/50 disabled:opacity-50"
                >
                  {waiting ? "Đang dừng…" : stopErr[j.id] ? "Dừng ↻" : "Dừng"}
                </button>
              )}
              {j.status !== "running" && (
                <span className="ml-auto text-[11px] text-neutral-400">
                  {j.status === "done" ? "✓ xong" : j.status === "cancelled" ? "đã dừng" : "lỗi"}
                </span>
              )}
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-black/40">
              <div
                className={`h-full rounded-full transition-all ${
                  j.status === "running" ? "bg-indigo-400" : j.status === "error" ? "bg-rose-400" : "bg-emerald-400"
                }`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="mt-1 flex items-center justify-between text-[11px] text-neutral-400">
              <span className="truncate">
                {stopErr[j.id]
                  ? `⚠ Dừng không thành: ${stopErr[j.id]}`
                  : j.status === "running" && j.current
                    ? `▶ ${j.current}`
                    : j.message || `${seen}/${j.total}`}
              </span>
              <span className="shrink-0 pl-2">
                {seen}/{j.total}
                {j.errors.length ? ` · ${j.errors.length} lỗi` : ""}
              </span>
            </div>
            {j.status === "running" && (
              <div className="mt-0.5 flex items-center justify-between gap-2 text-[11px]">
                <span className="truncate text-indigo-300/90">
                  {j.step ? `⏳ ${j.step}` : "⏳ đang chạy…"}
                </span>
                <span className={`shrink-0 tabular-nums ${stale ? "text-amber-400" : "text-neutral-500"}`}>
                  {mmss(elapsed)}
                </span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
