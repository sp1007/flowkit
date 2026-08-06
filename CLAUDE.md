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
- **Hai tab vẽ hình, đừng lẫn.** Tab **Illustrators** (bảng `shot`) là tab Storyboard cũ: sinh
  TỪNG ảnh rời, giờ chỉ để minh hoạ và làm nguồn cho `Export to DaVinci (images)` — hành vi giữ
  nguyên, đừng sửa. Tab **Storyboard** mới (`board_sheet` + `board_panel`) vẽ MỘT TRANG 4/6 panel
  trong MỘT lượt: cùng một bức tranh thì bối cảnh/ánh sáng/trang phục không thể lệch, đó là lý do
  nó tồn tại. **Trang KHÔNG bị cắt** — chính bức ảnh nguyên vẹn đi thẳng sang tab Shots làm
  reference DUY NHẤT cho một clip, và badge số tròn vẽ sẵn trong ảnh là thứ chỉ panel nào là
  panel nào (thay cho token `{handle}` mà clip nhiều-ảnh phải dùng). Vì vậy `board_panel` chỉ giữ
  CHỮ: không media_id, không cột video. `Export to DaVinci (video)` chỉ lấy video của trang, không
  fallback ảnh. Xem [agent/api/board.py](agent/api/board.py).
- **`_SHEET_PAGE` và `_SINGLE_FRAME` phủ định nhau.** Một khối bắt vẽ lưới nhiều panel kèm badge
  và caption, khối kia cấm lưới và cấm MỌI chữ trong ảnh. `compose_prompt` chọn đúng một trong
  hai (`sheet_page=(cols,rows)` thắng `single_frame`) — ghép cả hai là prompt tự mâu thuẫn và kết
  quả thành hên xui. Prompt video của trang cũng phải nói rõ trang là BẢN VẼ KẾ HOẠCH: không dặn
  thì model cho chính cái lưới chuyển động. Và đừng kê sẵn nước máy cho nó — chuyển cảnh giữa các
  panel là việc của AI, ràng buộc duy nhất là hợp lý về vật lý.
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
- **Một trang chỉ bind được MỘT lần mỗi ảnh, nên VỊ TRÍ bind quyết định tất cả.** Bind mọi lần
  nhắc thì trang 6 panel ra 30 reference part → Flow 400, nên `dedupe_refs=True` là bắt buộc.
  Nhưng khi chỉ được một lần, để token rơi vào giữa câu panel 1 thì ảnh chỉ ràng buộc panel đó
  còn 5 panel sau trôi theo chữ — đúng cái đã làm phố Hàng Mã (đường rộng, cây, sạp hoa quả)
  biến thành một ngõ đèn lồng generic. `brain.sheet_page_prompt` vì thế gom mỗi reference lên
  ĐẦU trong khối SETTING/SUBJECTS ("ảnh này định nghĩa chỗ này cho MỌI panel") và `_strip_braces`
  bỏ hết ngoặc trong mô tả panel — mỗi ngoặc còn sót lại sẽ ăn mất lượt bind khỏi khối đầu.
  Kèm theo, `sheet_autofill_prompt` cấm mô tả panel nhắc tới phố/đèn/thời tiết/ánh sáng: chữ tả
  cảnh cạnh tranh trực tiếp với ảnh tham chiếu và model theo chữ.
- **Ảnh location là LƯỚI 2x2 — phải nói cho model biết nó đang nhìn cái gì.** `entity.media_id`
  của location là một tờ contact sheet bốn góc máy. Illustrators dùng được vì mỗi frame là một
  lượt sinh riêng và `_SINGLE_FRAME` cấm vẽ lưới nên model tự chọn một ô. Trang storyboard thì
  hỏng nếu im lặng: prompt bảo "ảnh này LÀ con phố" trong khi đưa một tờ bốn ảnh khác nhau, và
  chính cái lưới ấy còn đánh nhau với lệnh dựng lưới 3x2 — đã đo: tham chiếu là phố rộng có cây
  và sạp hoa quả, trang vẽ ra là ngõ hẹp treo đèn lồng. Cách chữa: lưới location tự mang **badge
  số 1–4** (`_SHEET["location"]`, cùng kiểu badge của trang storyboard), và
  `brain.location_setting_clause` chỉ đích danh **frame 1** là con phố, 2–4 chỉ để tra chi tiết.
  Khối ấy dùng chung cho cả đường tự động (`sheet_page_prompt`) lẫn node editor của trang
  (`graph.py`, `kind="sheet"`) — hai đường cùng vẽ một thứ thì phải nhận cùng lời dặn. Đừng cắt ô
  ra rồi upload lại: thêm một ảnh rác cho mỗi location mà không hơn gì.
