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

## Đặt ảnh bìa — và tên trường thì suy được từ API cũ

```
o8DA4  [ "projects/<id>",
         [ "<tên dự án>", "<mediaId bìa>" ],     // đối tượng dự án: [0]=tên, [1]=ảnh bìa
         [ ["thumbnail_media_key"] ],            // field mask
         [ null, 22 ] ]                          // clientContext rút gọn
```

Hai điều đáng chú ý:

1. **Mask là `thumbnail_media_key`** — chính là `updateMask=thumbnailMediaKey` của
   `PATCH /v1/projects/{pid}` bên đường cũ, chỉ đổi sang snake_case. Nếu quy tắc này đúng
   chung (CHƯA kiểm hết), ta **suy được tên trường từ API cũ** thay vì phải bắt từng cái.
2. **Không phải một rpcid sửa dùng chung.** `o8DA4` và `Kcr7Ub` cùng nhận
   `["projects/<id>", <đối tượng>, <mask>]` nhưng là hai rpcid khác nhau — `Kcr7Ub` không có
   clientContext ở cuối và mask của nó trỏ tới trạng thái UI (`agent_toggle_state`). Tức là
   nhiều rpcid cùng sửa một resource path, chia theo NHÓM TRƯỜNG. Dự đoán ban đầu của tôi
   rằng mọi thao tác sửa dự án đi qua một `Kcr7Ub` là SAI.

## Thùng rác là một CỜ, không phải lệnh xoá

```
pGCYOe  [ [ ["<workflowId>", null, null, [null,null,1], "<projectId>"], … ],
          [ ["metadata.archived"] ] ]
```

- Không có lệnh xoá nào ở bước này — chỉ bật `metadata.archived`. **Khôi phục được** bằng
  cách đặt lại cờ đó, không cần rpcid khác.
- **Tham số đầu là DANH SÁCH: một lời gọi xử lý nhiều mục.** Giao diện thật vẫn bắn một lời
  gọi cho mỗi ảnh (5 ảnh = 5 lượt, cách nhau ~4–5 giây), nhưng đó là lựa chọn của UI chứ
  không phải giới hạn của API — đã kiểm bằng 2 workflow trong 1 lời gọi, cả hai trả
  `archived=true`. Khi port, dọn N ảnh nên gộp một lượt.
- Đối chiếu hai mask đã biết cho ra hình dạng **metadata của workflow**:
  `[0]`=display_name, `[1]`=ts tạo, `[2]`=archived, `[4]`=mediaId chính, `[5]`=UUID phiên,
  `[6]`=ts sửa.

## Tải ảnh lên và xin bản nét

**`maseQ` — tải lên.** Ảnh đi **bằng base64 ngay trong `f.req`**, cùng một đường
`batchexecute`: không có endpoint upload riêng, không có URL ký sẵn, không có resumable
upload. Đường của mình dùng thẳng được.

```
[ <clientContext có projectId>, "<ảnh base64>", "<mime>", 1,
  null,null,null,null, "<tên file>", null, "<UUID>", "<UUID>" ]
```

Bẫy: giao diện thật khai `"image/png"` trong khi byte là JPEG (base64 mở đầu `/9j/`). Máy chủ
tự nhận dạng — trường mime không phải nguồn sự thật.

**`SPrCad` — xin bản nét.** `["<mediaId>", <mức>, <clientContext>]`; clientContext ở đây
**không có projectId**, khác lúc tạo ảnh.

| mức | kết quả từ ảnh 1376×768 | |
|---|---|---|
| 1 | 2752×1536 | gấp đôi — "bản 2K" |
| 2 | 5504×3072 | gấp bốn — "bản 4K" |

**Đo trên tài khoản Ultra.** Tài khoản Pro (TIER_ONE) không lấy được 4K — trần theo tier y
như đường cũ (`UPSAMPLE_IMAGE_RESOLUTIONS`: ONE → 2K, Ultra → 4K). Đừng hardcode "mức 2 luôn
chạy": hạ mức xuống trần theo `_current_tier_for(project)`. CHƯA đo được Pro trả lỗi gì khi
xin mức 2 — phải thử lại trên tài khoản Pro, vì nếu nó trả `error [3]` giống hệt ca "ảnh tải
lên" thì hai nguyên nhân khác hẳn nhau lại nhìn y như nhau.

Trả về **mediaId mới + ảnh dạng base64 ngay trong phản hồi**, không phải URL — giống đường cũ,
nơi `upsampleImage` cũng trả vài MB base64. Mức 2 nặng ~920KB byte thật.

Hai cái bẫy đã dính:

1. **Chỉ chạy trên media do FLOW SINH.** Ảnh người dùng TẢI LÊN trả `error [3]`
   (INVALID_ARGUMENT). Không phải thiếu quyền, không phải sai mức.
