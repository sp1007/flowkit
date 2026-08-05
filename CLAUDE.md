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
  (`shot.continuity` + khối `brain._CONTINUITY` trong prompt autofill). Tab Shots gom
  `project.clip_frames` frame liền nhau thành một CLIP (`shot.clip_id`, frame `clip_idx=0` là
  frame dẫn và giữ video của cả nhóm) rồi render bằng MỘT lượt Omni Flash r2v. Trần cứng 6
  (`clips.HARD_MAX_CLIP_FRAMES`) vì clip dài nhất chỉ 10s; hạ `clip_frames` xuống là các nhóm
  đang có tự tách ra theo. Veo là i2v (một ảnh start) nên KHÔNG render được clip gộp. Quy tắc
  gom nằm ở [agent/studio/clips.py](agent/studio/clips.py) và `assembler` phải chia nhóm y hệt,
  không thì lời đọc của các frame sau trong nhóm biến mất khỏi video cuối.
- **Một frame một tên: `sc001-s01-mô-tả`** (`shot.media_name`) — dùng chung cho tên hiển thị
  trên Flow, tên file export, nhãn trong app VÀ làm handle reference của frame. Đổi thứ tự shot
  thì tên được đặt lại. Prompt timeline của clip gọi frame bằng token `{sc001-s01-mô-tả}`:
  ngoặc nhọn là cú pháp DUY NHẤT Flow bind (`flow_client._build_structured_parts`, cùng cơ chế
  `{handle}` của Node Editor) — viết kiểu khác thì ảnh vẫn đi kèm request nhưng model không
  biết khoảnh khắc nào thuộc reference nào. Vì thế `_slug` phải bỏ `{}` khỏi tên.
- **Reference không được prompt gọi tên = coi như không có.** `imageInputs` chỉ đính ảnh vào
  request; thứ thật sự khiến model bám theo ảnh là reference part trong `structuredPrompt`, mà
  `_build_structured_parts` chỉ tạo cho `{handle}` XUẤT HIỆN trong prompt. Ảnh gửi kèm nhưng
  không được gọi tên thì kết quả trả về như một lượt sinh mới, chẳng liên quan gì tới ảnh tham
  chiếu. `edit_image` xử lý bằng `base_part`; `generate_images` có cờ `bind_unreferenced=True`
  cho nơi BIẾT CHẮC ảnh phải được bám vào (node "Tạo ảnh"/"Thay nền" của Node Editor — người
  dùng nối ảnh vào là có chủ đích). ĐỪNG bật ở chỗ references chỉ là kho ứng viên để prompt tự
  chọn theo tên (ảnh storyboard, candidates): bind một entity mà shot không nhắc tới là mời
  model vẽ nhân vật đó vào khung hình.
- **Frame của một scene neo vào frame DẪN, và ảnh thắng chữ.** Mỗi frame là một lượt sinh độc
  lập; neo duy nhất từng có là lưới location 2x2 (bốn ô nhỏ, model tự chọn một ô), nên hai frame
  liền nhau ra hai nơi khác hẳn là chuyện đã xảy ra. Nay `_scene_anchor` đính frame đã vẽ sớm
  nhất của scene làm reference cho các frame sau và `brain.scene_anchor_clause` gọi tên nó bằng
  token — vì thế `_start_image_job` phải chạy frame dẫn của MỌI scene xong trước (`group_key`
  của job manager cắt lô theo pha; sắp lại thứ tự thôi không đủ vì lô cắt cứng theo
  `batch_size`). Kèm theo: `_SINGLE_FRAME` tuyên bố chữ lệch ảnh thì ẢNH THẮNG — mô tả frame do
  LLM viết từ `entity.description` còn ảnh location là do model vẽ, hai bên lệch nhau là thường,
  không phân xử thì model theo chữ và dựng lại cả con phố. Trần 8 reference của Flow là cứng nên
  `_build_frame_references(reserve=1)` phải chừa chỗ cho neo.
- **Asset có hai đầu vào ảnh, đừng lẫn.** `entity.ref_media` = ảnh MẪU người dùng đính vào để
  ✦ vẽ bám theo (đầu VÀO); `entity.media_id` là ảnh KẾT QUẢ; `entity.extra_media` là các góc
  phụ sinh thêm của location (đầu RA). Nút ✦ (`/entities/{eid}/generate`) chạy `graph_json`
  nếu entity đã có graph — trước đây nó luôn sinh lại từ chữ nên ai dựng sẵn tham chiếu trong
  Node Editor rồi bấm ✦ sẽ mất sạch mà không báo gì.
- **Đừng kê sẵn chuyển động cho model.** `brain.clip_timeline_prompt` cố ý KHÔNG liệt kê nước
  máy ("lùi dần", "đẩy vào"): đưa menu vào thì mọi clip ra cùng một khuôn và các frame biến
  thành checklist để tick. Token chỉ đánh dấu khoảnh khắc TRÔNG ra sao, không phải lệnh cắt
  cảnh; frame nói cú máy phải đi qua đâu, còn đi kiểu gì — kể cả đứng yên — là model tự chọn
  theo hành động. Nhịp thời gian dùng chung khối `_OMNI_TIMELINE_HEAD` sẵn có.
- `media_id` is always UUID format (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`), never `CAMS...`
- The agent holds no state; all generation goes through the connected extension.
  If `extension_connected: false`, open Google Flow in Chrome with the extension loaded.
- See [README.md](README.md) for the full endpoint table.
