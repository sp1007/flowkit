import { useEffect, useRef, useState, type ReactNode } from "react";
import Thumb from "../Thumb";
import DownloadMenu, { type DownloadChoice } from "./DownloadMenu";
import { downloadFile } from "../../lib/download";

interface Props {
  imageSrc?: string | null;
  videoSrc?: string | null;
  title: string;
  index?: number;
  subtitle?: string | null;
  busy?: boolean;
  busyLabel?: string;
  selected?: boolean;
  actions?: ReactNode;
  // Saves the card's media to disk. `downloadUrl` may point at a DIFFERENT file than the one
  // previewed (a shot card previews the HD clip but downloads the 1080p/4K upscale).
  downloadUrl?: string | null;
  downloadName?: string;
  downloadTitle?: string;
  // Nhiều mốc để chọn (nguyên bản / 1080p / 4K) — có thì ⬇ mở menu, `downloadUrl` bị bỏ qua.
  downloadOptions?: DownloadChoice[];
  onClick?: () => void;
  onPreview?: () => void;
  onEdit?: () => void;
}

// Shared image/video card: media on top, title + description below, hover actions.
export default function MediaCard({
  imageSrc,
  videoSrc,
  title,
  index,
  subtitle,
  busy,
  busyLabel = "Đang tạo…",
  selected,
  actions,
  downloadUrl,
  downloadName,
  downloadTitle,
  downloadOptions,
  onClick,
  onPreview,
  onEdit,
}: Props) {
  // Hàng nút chỉ hiện khi hover; menu ⬇ đang mở thì phải ghim lại, không thì rê chuột
  // xuống chọn mốc là cả cụm tắt mất.
  const [menuOpen, setMenuOpen] = useState(false);
  // Thẻ VIDEO chỉ tồn tại khi ở gần tầm nhìn — gắn khi cuộn tới, THÁO khi cuộn xa.
  //
  // `<img loading="lazy">` được trình duyệt hoãn giúp, `<video>` thì không có gì tương đương:
  // mỗi thẻ vừa gắn là một trình phát + bộ giải mã được cấp phát, cộng một lượt đọc metadata.
  // Đo trên dự án thật "practice" — 127 shot đều có clip, 660 MB — thì API trả shot hết 24ms
  // và mỗi lượt đọc đầu file 4–31ms, tức KHÔNG phải mạng chậm: thứ làm lưới trắng vài phút là
  // 127 phần tử media sống cùng lúc. Bấm ⟳ tháo/gắn lại tất cả nên còn tệ hơn.
  //
  // Tháo khi ra xa là phần bắt buộc: nếu chỉ gắn thêm mà không bao giờ tháo thì cuộn hết lưới
  // là quay về đúng 127 thẻ, chỉ chậm hơn một nhịp. Biên 600px để cuộn bình thường không thấy
  // ô trống. Tab bị ẩn (workspace giữ mọi tab đã mở trong DOM) cũng không giao nhau → các clip
  // của tab không xem tự nhả ra.
  const box = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    if (!videoSrc) return;
    const el = box.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setNear(true);           // môi trường không có IO → giữ hành vi cũ
      return;
    }
    const io = new IntersectionObserver(
      ([e]) => setNear(e.isIntersecting),
      { rootMargin: "600px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [videoSrc]);

  return (
    <div
      className={`group overflow-hidden rounded-xl border bg-neutral-900/50 transition ${
        selected ? "border-indigo-500 ring-1 ring-indigo-500" : "border-neutral-800 hover:border-neutral-600"
      }`}
    >
      <div ref={box} className="relative cursor-pointer" onClick={onClick}>
        {videoSrc ? (
          near ? (
            <video
              key={videoSrc}
              src={videoSrc}
              // `poster` = ảnh frame của shot khi có: vẽ được ngay, khỏi đợi metadata.
              poster={imageSrc || undefined}
              className="aspect-video w-full bg-black object-cover"
              muted
              playsInline
              preload="metadata"
              onMouseEnter={(e) => (e.currentTarget as HTMLVideoElement).play().catch(() => {})}
              onMouseLeave={(e) => {
                const v = e.currentTarget as HTMLVideoElement;
                v.pause();
                v.currentTime = 0;
              }}
            />
          ) : (
            <Thumb src={imageSrc} alt={title} rounded="rounded-none" className="aspect-video w-full" />
          )
        ) : (
          <Thumb src={imageSrc} alt={title} rounded="rounded-none" className="aspect-video w-full" />
        )}

        {busy && (
          <div className="absolute inset-0 grid place-items-center bg-black/60 text-sm text-neutral-200">
            <span className="animate-pulse">{busyLabel}</span>
          </div>
        )}

        <div
          className={`absolute right-1.5 top-1.5 flex gap-1 transition ${
            menuOpen ? "opacity-100" : "opacity-0 group-hover:opacity-100"
          }`}
        >
          {onPreview && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onPreview();
              }}
              title="Phóng to"
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-neutral-700"
            >
              ⤢
            </button>
          )}
          {onEdit && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onEdit();
              }}
              title="Edit (node editor)"
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-neutral-700"
            >
              ✎
            </button>
          )}
          {downloadOptions?.length ? (
            <DownloadMenu
              choices={downloadOptions}
              title={downloadTitle || "Tải về máy"}
              onOpenChange={setMenuOpen}
            />
          ) : null}
          {!downloadOptions?.length && downloadUrl && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                downloadFile(downloadUrl, downloadName || downloadUrl.split("/").pop() || "media");
              }}
              title={downloadTitle || "Tải về máy"}
              className="grid h-7 w-7 place-items-center rounded-md bg-neutral-900/80 text-sm hover:bg-emerald-600"
            >
              ⬇
            </button>
          )}
          {actions}
        </div>
      </div>
      <div className="p-2" onClick={onClick}>
        <div className="flex items-center gap-1.5">
          {index != null && (
            <span className="text-xs text-neutral-500">{String(index + 1).padStart(2, "0")}</span>
          )}
          <span className="truncate text-sm font-medium">{title}</span>
        </div>
        {subtitle && <p className="mt-0.5 line-clamp-2 text-xs text-neutral-500">{subtitle}</p>}
      </div>
    </div>
  );
}