2. **Bị chặn thì phải nghỉ.** Lần đo đầu tiên trả
   `PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC` sau ~10 lượt bấm trong 2 phút — và càng
   thử lại càng chặn. Nhìn từ phía người dùng nó giống hệt "tài khoản không lên được 2K",
   trong khi tài khoản này thật ra lên được cả 4K.

## Sửa ảnh — và ý nghĩa số cuối trong mục ảnh vào

Sửa ảnh **dùng chung `ogiZ0b`** với tạo ảnh, khác đúng ba chỗ:

1. Ảnh vào mang **kiểu `2`** thay vì `1`. Đây là đáp án cho câu hỏi treo từ đầu:
   `[<mediaId>, null, null, null, <kiểu>]` với **`1` = ảnh THAM CHIẾU, `2` = ảnh NỀN** — ứng
   đúng `IMAGE_INPUT_TYPE_REFERENCE` / `IMAGE_INPUT_TYPE_BASE_IMAGE` của đường cũ.
2. `clientContext[4]` mang **workflowId** của ảnh đang sửa (lúc tạo mới ô này là `null`).
3. `[1][0][12]` là `null`, chỉ `[13]` có UUID.

Sinh xong, giao diện gọi thêm `mYWVGd` với mask `metadata.primary_media_id` để trỏ workflow
sang media mới. **Đó là cách "sửa tại chỗ" hoạt động: cùng workflow, đổi media chính** — chứ
không phải ghi đè lên ảnh cũ. Ảnh cũ vẫn còn, truy lại được.

Ảnh người dùng **tải lên sửa được bình thường** — giới hạn "chỉ media do Flow sinh" là riêng
của `SPrCad`, không phải luật chung.

Kiểm bằng cách sửa chính ảnh mình tải lên: giữ nguyên bố cục, bối cảnh, ánh sáng, chỉ thêm
đúng chi tiết được yêu cầu.

## Trạng thái: mảng ẢNH đã xong

| việc | rpcid | đã chạy qua đường mới |
|---|---|---|
| tạo ảnh (kèm tham chiếu) | `ogiZ0b` | ✅ |
| sửa ảnh | `ogiZ0b` (kiểu 2) | ✅ |
| tải ảnh lên | `maseQ` | ✅ |
| xin bản 2K/4K | `SPrCad` | ✅ (Ultra) |
| đổi tên | `mYWVGd` | ✅ |
| đổi media chính | `mYWVGd` | — |
| chuyển vào thùng rác | `pGCYOe` | ✅ |
| đặt ảnh bìa | `o8DA4` | ✅ |
| đọc nội dung dự án | `Zzl0ze` | ✅ |
| liệt kê dự án (có phân trang) | `UpteDb` | ✅ |
| tạo dự án | `jHPbke` | ✅ |
| đổi tên dự án | `o8DA4` | ✅ |
| xoá dự án | `QI2zvc` | ✅ |
| đọc một media | `as29s` | ✅ |
| cấu hình model dự án | `ngNC2` | ✅ |
| credit | `nzlxg` | ✅ |

### Đọc nội dung dự án

`Zzl0ze` trả về **cả kho ảnh của dự án**, tương đương `project.getProjectContents` cũ:

- `data[1]` — danh sách **workflow** (32 mục trên dự án thử)
- `data[2]` — danh sách **bản ghi media** (36 mục), mỗi mục
  `[mediaId, projectId, workflowId, "CAE"|"CAI", …]`

Nhóm media theo `workflowId` là **ra lịch sử phiên bản**, không cần rpcid riêng — đúng cái
FlowKit đang lưu ở `media_history`.

`as29s ["<mediaId>"]` đọc một media và trả cả tham số đã sinh ra nó (prompt, ảnh vào kèm kiểu,
model) — dùng để truy nguồn một ảnh.

### Liệt kê dự án có PHÂN TRANG

```
UpteDb  ["projects/*", <số mục mỗi trang>, <token trang sau|null>, null, null, null, [1]]
     -> [ [<dự án>, …], "<token trang sau>" ]
```

Mỗi dự án: `[projectId, [tên, null, [ts], <URL thumbnail>, <mediaId bìa>]]`.

Token trang sau **chỉ xuất hiện khi còn trang**. Giao diện gửi `21` nên tài khoản ít dự án
không bao giờ thấy nó — lấy một trang rồi tưởng đã đủ là bug ngầm chờ sẵn ở tài khoản nhiều
dự án. Đo bằng cách hạ xuống 3: trang đầu ra 3 dự án + token, đưa token vào **ô [2]** thì ra
3 dự án KHÁC.

Bẫy: nhét token vào ô [3] thì nó **lặng lẽ trả lại trang đầu**, không báo lỗi — vòng lặp phân
trang đặt nhầm ô sẽ chạy mãi trên cùng một trang.

