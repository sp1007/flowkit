# Chuẩn bị cho ngày `labs.google` tắt

Nhánh `flow-new-stack`. `main` vẫn chạy đường cũ và vẫn hoạt động — đây là đường DỰ PHÒNG,
dựng trước để lúc Google đóng giao thức cũ thì có cái mà dùng.

## Vì sao không phải chỉ đổi vài URL

Flow đã chuyển giao diện sang `flow.google.com` (app Angular BOQ). Đo ngày 2026-09-08:

| Thứ | Đường cũ (`labs.google`) | Đường mới (`flow.google.com`) |
|---|---|---|
| Vận chuyển | gọi thẳng `aisandbox-pa.googleapis.com` từ trình duyệt | `POST /_/AiSandboxAngularFrontend/data/batchexecute` |
| Auth | `Authorization: Bearer ya29.…` | **cookie phiên + `at` (XSRF)** |
| Dự án / media | tRPC `project.*`, `media.getMediaUrlRedirect` | rpcid trong batchexecute |
| reCAPTCHA | trong trang labs | trong trang `/project/<uuid>`, **cùng site key** |
| Host ảnh | `storage.googleapis.com/ai-sandbox-videofx` | `flow-content.google` (Cloud CDN ký) |
| Host video | như trên | `googlevideo.com/videoplayback`, ràng theo IP, hết hạn ~2h |

Điểm chết người ở dòng **Auth**: token `ya29.*` mà extension đang bắt là do app Next.js ở
`labs.google` phát ra (NextAuth, lộ ở `/fx/api/auth/session`). Giao diện mới **không phát
bearer nào**. Nên `labs.google` tắt không chỉ mất mấy endpoint tRPC — mất NGUỒN TOKEN, và
mọi lời gọi thẳng `aisandbox-pa` chết theo, kể cả khâu sinh ảnh/video.

Đường batchexecute thì không cần token: cookie phiên của chính tab Flow là đủ.

## Chạy song song, không đụng bản chính

Nhánh này cố ý lệch cổng và lệch tên để cài được ở một trình duyệt KHÁC trong khi bản chính
vẫn đang chạy:

| | bản chính (`main`) | nhánh này |
|---|---|---|
| HTTP | 8100 | **8200** |
| WebSocket (extension nối vào) | 9222 | **9200** |
| Vite dev | 5173 | **5200** |
| Tên extension | Flow Kit | **Flow Kit Next** |

Dữ liệu tự tách sẵn: `BASE_DIR` là thư mục worktree nên `agent/studio.db` và `media/` của
nhánh này là bộ RIÊNG, khởi đầu rỗng — không đọc, không ghi đè dự án của bản chính.

```bash
cd D:/youtube/editor/flowkit-next
python -m agent.main          # HTTP :8200, WS :9200
curl -s http://127.0.0.1:8200/health
```

Rồi ở trình duyệt thứ hai (Edge, Chrome profile khác, Brave…): `chrome://extensions` →
Load unpacked → `D:\youtube\editorlowkit-next\extension`. Đăng nhập Google, mở một dự án
`flow.google.com/project/<id>`.

Hai extension KHÔNG được cùng chạy trong một trình duyệt: cả hai đều nghe `webRequest` trên
cùng những URL và đều tự mở tab Flow, nên sẽ giành nhau tab và token.

Đổi cổng thì phải sửa BỐN chỗ, không có nguồn chung: `agent/config.py`, `AGENT_WS_URL` và
host permission của extension, `webapp/vite.config.ts`.

## Đã có gì trên nhánh này

- `boqExecute(rpcid, args)` trong [extension/background.js](../extension/background.js) —
  đọc `at`/`f.sid`/`bl` từ `WIZ_global_data` của một tab `flow.google.com/project/*`, POST
  đúng dạng `f.req=[[["<rpcid>","<json>",null,"generic"]]]`, và `parseBoqResponse` tách phản
  hồi (bỏ tiền tố `)]}'`, đọc từng khối theo độ dài, lấy envelope `wrb.fr`, parse payload
  **lần hai** vì nó là chuỗi JSON lồng).
- `POST /api/flow/boq` `{rpcid, args, source_path}` — gọi thử một rpcid.
- Bộ ghi recon: `webRequest` nghe mọi batchexecute của tab thật, đẩy về agent, ghi ra
  `boq_calls.jsonl`. Đọc bằng `GET /api/flow/boq/log`.
- Host media không còn hardcode GCS (`MEDIA_URL_RE`).

## Bảng rpcid (đo trực tiếp, 2026-09-08)

