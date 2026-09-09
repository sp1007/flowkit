"""Khởi động Flow Kit Next — dùng trực tiếp hoặc đóng gói thành .exe.

Vì sao cần file này thay vì gọi thẳng `python -m agent.main`: mọi đường dẫn trong app
tính từ `Path(__file__).parent.parent`, mà PyInstaller giải nén code vào một thư mục
TẠM. Chạy exe thì `__file__` trỏ vào chỗ tạm ấy, nên DB, media và giao diện đều tìm
không ra — app vẫn khởi động, chỉ là trống rỗng, và đó là kiểu hỏng khó đoán nhất.

Nên ở đây xác định thư mục cài đặt TRƯỚC rồi đặt biến môi trường, sau đó mới nạp app.
Thứ tự đó là bắt buộc: `agent.config` đọc biến môi trường ngay lúc import.

Đóng gói:  pyinstaller flowkit.spec --noconfirm
Chạy:      dist\\FlowKitNext\\FlowKitNext.exe
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _force_utf8_console() -> None:
    """Bắt console in được tiếng Việt.

    Console Windows mặc định cp1252, và một `print` có dấu là ném UnicodeEncodeError rồi
    thoát — tức exe CHẾT NGAY ở dòng chào, trước cả khi server kịp lên. Đã dính đúng lỗi
    này lúc chạy thử.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            # line_buffering: không có nó thì stdout bị đệm theo khối khi bị chuyển hướng,
            # và người mở exe nhìn thấy MÀN HÌNH TRỐNG cho tới lúc bộ đệm đầy — trong khi
            # log của uvicorn đi qua stderr nên hiện ngay. Nhìn như app treo.
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, ValueError):
            pass          # stream bị chuyển hướng hoặc không hỗ trợ — bỏ qua, đừng chết


def _is_data_root(p: Path) -> bool:
    """Thư mục này có phải kho dữ liệu không — nhận ra bằng `agent/` và `webapp/`."""
    return (p / "agent").is_dir() and (p / "webapp").is_dir()


def base_dir() -> Path:
    """Thư mục DỮ LIỆU — nơi chứa agent/studio.db, media/ và webapp/dist.

    Mặc định là thư mục chứa exe, nhưng nếu ở đó không thấy dữ liệu thì ĐI NGƯỢC LÊN tối
    đa ba cấp để tìm. Nhờ vậy `build_dist/FlowKitNext/FlowKitNext.exe` chạy được ngay tại
    chỗ build mà không phải rải 83 MB thư viện ra gốc repo, và chép cả thư mục sang cạnh
    kho dữ liệu thì cũng chạy.

    KHÔNG dùng `sys._MEIPASS` (chỗ PyInstaller giải nén tạm): dữ liệu người dùng không
    nằm ở đó, và nó bị xoá khi thoát — ghi vào đấy là mất trắng.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent
    here = Path(sys.executable).resolve().parent
    if _is_data_root(here):
        return here
    for up in list(here.parents)[:3]:
        if _is_data_root(up):
            return up
    return here          # không tìm thấy thì vẫn dùng chỗ exe, và _check() sẽ kêu


def bundled_dir() -> Path:
    """Nơi PyInstaller giải nén các file đi kèm (models.json, boq_*.json)."""
    return Path(getattr(sys, "_MEIPASS", base_dir()))


def setup_env(root: Path) -> None:
    """Đặt biến môi trường theo thư mục cài đặt — chỉ điền chỗ TRỐNG.

    `setdefault` chứ không gán đè: người dùng đặt sẵn biến nào thì biến ấy thắng, nên vẫn
    trỏ được sang kho dữ liệu ở nơi khác mà không phải sửa exe.
    """
    os.environ.setdefault("FLOW_AGENT_DIR", str(root))
    os.environ.setdefault("STUDIO_DB", str(root / "agent" / "studio.db"))
    os.environ.setdefault("STUDIO_MEDIA_DIR", str(root / "media"))
    os.environ.setdefault("STUDIO_OUT_DIR", str(root / "studio_media"))
    os.environ.setdefault("FLOWKIT_SPA_DIST", str(root / "webapp" / "dist"))
    os.environ.setdefault("FLOWKIT_BOQ_LOG", str(root / "boq_calls.jsonl"))
    # Bản dựng mới sinh ra để chạy trên batchexecute; đường cũ cần token ya29 mà giao
    # diện mới không phát nữa.
    os.environ.setdefault("FLOWKIT_USE_BOQ", "1")


def _open_browser(url: str, delay: float = 2.0) -> None:
    def go():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass          # không mở được trình duyệt thì thôi, server vẫn chạy
    threading.Thread(target=go, daemon=True).start()


def _check(root: Path) -> list[str]:
    """Cảnh báo TRƯỚC khi chạy, thay vì để người dùng gặp một app trống.

    Thiếu DB hay thiếu giao diện thì server vẫn lên và trả 200 ở /health, nên không nói
    ra thì rất khó đoán là thiếu gì.
    """
    warn = []
    if not (root / "agent" / "studio.db").exists():
        warn.append(f"Chưa có CSDL: {root / 'agent' / 'studio.db'}")
    if not (root / "webapp" / "dist").is_dir():
        warn.append(f"Chưa build giao diện: {root / 'webapp' / 'dist'} "
                    f"(chạy `npm install && npm run build` trong webapp/)")
    if not (root / "media").is_dir():
        warn.append(f"Chưa có kho media: {root / 'media'}")
    return warn


def main() -> int:
    _force_utf8_console()
    root = base_dir()
    setup_env(root)

    # Nạp SAU khi đặt biến môi trường — agent.config đọc chúng ngay lúc import.
    from agent.config import API_HOST, API_PORT, WS_PORT

    url = f"http://{API_HOST}:{API_PORT}"
    print("=" * 64)
    print("  Flow Kit Next")
    print(f"  Thư mục : {root}")
    print(f"  Giao diện: {url}")
    print(f"  WebSocket cho extension: ws://{API_HOST}:{WS_PORT}")
    print("=" * 64)
    for w in _check(root):
        print(f"  [!] {w}")
    print("  Mở một tab flow.google.com và VÀO một dự án — khâu sinh cần reCAPTCHA,")
    print("  mà trang giới thiệu chưa khởi động app thì không có.")
    print("  Đóng cửa sổ này là tắt server.")
    print()

    if "--no-browser" not in sys.argv:
        _open_browser(url)

    import uvicorn
    from agent.main import app

    # Truyền THẲNG đối tượng app, không truyền chuỗi "agent.main:app": uvicorn sẽ đi
    # import lại theo tên, mà trong exe thì đường import không giống lúc chạy mã nguồn.
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