## Tạo và đổi tên dự án

```
jHPbke  ["projects/*", [null, ["<tên dự án>"]], [null, 22]]   -> ["<projectId mới>", ["<tên>"]]
o8DA4   ["projects/<id>", ["<tên mới>"], [["project_title"]], [null, 22]]
```

- **Một lời gọi là xong, KHÔNG cần reCAPTCHA.** Tên do CLIENT đặt: giao diện tự sinh
  `"Tháng 9 08 - 23:12"` rồi gửi lên, nên mình đặt tên gì cũng được ngay từ lượt tạo — không
  phải tạo xong rồi đổi tên thành hai lượt như tôi đoán ban đầu.
- Tham số thứ hai là bản ghi dự án chưa có id `[null, <projectInfo>]`, **cùng hình dạng với
  mục trong phản hồi `UpteDb`**. Sau khi tạo, trang tự chuyển sang `/u/2/project/<id mới>`.
- Đổi tên dùng lại `o8DA4` (rpcid của ảnh bìa), chỉ khác mặt nạ: `project_title` — đúng
  snake_case của `projectInfo.projectTitle` bên đường cũ.

### Mặt nạ sai tên thì IM LẶNG không làm gì

Đây là bẫy nguy hiểm nhất gặp tới giờ. Thử bốn tên trường cho lệnh đổi tên: chỉ
`project_title` có tác dụng; `title`, `display_name`, `name` và cả một tên **bịa hoàn toàn**
đều trả **200, không lỗi, không đổi gì**, và phản hồi là trạng thái HIỆN TẠI.

Hệ quả khi port: gõ sai tên trường là một lệnh ghi **không bao giờ chạy** mà không có gì báo.
Nên thử mặt nạ mới thì **phải đọc lại để xác nhận**, và mỗi lượt thử phải dùng **giá trị khác
nhau** — lần đầu tôi dùng chung một tên đích cho cả bốn mặt nạ, thế là cả bốn "thành công"
vì lượt đầu đã đổi rồi, ba lượt sau chỉ đang trả lại đúng giá trị đó.

### Xoá dự án là động từ THẬT, không phải cờ

```
QI2zvc  ["projects/<id>"]   -> []
```

Chỉ nhận đường dẫn tài nguyên: không mặt nạ, không clientContext, không reCAPTCHA.

**Đây là ngoại lệ đáng nhớ.** Mọi thao tác "xoá" khác trong API này đều là bật cờ theo mặt nạ
— ảnh vào thùng rác là `metadata.archived` qua `pGCYOe`, khôi phục được. Riêng dự án có động
từ xoá thật. Đừng suy từ ảnh sang dự án hay ngược lại.

Kiểm bằng vòng đời trọn vẹn: tạo → đếm 11 dự án → xoá → đếm 10.

## Còn thiếu cho mảng ảnh

Không còn gì. Toàn bộ mảng ảnh và mảng dự án đã chạy được qua đường mới.

## Mảng VIDEO — biết trước gì, phải đo gì

Giao diện mới (người dùng mô tả): **5 model**, 3 mức thời lượng (riêng Omni Flash 1.1 có 4),
có chế độ frame và reference.

**Độ phân giải thuộc RIÊNG Omni Flash**, không phải lựa chọn chung: chỉ họ này có 360p và
720p, bốn model kia không cho chọn. Khớp với đường cũ — chỗ duy nhất `VIDEO_RESOLUTION_720P`
xuất hiện là nhánh Omni Flash text-to-video (`flow_client.py`, `outputSpec.resolution`), các
engine khác không gửi trường đó. Nên khi port, `resolution` là tham số của Omni Flash chứ
đừng nâng lên thành cài đặt cấp dự án; **360p là mức MỚI** cần thêm.

Đối chiếu với `models.json` của bản chính:

| bản chính đang biết | khớp? |
|---|---|
| Veo 3.1 Lite: `veo_3_1_{i2v,interpolation,r2v,t2v}_lite_low_priority` | 1 trong 5 |
| Veo trả tiền theo tier (`video_models`) | |
| Omni Flash: `abra_{r2v,t2v}_{4,6,8,10}s` | ✅ đúng 4 mức thời lượng |
| Upscale: 1080p / 4K | |
| `VIDEO_RESOLUTION_720P` hardcode ở nhánh Omni Flash t2v | ⚠️ thiếu mức **360p**, và mức này chỉ Omni Flash mới có |

Một mảnh đã khớp sẵn: `ngNC2` trả `"abra"` — chính là họ Omni Flash.

### Bảng giá phẳng của bản chính SAI với model đắt

`webapp/src/lib/credits.ts` dùng **một con số cho mọi clip**: `CREDIT_COST.video = 20`, chỉ
trừ Veo Lite ra 0. Nhưng đo thật: **Veo 3.1 Quality = 100 credit cho một clip 8 giây**.