| rpcid | args | việc |
|---|---|---|
| `ogiZ0b` | xem dưới | **tạo ảnh** |
| `mYWVGd` | `[<workflow>, [["metadata.display_name"]]]` | **sửa workflow theo field mask** (đổi tên ảnh) |
| `UpteDb` | `["projects/*", 21, null, null, null, null, [1]]` | liệt kê dự án |
| `Zzl0ze` | `["projects/<id>", null, null, null, [1]]` | đọc một dự án |
| `ngNC2` | `["tools/PINHOLE/projects/<id>"]` | trạng thái dự án theo tool |
| `nzlxg` | `[]` → `[12439,2,3,3,null,12439]` | credit còn lại |
| `mrlkwd` | `["<projectId>"]` | chưa rõ |
| `xI9TVb` | `["userPreferences/"]` | tuỳ chọn người dùng |
| `o30O0e` | `[["me"], …person.name/email…]` | hồ sơ tài khoản |
| `Kcr7Ub` / `DA4VGb` | `…["agent_toggle_state"]` | lưu trạng thái UI |
| `WuwhI` | `PAGE_VIEW …` | telemetry, bỏ qua |
| `DTaVef`, `NfrxTb`, `KV2T2d`, `LPzVkd`, `cPZSdc`, `Yizz8d`, `ve2Lsc`, `HTrJv`, `tRARke`, `qJcgMc`, `yBhWQ` | hằng số nhỏ | cấu hình / cờ tính năng |

### API mới là CRUD tài nguyên + FIELD MASK, không phải một rpcid cho mỗi việc

Đây là quy luật quan trọng nhất đọc ra được — nó quyết định cách port:

```
mYWVGd  [ [ "<workflowId>", null, null, ["<tên mới>"], "<projectId>" ],   // tài nguyên
          [ ["metadata.display_name"] ] ]                                 // field mask
Kcr7Ub  [ "projects/<id>", [ … ], [ ["agent_toggle_state"] ] ]            // cùng dạng
```

Tham số thứ hai là **mặt nạ trường** kiểu `updateMask` của Google API. Nghĩa là không phải
dò một rpcid riêng cho mỗi thao tác sửa: cùng `mYWVGd` đổi được trường khác chỉ bằng cách
đổi tên trường trong mặt nạ. Tương tự, `UpteDb` (`"projects/*"`) và `Zzl0ze`
(`"projects/<id>"`) là list/get dùng chung theo ĐƯỜNG DẪN TÀI NGUYÊN.

Đối tượng workflow (trả về ở cả `mYWVGd` lẫn `ogiZ0b`) có dạng:

```
[ "<workflowId>", null, null,
  [ "<tên hiển thị>", [<ts tạo>], null, null, "<mediaId chính>", "<UUID phiên>", [<ts sửa>] ],
  "<projectId>" ]
```

### `ogiZ0b` — tạo ảnh

```
[ null,
  [[ null, null, null,
     943801078,            // seed
     3,                    // ? (chưa xác định: tỉ lệ khung hay số ảnh)
     "GEM_PIX_2",          // khoá model — GIỐNG đường cũ
     null,
     [ null, 22, null, null, null, "<projectId>", null, null, null, null,
       [ "<token reCAPTCHA ~2.4KB>", 1 ] ],       // clientContext
     [[["<prompt>"]]],     // prompt, lồng ba lớp
     null, null, null,
     "<uuid>", "<uuid>" ]],
  1,                       // ? (số ảnh)
  [ …clientContext lặp lại… ],
  [ "<uuid>" ] ]
```

Khoá model và tham số bên dưới KHÔNG đổi so với đường cũ — chỉ tầng vận chuyển đổi.

### Phản hồi của `ogiZ0b`

```
[[[ "<mediaId>", null, "<workflowId>", null, null, null,
    [[ null, <seed>, null,null,null,null, 1, "<prompt>", 25, null, null,
       "<workflowId>", null,
       "https://flow-content.google/image/<mediaId>?Expires=…&Signature=…",
       3, [ …prompt lồng lại… ], null, "<mediaId>" ],
     null, [1376, 768] ]]],          // ← kích thước thật
 [[ "<workflowId>", null, null,
    [ "<tiêu đề tự sinh>", [<ts>,<ns>], null, null, "<mediaId>", "<UUID>", [<ts>,<ns>] ],
    "<projectId>" ]]]
```

`media_id` vẫn là UUID, và URL ảnh nằm SẴN trong phản hồi — không phải gọi thêm lượt resolve
nào. Số `3` truyền vào ở vị trí thứ 5 là **tỉ lệ khung**: ra 1376×768 (ngang).

