# Flow Kit

Minimal Google Flow API proxy: FastAPI + WebSocket server (`agent/`) + Chrome
extension (`extension/`). No local DB, no queue, no skills — a pure relay to the
Google Flow API via the extension.

Base URL: `http://127.0.0.1:8100`

## Pre-flight

```bash
curl -s http://127.0.0.1:8100/health
# Must return: {"status":"ok", "extension_connected": true, ...}
```

## Run

```bash
python -m agent.main   # HTTP on :8100, extension WebSocket on :9222
```

## Layout

- `agent/main.py` — app entry, extension WebSocket, `/health`, `/api/ext/callback`
- `agent/api/flow.py` — all `/api/flow/*` endpoints
- `agent/api/tts.py` — `/api/tts/*` proxy to the OmniVoice server on Google Colab
  (set the rotating Colab URL via `PUT /api/tts/config` or `OMNIVOICE_BASE_URL`)
- `agent/api/ai_agent.py` — `/api/agent/*` runs coding-agent CLIs (Claude Code,
  Antigravity) headless as subprocesses. Registry in `config.py` (`AI_AGENTS`),
  env-overridable. Defaults to bypassing CLI permissions — local-only.
- `agent/services/flow_client.py` — relays requests to the extension over WS
- `agent/services/headers.py` — randomized headers
- `agent/config.py`, `agent/models.json` — endpoints + model keys
- `extension/` — Chrome MV3 extension (token capture, reCAPTCHA, Flow calls)

## Notes

- **Mỗi dự án thuộc về một tài khoản Flow.** Extension đọc account đang đăng nhập từ
  `labs.google/fx/api/auth/session` và đẩy lên agent; `project.account_id` ghi lại chủ sở
  hữu. `/studio/projects` chỉ trả dự án của account hiện tại, mọi endpoint đụng tới dự án
  của account khác trả 403. Chưa xác định được account → không lọc, chỉ cảnh báo trên UI.
  Xem [agent/studio/accounts.py](agent/studio/accounts.py).
- **Hai kiểu nhạc, đừng lẫn.** `project.bgm_path` = MỘT bài trộn chìm dưới lời đọc (⚙ cấu
  hình dự án). Bảng `music_track` = playlist nhiều bài của chế độ music video
  (`project.music_mode`): nhạc là tiếng duy nhất, các bài cách nhau `music_gap` giây, và tổng
  thời lượng playlist quyết định độ dài video — hình được lặp cho phủ kín, thừa thì cắt.
  Xem [agent/studio/music.py](agent/studio/music.py) + tab "Nhạc" trong workspace.
- **Frame ≠ clip — số shot hai tab KHÔNG bằng nhau.** Tab Storyboard cắt scene thành FRAME:
  các khoảnh khắc chính của MỘT cú máy liên tục, nên frame liền nhau phải nối được vào nhau
  (`shot.continuity` + khối `brain._CONTINUITY` trong prompt autofill). Tab Shots gom tối đa 6
  frame liền nhau thành một CLIP (`shot.clip_id`, frame `clip_idx=0` là frame dẫn và giữ video
  của cả nhóm) rồi render bằng MỘT lượt Omni Flash r2v: mọi frame là reference `frame 1..N` và
  prompt là timeline gọi tên chúng (`[00:00] mở ở (frame 1), máy lùi dần sang (frame 2)…`).
  Trần 6 vì clip dài nhất chỉ 10s. Veo là i2v (một ảnh start) nên KHÔNG render được clip gộp.
  Quy tắc gom nằm ở [agent/studio/clips.py](agent/studio/clips.py) và `assembler` phải chia
  nhóm y hệt, không thì lời đọc của các frame sau trong nhóm biến mất khỏi video cuối.
- **Một frame một tên: `sc001-s01-mô-tả`** (`shot.media_name`) — dùng chung cho tên hiển thị
  trên Flow, tên file export và nhãn trong app. Đổi thứ tự shot thì tên được đặt lại.
- `media_id` is always UUID format (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`), never `CAMS...`
- The agent holds no state; all generation goes through the connected extension.
  If `extension_connected: false`, open Google Flow in Chrome with the extension loaded.
- See [README.md](README.md) for the full endpoint table.