Hệ quả: `creditGuard` báo thiếu **5 lần** — dựng 20 clip báo "≈400 credit" trong khi thật ra
tốn 2000, và người dùng bấm "Vẫn chạy" vì tưởng còn dư. Khi port phải đổi thành **bảng giá
theo model**, không phải một hằng số. Chưa đo giá của 3 model còn lại.

### Bảng model và GIÁ lấy được MIỄN PHÍ — đừng render để đo

Hai rpcid tôi từng xếp vào "nhiễu" hoá ra là kho dữ liệu:

- **`yBhWQ []`** → đúng 5 model video trong giao diện:
  `abra`, `veo_3_1_fast`, `veo_3_1_lite`, `veo_3_1_quality`, `veo_3_1_lite_low_priority`
- **`HTrJv []`** → **toàn bộ danh mục: 153 khoá, tên hiển thị, và GIÁ THEO TIER.**
  Giá nằm ở ô `[4]` mỗi mục: `[[1,[[null,C]]],[2,[[null,C]]],[3,[[null,C]]]]`.
  Đã trích sẵn ra `agent/boq_model_catalog.json`.

Đây chính là con số giao diện hiện trong prompt box. **Lấy được miễn phí, không tốn credit,
không cần ai thao tác** — nên đừng bao giờ đo giá bằng cách render thử rồi trừ số dư.

| nhóm | giá (tier 1 / 2 / 3) |
|---|---|
| Veo 3.1 - Quality | 100 / 100 / 100 |
| Veo 3.1 - Fast | 20 / 20 / **10** |
| Veo 3.1 - Lite | 10 / 10 / **5** |
| Veo 3.1 - Lite [Lower Priority] | — / — / **0** (chỉ tier 3) |
| Omni 1.1 Flash | theo thời lượng VÀ độ phân giải: `abra_i2v_4s` 7 · `abra_i2v_4s_360p` **4** · `abra_i2v_10s` 15 · `abra_i2v_10s_360p` 7 · `abra_edit` 20 · `abra_edit_360p` 10 |
| Upsampler 1080p / **360p** | 0 |
| Upsampler 4K | **50**, chỉ tier 3 |
| Mọi model ảnh + upsample ảnh 2K | 0 |
| Upsample ảnh 4K (`GEM_PIX_2_UPSAMPLE_4K`) | 0 nhưng **chỉ tier 3** |

Bảng phẳng `CREDIT_COST.video = 20` của bản chính sai với **cả bốn** nhóm Veo, không riêng
Quality: Fast 20/10, Lite 10/5, Lower Priority 0, Quality 100. Và Omni Flash thì phụ thuộc
thời lượng lẫn độ phân giải.

**Thời lượng, tỉ lệ khung và 360p đều nằm TRONG khoá model**, không phải trường riêng —
`abra_i2v_8s_360p`, `veo_3_1_t2v_lite_4s_low_priority`, `veo_3_1_i2v_s_fast_portrait_ultra`.
Đúng như thiết kế cũ. Nên không cần render thử từng tổ hợp: danh mục đã liệt kê sẵn tất cả.

Vài khoá bản chính CHƯA có: `veo_3_1_t2v_lite_{4,6}s_low_priority`, `veo_3_1_extension_lite*`
(nối dài video), `omni_upsampler_360p`, `abra_edit*`, và cả `abra_i2v_*` (bản chính mới có
`abra_r2v_*` và `abra_t2v_*`).

### Tier đánh số 1/2/3, KHÔNG trùng tên `PAYGATE_TIER_*`

Suy ra **3 = Ultra** từ hai bằng chứng khớp nhau: `veo_3_1_*_lite_low_priority` (CLAUDE.md:
"0đ, chỉ Ultra") chỉ có tier 3, và `GEM_PIX_2_UPSAMPLE_4K` cũng chỉ có tier 3 — đúng lúc
người dùng báo tài khoản Pro không lấy được ảnh 4K. Vậy nhiều khả năng 2 = Pro
(`PAYGATE_TIER_ONE`), 3 = Ultra (`PAYGATE_TIER_TWO`). **Đừng ánh xạ thẳng số sang tên tier cũ
mà chưa kiểm.**

### Kế hoạch đo

Sau khi có danh mục, thứ CÒN THIẾU chỉ là **hình dạng lời gọi submit** và **cách poll** —
không phải tên model hay giá nữa. Nên chỉ cần **MỘT lượt** bằng model 0 credit
(`veo_3_1_*_lite_low_priority`), không cần 5 lượt mỗi model.

Thứ **chỉ capture mới trả lời được** là cách POLL: video render 30–240 giây nên submit xong
phải hỏi lại kết quả, cơ chế đó chưa từng xuất hiện trong mảng ảnh (ảnh trả kết quả ngay).

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