## Đã xác minh chạy được

- `POST /api/flow/boq {"rpcid":"nzlxg","args":[]}` → `[12439,2,3,3,null,12439]`
- `POST /api/flow/boq {"rpcid":"UpteDb","args":["projects/*",21,null,null,null,null,[1]]}`
  → danh sách dự án thật, kèm tên/thời gian/thumbnail
- Ảnh `https://flow-content.google/image/<uuid>?Expires=…&Signature=…` tải bằng GET trơn từ
  agent: 200, `image/jpeg`, 333KB. Không cookie, không token, không ràng IP →
  `media_store._download` giữ nguyên là chạy.
- **`ogiZ0b` TẠO ẢNH THẬT qua đường này** — prompt tự chọn, token reCAPTCHA tự lấy bằng
  `chrome.scripting.executeScript` ở MAIN world, KHÔNG dùng token ya29 nào. Trả về
  media_id `37896937-…`, tải xuống được 127KB JPEG 1376×768, đúng prompt.

- **`mYWVGd` đổi tên được** ảnh do chính mình tạo, phản hồi trả về đối tượng đã cập nhật.

Tức là đường mới không chỉ ĐỌC được mà GHI được. Đây là bằng chứng đủ để port từng lời gọi.

Token reCAPTCHA dùng MỘT lần, sống ~2 phút, nên không chép lại token bắt được: đặt chuỗi
`"__CAPTCHA__"` vào bất kỳ đâu trong `args`, extension tự lấy token mới và điền vào.

## Lô ảnh: N lời gọi riêng, chung một batch id

Đo trên lượt "tạo 4 ảnh" thật của giao diện mới (22:10:11 → 22:10:14):

- **4 lời gọi `ogiZ0b` RIÊNG**, cách nhau ~1 giây — không phải một lời gọi trả 4 ảnh.
- Mỗi lời gọi có **seed riêng**, **hai UUID riêng**, và **token reCAPTCHA RIÊNG** (đo được
  bốn token khác nhau, dài 2361–2468 ký tự). Không dùng chung token được.
- `args[4][0]` là **BATCH ID, cả lô dùng CHUNG** — đúng khái niệm
  `mediaGenerationContext.batchId` của đường cũ. Giữ nguyên cách làm đó khi port.
- `args[2]` KHÔNG phải số ảnh: đặt 2 vẫn chỉ trả về 1 ảnh (đã thử).

Hệ quả: mỗi ảnh tốn một lượt reCAPTCHA, y như đường cũ — không có đường tắt sinh nhiều ảnh
bằng một lời gọi. Cách giãn nhịp của bản chính (lô 4, cooldown, stagger) áp thẳng sang được.

Tỉ lệ khung nằm ở `args[1][0][4]`, đo được: **3 → 1376×768 (16:9 ngang)** (người dùng xác
nhận đã chọn 16:9), **4 → 896×1200 (3:4 dọc)**.

## Ảnh tham chiếu, model, tỉ lệ khung

Đo trên lượt "4 ảnh, 1 ảnh tham chiếu, Nano Banana 2, 9:16":

- **Ảnh tham chiếu nằm ở `args[1][0][2]`** — ô trước đó luôn `null`. Dạng danh sách:
  `[[ "<mediaId>", null, null, null, 1 ]]`. Số cuối gần như chắc là kiểu input, ứng với
  `IMAGE_INPUT_TYPE_*` của đường cũ.
- **Ảnh đi bằng `mediaId`, KHÔNG phải base64 hay URL** — payload có ref chỉ dài thêm ~170 ký
  tự. Nghĩa là không phải tải ảnh lên lại mỗi lượt, y như đường cũ.
- Khối prompt vẫn lồng ba lớp `[[["…"]]]` kể cả khi có reference, nên **ba lớp đó KHÔNG phải
  chỗ dành cho ảnh tham chiếu** như tôi đoán ban đầu.
- **`NARWHAL` = Nano Banana 2**; `GEM_PIX_2` là mặc định. Cùng bộ khoá model với đường cũ.
- Tỉ lệ khung `args[1][0][4]`, đo đủ cả năm giá trị:

  | giá trị | 1 | 2 | 3 | 4 | 5 |
  |---|---|---|---|---|---|
  | kích thước | 1024×1024 | 768×1376 | 1376×768 | 896×1200 | 1200×896 |
  | khung | 1:1 | 9:16 | 16:9 | 3:4 | 4:3 |

  **Giá trị ngoài dải KHÔNG báo lỗi.** `0` và `6` đều lặng lẽ trả `1408×768` — khung 11:6
  không có trong menu của UI. Luôn gửi 1–5 tường minh; để trống hay `0` mà tưởng sẽ có mặc
  định hợp lý là ra ảnh sai khung mà không có gì cảnh báo.
