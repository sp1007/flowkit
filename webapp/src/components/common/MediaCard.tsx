import { useState, type ReactNode } from "react";
import Thumb from "../Thumb";
import DownloadMenu, { type DownloadChoice } from "./DownloadMenu";
import { downloadFile } from "../../lib/download";

interface Props {
  imageSrc?: string | null;
  videoSrc?: string | null;
  /** Ảnh RẺ thay mặt cho clip trên lưới (khung đầu, JPEG ~35KB) — xem GET /shots/{id}/poster.
   *  Chỉ cần khi thẻ có `videoSrc` mà không có `imageSrc`. */
  posterSrc?: string | null;
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
  posterSrc,
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
  // Lưới hiển thị ẢNH, không phải <video>. Thẻ video chỉ được gắn trong lúc RÊ CHUỘT.
  //
  // Nhúng <video> cho mỗi thẻ nghe thì tiện, nhưng clip Flow phát ra đều có `moov` ở CUỐI
  // file (kiểm 6/6: ftyp/uuid/mdat/moov), nên `preload="metadata"` buộc trình duyệt lần tới
  // cuối một file 4–9MB. Lưới 127 clip = hàng trăm MB và 127 phần tử media sống cùng lúc →
  // treo trình duyệt. Gắn/tháo theo tầm nhìn cũng không cứu được: cuộn vài nhịp là churn
  // liên tục, mỗi nhịp lại dựng một bộ giải mã và một lượt đọc mới.
  //
  // Ảnh thay mặt (`posterSrc`) là JPEG ~35KB do server dựng sẵn, `<img loading="lazy">` lo
  // được phần hoãn tải. Rê chuột mới gắn <video>, nên nhiều nhất MỘT thẻ video sống một lúc.
  const [hover, setHover] = useState(false);
  const still = imageSrc || posterSrc || null;

  return (
    <div
      className={`group overflow-hidden rounded-xl border bg-neutral-900/50 transition ${
        selected ? "border-indigo-500 ring-1 ring-indigo-500" : "border-neutral-800 hover:border-neutral-600"
      }`}
    >
      <div
        className="relative cursor-pointer"
        onClick={onClick}
        onMouseEnter={() => videoSrc && setHover(true)}
        onMouseLeave={() => setHover(false)}
      >
        <Thumb src={still} alt={title} rounded="rounded-none" className="aspect-video w-full" />
        {videoSrc && hover && (
          <video
            key={videoSrc}
            src={videoSrc}
            poster={still || undefined}
            className="absolute inset-0 h-full w-full bg-black object-cover"
            muted
            playsInline
            autoPlay
            preload="metadata"
          />
        )}
        {videoSrc && !hover && (
          <span className="pointer-events-none absolute bottom-1.5 left-1.5 rounded bg-black/60 px-1 py-0.5 text-[10px] text-neutral-200">
            ▶
          </span>
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