- **Ảnh mẫu của location chỉ có MỘT góc, phải bắt ba ô còn lại mang đặc điểm của nó sang.** Câu
  "bám theo ảnh" chung chung không đủ: ô 2/3/4 là góc máy KHÔNG có trong ảnh mẫu nên model bịa,
  mà bịa thì rơi về khuôn phố cổ generic — đã đo với ảnh thật phố Hàng Mã: ô 1 giữ được cây và
  mái bạt, ô 2–4 thành dãy nhà ống hẹp không cây. `_SHEET["location"]` vì thế nói thẳng ô 1 tái
  hiện ảnh mẫu, ô 2–4 là CÙNG chỗ ấy nhìn từ nơi khác và phải mang theo đúng bề rộng đường, cây,
  mái bạt, loại hàng, biển hiệu. Và đừng viết tên cú máy thành tiêu đề in hoa (`1 WIDE
  ESTABLISHING — …`): model hiểu đó là nhãn cần vẽ và in luôn bốn chữ ấy vào ảnh.
- **"Vắng người" ≠ "dọn sạch".** Câu `COMPLETELY EMPTY — no people, no animals` của lưới location
  từng làm model kéo cửa cuốn xuống và dọn hết sạp: phố còn đúng cây với mặt đường, mất sạch hàng
  hoá và màu — mà với phố chợ thì hàng hoá CHÍNH LÀ bối cảnh. Phải nói tách bạch: không người,
  nhưng chỗ đó vẫn đang mở cửa buôn bán, sạp đầy ắp, hàng treo, đèn sáng đúng như ảnh mẫu.
- **Đừng để thông số máy đứng cạnh số panel.** `Panel 1 [Wide, 24mm, tracking back]:` trông y như
  một nhãn viết sẵn và model chép nguyên cụm xuống làm caption — trang ra chữ
  `toàn cảnh [Wide, 24mm, tracking back]`. Tách thành câu riêng đặt SAU hành động ("Shoot it
  wide, 24mm, tracking back.") thì nó đọc như lời dặn và caption ra đúng tiếng Việt.
- **Khối SETTING ở đầu trôi mất trước khi model đọc tới panel.** Prompt trang dài ~9700 ký tự;
  đặt lời dặn bối cảnh ở đầu là đúng (lượt bind reference phải nằm đó) nhưng KHÔNG đủ — đã tái
  hiện được: cùng một prompt, hai lượt liền đều cho ngõ hẹp generic dù frame 1 là phố rộng có cây.
  `sheet_page_prompt` vì thế nhắc lại NGAY TRƯỚC danh sách panel, bằng TÊN THƯỜNG không ngoặc —
  lượt bind đã tiêu ở khối SETTING, thêm ngoặc ở đây chỉ ăn mất nó khỏi chỗ có ích hơn.
- **Số panel phải nói bằng số VÀ cấm bỏ sót.** "6 panels in a 3x2 grid" không đủ: model vẽ 5 ô,
  đánh số 1,2,3,5,6, hàng trên 2 ô hàng dưới 3 — tái hiện được, không phải lượt xui. Phải nói rõ
  "{rows} hàng, mỗi hàng {cols} ô bằng nhau", "vẽ ĐỦ {n} panel", và nói thẳng rằng thiếu một ô
  hay đứt số là SAI kể cả khi từng ô đẹp.
- **Đừng lưu thân prompt tự sinh vào `board_sheet.prompt`.** Cột đó là chỗ NGƯỜI DÙNG ghi đè;
  ghi bản tự sinh vào đấy thì mọi lượt vẽ sau tái dùng thân cũ, sửa panel hay sửa cách dựng
  prompt đều không có tác dụng. Muốn xem thân đang gửi thì gọi `/sheets/{id}/prompt-preview`.
- **`structuredPrompt` bị băm vụn = Flow trả 400.** Mỗi token KHÔNG bind (token lạ, hoặc ảnh đã
  bind rồi khi `dedupe`) cắt đoạn văn làm đôi, nên `_build_structured_parts` phải GỘP mảnh text
  vào part liền trước chứ không tạo part mới. Trang storyboard 30 token từng ra 37 part → 400
  `INVALID_ARGUMENT`; gộp lại còn 3 part → chạy, cùng nguyên văn prompt. Triệu chứng dễ đánh
  lừa: agent báo *"Flow không trả media (có thể bị chặn)"* (vì `res["error"]` rỗng) chứ không
  báo 400 — muốn thấy mã lỗi thật thì gửi lại qua `POST /api/flow/generate-image`, endpoint đó
  bóc `status` + `error` ra. Cũng đừng đoán là do prompt dài: đã đo 9306 ký tự chạy tốt, còn
  6078 ký tự vẫn hỏng khi part bị vụn. Kèm theo: `dedupe_refs=True` bind mỗi ẢNH đúng một lần —
  bắt buộc cho prompt nhắc cùng một entity ở nhiều panel.
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
