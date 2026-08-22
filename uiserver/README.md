# FlowKit Launcher

Một cửa sổ chạy hết các server của FlowKit, thay cho 3 cửa sổ command mở tay.

```
uiserver/
  flowkit_launcher.py   # toàn bộ app (tkinter, không cần thư viện ngoài)
  build.ps1             # đóng gói ra FlowKitLauncher.exe
  config.json           # cấu hình, tự tạo lần chạy đầu, tự lưu khi thoát
  FlowKitLauncher.exe   # sản phẩm build (không commit)
```

## Chạy

```powershell
.\FlowKitLauncher.exe          # bản đã build
python flowkit_launcher.py     # hoặc chạy thẳng bằng Python 3.10+
```

Build lại: `.\build.ps1` (tự cài `pyinstaller` nếu thiếu).

## Có gì

- **Start All / Stop All / Restart All** — chỉ những server có tick *"Nằm trong Start All"*
  mới nằm trong Start All; Start All khởi động lần lượt, cách nhau `start_all_delay_ms`
  (mặc định 800ms) để backend lên trước web UI.
- **Start / Stop / ↻ / 🌐 từng server** — 🌐 mở `url` của server trong trình duyệt.
- **Ô thư mục + ô lệnh cho từng server**, sửa trực tiếp trên giao diện, có nút `…` chọn thư mục.
- **Ô socket riêng cho TTS** — `{socket}` trong lệnh được thay bằng nội dung ô này.
  Bỏ trống thì `run_bridge.ps1` chạy chế độ AUTO (đọc URL tunnel từ Google Drive).
- **Log như console thật**: giữ nguyên màu ANSI (kể cả 256 màu và truecolor), `\r` ghi đè
  dòng nên progress bar chạy tại chỗ, đọc byte thô nên chữ hiện ngay chứ không đợi đủ dòng.
  Có *Tự cuộn*, *Xoá log*, *Lưu log ra file*. Giữ tối đa 5000 dòng mỗi tab.
- **Console riêng (cửa sổ ngoài)** — tick vào thì server chạy trong cửa sổ cmd thật (dùng khi
  lệnh cần bàn phím hoặc cần tty), lúc đó tab log chỉ ghi trạng thái.
- **Trạng thái** — chấm màu + pid + thời gian chạy, cập nhật mỗi giây.
- Đóng app sẽ hỏi rồi dừng hết server đang chạy, và lưu cấu hình.

## config.json

```json
{
  "servers": [
    {
      "id": "backend",
      "name": "Backend (agent)",
      "cwd": "D:\\youtube\\editor\\flowkit",
      "command": "python -m agent.main",
      "url": "http://127.0.0.1:8100/health",
      "autostart": true,
      "own_console": false
    }
  ],
  "start_all_delay_ms": 800,
  "autostart_on_launch": false
}
```

- `socket_field: true` + `socket` — chỉ server TTS dùng, sinh ra ô socket trên giao diện.
- `autostart_on_launch` — bật thì app tự Start All ngay khi mở (tick trên thanh công cụ).
- Thêm server mới = thêm một mục vào `servers` (cần `id` khác nhau) rồi mở lại app.

## Ghi chú kỹ thuật

- Mỗi server chạy dưới `cmd.exe` (`shell=True`), nên `npm run dev` (thực chất là `npm.cmd`)
  và `powershell -File ...` đều chạy được như gõ tay trong terminal.
- Dừng bằng `taskkill /PID <pid> /T /F` — giết cả cây, nếu không thì `node`/`python` con
  vẫn giữ cổng sau khi cmd.exe cha thoát.
- Tiến trình con chạy không có tty nên CLI thường tự tắt màu; app đặt sẵn `FORCE_COLOR=1`,
  `CLICOLOR_FORCE=1`, `TERM=xterm-256color`, `PYTHONUNBUFFERED=1` để log vẫn giống console.