- Nhiều ảnh tham chiếu = nhiều mục trong cùng danh sách, và **thứ tự danh sách chính là
  "ảnh thứ nhất / ảnh thứ hai"** mà prompt nhắc tới (kiểm bằng prompt ghép mèo từ ảnh 1
  vào giỏ xe đạp từ ảnh 2 — ra đúng).
- Khoá model đo thêm: **`HARBOR_SEAL` = Nano Banana 2 Lite**, khoá MỚI chưa có trong
  `models.json` của bản chính. Là tên riêng chứ không phải hậu tố `_lite` như bên video —
  đừng suy từ quy tắc đặt tên của video sang.
- Cả 4 lời gọi dùng **chung một ảnh tham chiếu và chung một batch id**, khác nhau ở seed,
  UUID và token reCAPTCHA — giống hệt lô không có reference.

Đã chạy lại toàn bộ qua `POST /api/flow/boq`, lấy ảnh mèo do chính mình sinh làm tham chiếu:
ra đúng con mèo đó đội mũ rơm giữa đồng hướng dương, 768×1376. Reference có tác dụng thật,
không chỉ được API chấp nhận.

## rpcid đổi thì sao

Không có gì bảo đảm `ogiZ0b` mãi là "tạo ảnh". Nhưng ba thứ khiến việc hỏng trở nên rẻ:

1. **Tham số phiên đọc SỐNG từ trang mỗi lượt gọi** — `at`, `f.sid`, `bl`. `bl` chính là số
   hiệu bản phát hành backend (`…_20260907.00_p0`), nên nó tự bám theo mỗi lần Google đẩy bản
   mới, không phải sửa gì.
2. **Bảng rpcid nằm ở `agent/boq_rpcids.json`, KHÔNG nhúng trong code** — đọc lại mỗi lần gọi
   nên sửa file là có hiệu lực ngay, không phải khởi động lại agent.
3. **`POST /api/flow/boq/verify`** chạy thử mọi op ĐỌC trong bảng (chỉ op có `probe_args`,
   không đụng dữ liệu) và báo `ok` / `moved` / `error`. Chạy nó trước mỗi lượt dựng dài là
   biết ngay bảng còn đúng không, thay vì hỏng giữa chừng.

Khi có cái `moved`: bật extension, làm đúng thao tác đó trên giao diện thật, rồi đọc
`GET /api/flow/boq/log`. rpcid lạ nào mang payload có prompt hoặc có `projectId` chính là cái
vừa đổi — sửa một dòng trong `boq_rpcids.json` là xong.

Đo ngày 2026-09-08: `credits`, `list_projects`, `user_prefs`, `generate_image` đều `ok`.

## Việc còn lại

1. **Dò rpcid.** Mở `flow.google.com/project/<id>`, thao tác thật (tạo ảnh, tạo video, mở
   dự án, tạo dự án, đổi tên), rồi đọc `GET /api/flow/boq/log`. Mỗi rpcid là một mã 6 ký tự
   kiểu `YhhmEf`. Ghi bảng rpcid → việc vào đây.
2. **Ánh xạ tham số.** `args` là mảng JSON lồng, vị trí có nghĩa chứ không có tên khoá —
   phải đối chiếu từng lượt gọi thật với thao tác tương ứng.
3. **Chuyển từng lời gọi** sang batchexecute, giữ đường cũ làm dự phòng cho tới khi
   `labs.google` thật sự tắt.
4. **Tải media**: URL `googlevideo.com` ràng theo IP người xem và hết hạn ~2 tiếng. Agent tải
   từ cùng máy nên cùng IP — nhưng nếu trình duyệt đi IPv6 còn httpx đi IPv4 thì khác địa chỉ
   và Google trả 403. Chưa xử lý.

## Cạm bẫy đã biết

- `at` (`WIZ_global_data.SNlM0e`) đổi theo phiên — đọc lại mỗi lần gọi, đừng cache.
- Không có tab `/project/*` nào mở thì không lấy được tham số → `NO_FLOW_APP_TAB`. Trang chủ
  `flow.google.com/` KHÔNG dùng được: app chưa boot nên không có `WIZ_global_data` đầy đủ,
  cũng không có `grecaptcha`.
- Response có thể gồm nhiều khối; một rpcid hỏng thì envelope là `er` chứ không phải `wrb.fr`.
