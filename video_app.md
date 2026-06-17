# Kế hoạch xây dựng App tạo Video (Video Studio)

Tài liệu này mô tả chi tiết kế hoạch xây dựng một ứng dụng tạo video/ảnh AI, lấy
**FlowKit agent** (`http://127.0.0.1:8100`) hiện tại làm engine sinh nội dung. App
bổ sung lớp lưu trữ riêng (SQLite), quản lý dự án/entity, và 2 chế độ làm việc:
node kéo-thả và sinh hàng loạt.

---

## 1. Mục tiêu & phạm vi

- Giao diện **Next.js** để người dùng tạo video/ảnh mà không cần gọi API thủ công.
- Lưu trữ toàn bộ trạng thái (dự án, entity, scene, asset, job) vào **SQLite**.
- **Không gọi thẳng Google Flow** — mọi thao tác sinh ảnh/video/TTS đều đi qua các
  API sẵn có của FlowKit (`/api/flow/*`, `/api/tts/*`).
- Hai chế độ làm việc:
  1. **Node mode** — dựng pipeline bằng các node kéo-thả (prompt → ảnh → video → upscale…).
  2. **Batch mode** — pipeline AI: từ nội dung gốc + style (+ duration) → script Hollywood →
     entity → shot → ảnh/video hàng loạt → xuất XML DaVinci Resolve.
- **Quản lý dự án**: tạo/sửa/xóa/liệt kê, mỗi dự án map tới 1 Flow project.
- **Quản lý entity** (nhân vật/địa điểm/đạo cụ…): dùng lại **xuyên dự án**.

### Ngoài phạm vi (giai đoạn đầu)
- Xác thực người dùng / multi-tenant (app chạy local, 1 người dùng).
- Dựng/ghép video cuối (ffmpeg concat) — sẽ là phase mở rộng.
- Upload YouTube, thumbnail, music — phase mở rộng.

---

## 2. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────────────┐
│                     Next.js App (UI + API)                        │
│                                                                   │
│  app/ (React, App Router)        app/api/* (Route Handlers)       │
│  ├─ Node editor (React Flow)  ┐                                   │
│  ├─ Batch editor              ├─► CRUD project/entity/scene/asset  │
│  ├─ Project/Entity manager    │   + tạo job vào hàng đợi           │
│  └─ Asset gallery             ┘                                   │
└───────────────┬──────────────────────────────┬───────────────────┘
                │ Prisma                         │ tạo Job (DB)
                ▼                                ▼
        ┌───────────────┐              ┌──────────────────────┐
        │  SQLite (.db) │◄────────────►│  Worker (Node, tsx)   │
        └───────────────┘   poll jobs  │  - chạy generation    │
                                        │  - poll video status  │
                                        │  - refresh URL hết hạn│
                                        └──────────┬────────────┘
                                                   │ HTTP
                                                   ▼
                          ┌───────────────────────────────────────┐
                          │   FlowKit agent  (đang có)  :8100       │
                          │   /api/flow/*   →  Chrome extension     │
                          │   /api/tts/*    →  OmniVoice (Colab)    │
                          └───────────────────────────────────────┘
```

**Nguyên tắc:** Next.js xử lý CRUD + điều phối nhanh; mọi tác vụ sinh nội dung
(đặc biệt video bất đồng bộ cần poll) do **Worker** chạy nền đảm nhận để không block
request. Cả Next.js và Worker dùng chung 1 file SQLite qua Prisma.

> **Lựa chọn kiến trúc & lý do.** Backend đặt trong Next.js Route Handlers (thay vì
> dựng thêm service Python/FastAPI) để giảm số tiến trình và chia sẻ type TypeScript
> với UI. FlowKit agent vẫn là tiến trình Python riêng vì nó giữ kết nối WebSocket
> tới extension. Phần xử lý job bất đồng bộ tách thành Worker riêng vì serverless-style
> route handlers không phù hợp tác vụ chạy dài (poll video vài phút).
> *Phương án thay thế:* nếu muốn 1 ngôn ngữ duy nhất, có thể viết backend bằng FastAPI
> mới cạnh FlowKit; đánh đổi là phải tự định nghĩa lại type cho UI.

---

## 3. Tech stack

| Lớp | Lựa chọn | Ghi chú |
|-----|----------|---------|
| Framework | **Next.js 15** (App Router, TypeScript) | UI + API route handlers |
| UI | React 19, **Tailwind CSS**, shadcn/ui | component & style |
| Node editor | **@xyflow/react** (React Flow) | canvas kéo-thả |
| State/data | **TanStack Query** (server state) + **Zustand** (UI state node editor) | |
| ORM/DB | **Prisma** + **SQLite** | `better-sqlite3` driver |
| Worker | Node process chạy bằng **tsx**, dùng chung Prisma client | poll job |
| Realtime | **SSE** (`/api/events`) hoặc polling TanStack Query | cập nhật trạng thái job |
| HTTP client | `fetch` (gọi FlowKit) | wrap trong `lib/flowkit.ts` |
| Validation | **Zod** | validate body API + form |

*Có thể thay Prisma bằng Drizzle nếu thích SQL thuần; thay shadcn bằng Mantine. Các lựa chọn trên là khuyến nghị, không bắt buộc.*

---

## 4. Mô hình dữ liệu (SQLite / Prisma)

Khóa chính dùng `cuid()`. `flowProjectId`, `mediaId` là UUID do Google Flow trả về.

```prisma
// ---------- Quản lý dự án ----------
model Project {
  id             String   @id @default(cuid())
  name           String
  description    String?
  flowProjectId  String?            // projectId trả về từ /api/flow/create-project
  aspectRatio    String   @default("VERTICAL")   // VERTICAL | HORIZONTAL
  status         String   @default("ACTIVE")     // ACTIVE | ARCHIVED
  createdAt      DateTime @default(now())
  updatedAt      DateTime @updatedAt

  flows          Flow[]
  batches        Batch[]          // scenes/shots nằm dưới Batch (mục 8)
  entities       ProjectEntity[]
  assets         Asset[]
}

// ---------- Entity dùng chung xuyên dự án ----------
model Entity {
  id                String   @id @default(cuid())
  name              String
  type              String   @default("character")  // character | location | prop
  description       String?
  imagePrompt       String?
  voiceDescription  String?
  // ảnh tham chiếu (sinh hoặc upload) — dùng làm character_media_ids / reference_media_ids
  referenceMediaId  String?
  referenceImageUrl String?
  createdAt         DateTime @default(now())
  updatedAt         DateTime @updatedAt

  projects          ProjectEntity[]
}

// link M:N — 1 entity có thể thuộc nhiều project (chia sẻ xuyên dự án)
model ProjectEntity {
  projectId String
  entityId  String
  project   Project @relation(fields: [projectId], references: [id], onDelete: Cascade)
  entity    Entity  @relation(fields: [entityId],  references: [id], onDelete: Cascade)
  @@id([projectId, entityId])
}

// ---------- Asset: mọi media đã sinh (ảnh/video/upscale/audio) ----------
model Asset {
  id          String   @id @default(cuid())
  projectId   String
  project     Project  @relation(fields: [projectId], references: [id], onDelete: Cascade)
  kind        String                    // IMAGE | VIDEO | UPSCALE | AUDIO
  mediaId     String?                   // Flow mediaId (UUID)
  url         String?                   // signed URL gốc (có hạn) — chỉ dùng để tải về
  urlExpireAt DateTime?
  localPath   String?                   // ./media/<projectId>/<kind>_<mediaId>.<ext> — NGUỒN HIỂN THỊ CHÍNH
  meta        String?                   // JSON: seed, model, dimensions...
  createdAt   DateTime @default(now())
}

// ---------- Batch mode: Batch → ScriptScene → Shot ----------
// 1 Batch = 1 sản phẩm sinh từ (style + nội dung gốc + duration mong muốn?).
model Batch {
  id                String   @id @default(cuid())
  projectId         String
  project           Project  @relation(fields: [projectId], references: [id], onDelete: Cascade)
  name              String
  style             String                  // phong cách mong muốn (vd "cinematic noir, 35mm")
  sourceContent     String                  // nội dung gốc (chương truyện / truyện ngắn)
  targetDurationSec Float?                   // duration mong muốn (TÙY CHỌN)
  config            String?                 // JSON: aspectRatio, tier, model, shotSec=8, voiceId...
  status            String   @default("DRAFT") // DRAFT|SCRIPTING|ENTITIES|SHOTS|RUNNING|DONE|FAILED
  createdAt         DateTime @default(now())
  scenes            ScriptScene[]
}

// 1 scene kịch bản = 1 ĐỊA ĐIỂM cụ thể + 1 đoạn NỘI DUNG GỐC (các đoạn liên tiếp, phủ kín).
model ScriptScene {
  id               String   @id @default(cuid())
  batchId          String
  batch            Batch    @relation(fields: [batchId], references: [id], onDelete: Cascade)
  order            Int      @default(0)
  heading          String?                  // slugline kiểu Hollywood: "EXT. RỪNG THÔNG - HOÀNG HÔN"
  action           String?                  // mô tả hành động/diễn biến
  narration        String?                  // lời bình / voice-over của scene
  unitStart        Int                      // chỉ số đơn vị nội dung gốc bắt đầu (do AI gom)
  unitEnd          Int                      // chỉ số đơn vị kết thúc (unitEnd+1 == next.unitStart)
  sourceStart      Int                      // offset ký tự derive từ unitStart (tiện hiển thị)
  sourceEnd        Int                      // offset ký tự derive từ unitEnd
  locationEntityId String?                  // entity type=location (địa điểm cụ thể của scene)
  audioAssetId     String?                  // TTS narration (khi KHÔNG set targetDuration)
  durationSec      Float?                   // duration scene (từ target phân bổ hoặc đo audio)
  shotCount        Int?                     // = ceil(durationSec / shotSec)
  shots            Shot[]
}

// Shot = đơn vị 8s; mọi shot trong cùng scene tham chiếu CÙNG location của scene.
model Shot {
  id            String   @id @default(cuid())
  sceneId       String
  scene         ScriptScene @relation(fields: [sceneId], references: [id], onDelete: Cascade)
  order         Int      @default(0)
  prompt        String?                 // prompt sinh ảnh (theo style)
  videoPrompt   String?
  entityRefs    String?                 // JSON entityId[] — gồm location của scene + nhân vật/đạo cụ, TỐI ĐA 10
  durationSec   Float    @default(8)
  imageAssetId  String?
  videoAssetId  String?
  upscaleAssetId String?
  imageStatus   String   @default("PENDING")
  videoStatus   String   @default("PENDING")
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt
}

// ---------- Node mode ----------
model Flow {
  id        String   @id @default(cuid())
  projectId String
  project   Project  @relation(fields: [projectId], references: [id], onDelete: Cascade)
  name      String
  graph     String                      // JSON {nodes:[], edges:[]} của React Flow
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
}

// ---------- Hàng đợi job (Worker xử lý) ----------
model Job {
  id           String   @id @default(cuid())
  type         String                   // GEN_IMAGE|EDIT_IMAGE|GEN_VIDEO|GEN_VIDEO_REFS|UPSCALE|TTS|GEN_ENTITY_REF
  status       String   @default("PENDING") // PENDING|RUNNING|POLLING|COMPLETED|FAILED
  payload      String                   // JSON input gửi FlowKit
  // liên kết ngược để cập nhật khi xong
  shotId       String?                  // GEN_IMAGE/VIDEO/UPSCALE (batch)
  sceneId      String?                  // TTS narration của ScriptScene
  nodeId       String?                  // node trong Flow.graph (node mode)
  entityId     String?                  // GEN_ENTITY_REF
  projectId    String?
  operations   String?                  // JSON operations trả về (video) để poll
  resultMediaId String?
  resultUrl    String?
  error        String?
  attempts     Int      @default(0)
  createdAt    DateTime @default(now())
  updatedAt    DateTime @updatedAt
  @@index([status])
}
```

---

## 5. Backend của app — API nội bộ & mapping sang FlowKit

### 5.1 Bảng mapping thao tác → FlowKit

| Thao tác app | Gọi FlowKit | Đồng bộ? |
|--------------|-------------|----------|
| Tạo Flow project | `POST /api/flow/create-project` | đồng bộ |
| Sinh ảnh scene | `POST /api/flow/generate-image` (`character_media_ids` = ảnh ref của entity) | gần như đồng bộ (trả `media[].fifeUrl`) |
| Sửa ảnh | `POST /api/flow/edit-image` | đồng bộ |
| Sinh ảnh ref cho entity | `POST /api/flow/generate-image` | đồng bộ |
| Sinh video (i2v) | `POST /api/flow/generate-video` → trả **operations** | **bất đồng bộ** → poll |
| Sinh video từ refs (r2v) | `POST /api/flow/generate-video-refs` → operations | **bất đồng bộ** → poll |
| Upscale video | `POST /api/flow/upscale/video` → operations | **bất đồng bộ** → poll |
| Poll trạng thái video | `POST /api/flow/check-status` `{operations}` | poll định kỳ |
| Refresh URL hết hạn | `GET /api/flow/media/{primaryMediaId}` | đồng bộ |
| Upload ảnh local | `POST /api/flow/upload-image` `{file_path}` | đồng bộ |
| TTS | `POST /api/tts/synthesize` | đồng bộ (có thể chậm) |

> **Lưu ý quan trọng (rút từ code FlowKit):**
> - Ảnh sinh **trả ngay** `media[].fifeUrl` + `mediaId`. Video thì **chỉ submit**, sau đó
>   phải gọi `check-status` lặp lại tới khi `operations` báo done mới có URL video.
> - URL `fifeUrl`/`flow-content.google` **có hạn** (param `Expires`) → Worker **tải file
>   về ngay** vào `./media/<projectId>/<kind>_<mediaId>.<ext>` và app hiển thị từ file local
>   (nhanh, không lo URL hết hạn). URL gốc chỉ dùng để tải; nếu hết hạn trước khi tải xong
>   thì refresh bằng `GET /api/flow/media/{mediaId}` rồi tải lại.
> - Extension chỉ giữ **1 kết nối**; Google giới hạn song song (~5). Worker phải có
>   giới hạn đồng thời và backoff.

### 5.2 REST API của app (`app/api/*`)

```
# Project
GET    /api/projects                 liệt kê
POST   /api/projects                 tạo (đồng thời gọi create-project lấy flowProjectId)
GET    /api/projects/:id             chi tiết
PATCH  /api/projects/:id             sửa
DELETE /api/projects/:id             xóa (tùy chọn xóa luôn Flow project)
GET    /api/projects/:id/export?media=1   SAVE: tải bundle .zip (project.json + media/)
POST   /api/projects/import          LOAD: upload .zip → khôi phục vào DB (remap id, dùng lại entity)

# Entity (toàn cục, chia sẻ xuyên dự án)
GET    /api/entities                 liệt kê tất cả (filter theo type, ?projectId=)
POST   /api/entities                 tạo entity
PATCH  /api/entities/:id             sửa
DELETE /api/entities/:id
POST   /api/entities/:id/reference   tạo job sinh ảnh ref (GEN_ENTITY_REF)
POST   /api/projects/:id/entities    gắn entity vào project   {entityId}
DELETE /api/projects/:id/entities/:entityId   gỡ liên kết

# Shot ops (sinh lại từng shot — scene/shot do pipeline Batch tạo, mục 8)
POST   /api/shots/:id/generate-image    tạo job GEN_IMAGE
POST   /api/shots/:id/generate-video    tạo job GEN_VIDEO / GEN_VIDEO_REFS
POST   /api/shots/:id/upscale           tạo job UPSCALE

# Batch mode (pipeline AI — mục 8)
POST   /api/projects/:id/batches             tạo batch {style, sourceContent, targetDurationSec?}
GET    /api/batches/:id                       trạng thái + scenes + shots
POST   /api/batches/:id/script                B1: AI sinh script → ScriptScene[] (validate liên tiếp)
POST   /api/batches/:id/entities              B2: AI sinh entity theo style + sinh ảnh ref
POST   /api/batches/:id/plan-shots            B3: TTS (nếu cần) → đo duration → tạo Shot rỗng (8s)
POST   /api/batches/:id/shots                 B4: AI viết prompt ảnh/scene (cùng location, ≤10 ref)
PATCH  /api/scenes/:id                         sửa scene (narration/location)
PATCH  /api/shots/:id                          sửa prompt/entityRefs của shot
POST   /api/batches/:id/run                   B5: enqueue ảnh→video→upscale cho mọi shot
POST   /api/batches/:id/export-resolve        B6: dựng & tải FCP7 XML (V1 video, A1 audio)

# Node mode
GET    /api/projects/:id/flows
POST   /api/projects/:id/flows               tạo flow (graph rỗng)
PATCH  /api/flows/:id                         lưu graph {nodes, edges}
POST   /api/flows/:id/run                     biên dịch graph → tạo job theo thứ tự topo

# Jobs & realtime
GET    /api/jobs?status=&projectId=          liệt kê job
GET    /api/events                            SSE: phát sự kiện job cập nhật

# TTS
GET/PUT /api/tts/config                       proxy cấu hình OmniVoice
POST    /api/tts/synthesize                    tạo job/asset AUDIO

# AI-CLI local (mục 9)
GET    /api/ai/clis                           liệt kê CLI khả dụng (allowlist)
POST   /api/ai/prompt                          chạy 1 AI-CLI với prompt → stdout

# Assets
GET    /api/assets/:id/url                     trả URL/đường dẫn local (refresh nếu cần)
GET    /media/<projectId>/<file>               phục vụ file media local (static)
```

Tất cả handler validate input bằng Zod, gọi Prisma, và **không tự sinh nội dung** —
chỉ tạo bản ghi `Job` rồi trả về ngay (HTTP 202 + jobId). UI theo dõi job qua SSE.
Riêng thao tác đồng bộ nhanh (tạo project, refresh URL) có thể gọi FlowKit trực tiếp.

### 5.3 Tier, model & aspect ratio (chuẩn hóa trong `lib/flowkit.ts`)

App lưu giá trị "thân thiện", `lib/flowkit.ts` dịch sang enum của Flow trước khi gửi:
- **Aspect ratio:** `VERTICAL → IMAGE_ASPECT_RATIO_PORTRAIT` / `VIDEO_ASPECT_RATIO_PORTRAIT`;
  `HORIZONTAL → *_LANDSCAPE`. (FlowKit chọn `videoModelKey` theo tier × loại × tỉ lệ.)
- **Tier:** đọc từ `GET /api/flow/credits` (`userPaygateTier`) khi mở app, cache vào project
  config; truyền `user_paygate_tier` cho generate. Không hardcode tier của người khác.
- **Model:** dùng mặc định của FlowKit (`models.json`); cho phép override trong batch config
  nếu cần. App không cần biết model key — chỉ chuyển tham số FlowKit đã hỗ trợ.

---

## 6. Worker (xử lý job nền)

Tiến trình Node riêng (`worker/index.ts`, chạy bằng `tsx watch`), vòng lặp:

```
mỗi 1–2s:
  1. Lấy tối đa N job PENDING (N = giới hạn đồng thời, vd 4) → set RUNNING
  2. Theo type:
     - GEN_IMAGE / EDIT_IMAGE / GEN_ENTITY_REF:
         gọi FlowKit → nhận media → **tải ảnh về ./media/<projectId>/image_<mediaId>.png**
         → tạo Asset (localPath) → cập nhật Shot.imageAssetId / Entity.referenceMediaId → COMPLETED
     - GEN_VIDEO / GEN_VIDEO_REFS / UPSCALE:
         gọi FlowKit (submit) → lưu operations → set POLLING
     - TTS: gọi /api/tts/synthesize → lưu base64 ra ./media/<projectId>/audio_<id>.wav
         → đo duration → cập nhật ScriptScene.durationSec/shotCount → Asset AUDIO → COMPLETED
  3. Job POLLING: gọi /api/flow/check-status(operations) mỗi VIDEO_POLL_INTERVAL
         - done → lấy URL → **tải video về ./media/<projectId>/video_<mediaId>.mp4**
           → Asset VIDEO (localPath) → cập nhật Shot.videoAssetId → COMPLETED
         - timeout (vd 420s) → FAILED
  4. Lỗi → tăng attempts, retry tới MAX_RETRIES rồi FAILED
  5. Phát event qua kênh SSE (ghi vào bảng tạm / EventEmitter in-process)
```

**Đồng thời & nhịp độ:** tôn trọng giới hạn ~5 request song song của Google; thêm
cooldown nhỏ giữa các submit. Nếu FlowKit trả `extension_connected:false`/`NO_FLOW_KEY`
→ tạm dừng job và báo UI (giống tinh thần `/fk-doctor`).

**Khôi phục sau crash (idempotent).** Khi Worker khởi động lại: job `RUNNING` mồ côi →
reset về `PENDING`; job `POLLING` → tiếp tục poll từ `operations` đã lưu. Mọi job lưu đủ
input trong `payload` để chạy lại an toàn; ghi `resultMediaId` trước khi đánh dấu COMPLETED
để không tạo trùng.

**Quy tắc cascade khi sinh lại** (giống FlowKit): sinh lại **ảnh** của shot → xóa
video + upscale của shot đó (PENDING lại); sinh lại **video** → xóa upscale; **upscale**
không cascade. Tránh hiển thị video cũ không khớp ảnh mới.

> SSE phát từ Worker tới UI: vì Worker và Next.js là 2 tiến trình, dùng 1 trong các cách:
> (a) Worker `POST /api/events/emit` nội bộ để Next.js đẩy SSE; hoặc (b) UI dùng
> TanStack Query polling `GET /api/jobs` mỗi 2s (đơn giản hơn, khuyến nghị cho phase 1).

---

## 7. Chế độ Node (kéo-thả)

Dùng **React Flow**. Graph lưu vào `Flow.graph` (JSON). Các loại node:

| Node | Input | Output | Map FlowKit |
|------|-------|--------|-------------|
| **PromptNode** | text | prompt | — (giá trị) |
| **EntityRefNode** | chọn entity | mediaId ref | dùng `referenceMediaId` |
| **ImageUploadNode** | file | mediaId | `upload-image` |
| **GenImageNode** | prompt + (refs) | image asset | `generate-image` (`character_media_ids`) |
| **EditImageNode** | image + prompt | image asset | `edit-image` |
| **GenVideoNode** | startImage (+endImage) + prompt | video asset | `generate-video` |
| **GenVideoRefsNode** | refs[] + prompt | video asset | `generate-video-refs` |
| **UpscaleNode** | video | upscale asset | `upscale/video` |
| **TTSNode** | text (+voice) | audio asset | `/api/tts/synthesize` |
| **OutputNode** | asset | kết quả cuối | — |

**Thực thi (`/api/flows/:id/run`):**
1. Parse `{nodes, edges}` → đồ thị phụ thuộc.
2. **Sắp xếp topo**; kiểm tra hợp lệ (không vòng lặp, đủ input bắt buộc, kiểu khớp:
   ví dụ GenVideoNode cần input là IMAGE).
3. Sinh `Job` cho từng node "thực thi được", gắn `nodeId`. Job phụ thuộc node trước
   sẽ chờ node đó `COMPLETED` (Worker kiểm tra điều kiện trước khi chạy).
4. UI tô màu node theo trạng thái job (PENDING/RUNNING/DONE/FAILED) realtime.

Frontend: canvas React Flow + panel cấu hình node bên phải + nút **Run**. Trạng thái
node giữ trong Zustand, đồng bộ DB khi lưu/để chạy.

---

## 8. Chế độ Batch (pipeline AI từ nội dung → video)

Biến **một nội dung gốc** (chương truyện / truyện ngắn) + **style** + **duration mong muốn
(tùy chọn)** thành kịch bản chuẩn Hollywood rồi sinh ảnh/video hàng loạt, nhất quán nhờ
**entity refs**. Toàn bộ phần "sáng tạo" do **AI-CLI** (mục 9) đảm nhận, app điều phối + lưu trữ.

### Đầu vào (form tạo Batch)
- **style** — phong cách mong muốn (vd "cinematic noir, 35mm, moody lighting").
- **sourceContent** — nội dung gốc (1 chương truyện hoặc 1 truyện ngắn).
- **targetDurationSec** — *tùy chọn*. Có → script viết cho đủ độ dài; không → đo bằng TTS.
- config: aspectRatio, tier, model, `shotSec=8`, voiceId, có video/upscale không.

### Pipeline (mỗi bước có thể review/sửa trước khi sang bước sau)

**B1 — Sinh script chuẩn Hollywood (AI).** `POST /api/batches/:id/script`:
1. **Tiền-tách** `sourceContent` thành các **đơn vị có chỉ số** (đoạn văn/câu) `units[0..n]`
   — app tự làm, không nhờ AI. (Vì LLM trả offset ký tự rất hay sai.)
2. Gọi `/api/ai/prompt` đưa `units` (kèm index) + style + targetDuration; AI **gom các đơn
   vị LIÊN TIẾP** thành scene, trả mỗi scene: `heading`, `action`, `narration`, `location`,
   và **`unitStart`/`unitEnd`** (chỉ số đơn vị). App **derive** `sourceStart/sourceEnd` từ chỉ số.
3. **Validate** (Zod + luật): `scene[0].unitStart==0`, `scene[i].unitEnd+1==scene[i+1].unitStart`,
   scene cuối `unitEnd==n` → đảm bảo **liên tiếp & phủ kín** một cách tầm thường, không phụ
   thuộc độ chính xác offset của AI. Sai → gọi lại AI kèm lỗi (mục Rủi ro #9).
4. Lưu `ScriptScene[]`; mỗi `location` map sang entity `type=location` (tạo nếu chưa có).

**B2 — Sinh entity theo style (AI).** `POST /api/batches/:id/entities` → từ script + nội dung
gốc, AI trích **characters / locations / props** kèm **imagePrompt theo `style`**. App tạo
`Entity` (toàn cục) + gắn vào project (`ProjectEntity`), rồi enqueue `GEN_ENTITY_REF` sinh
ảnh tham chiếu. Người dùng review bảng entity, sửa/sinh lại ref.

**B3 — Xác định số shot.**
- **Có `targetDurationSec`:** phân bổ duration cho từng scene (theo độ dài đoạn nội dung /
  narration) → `scene.shotCount = ceil(scene.durationSec / shotSec)`.
- **Không có:** mỗi scene → job `TTS` từ `narration` → Worker **đo duration** (parse header
  WAV 24kHz/mono/16-bit hoặc ffprobe) → `scene.durationSec` → `shotCount = ceil(/shotSec)`.

  App tạo sẵn `shotCount` `Shot` rỗng cho mỗi scene.

**B4 — Sinh prompt ảnh theo số shot (AI).** `POST /api/batches/:id/shots` → với mỗi scene,
AI viết đúng `shotCount` prompt ảnh (theo style), phân bổ diễn biến của scene qua các shot.
**Ràng buộc bắt buộc (app enforce):**
- Mọi shot trong **cùng scene** đều **tham chiếu cùng `locationEntityId`** của scene đó.
- `entityRefs` mỗi shot **tối đa 10 entity** (gồm location + nhân vật + đạo cụ). App cắt/báo
  lỗi nếu AI trả > 10.

**B5 — Run** → `POST /api/batches/:id/run`:
- **Trước khi chạy:** hiện **ước lượng** (số ảnh = tổng shot, số video, số upscale) + credits
  còn lại (`/api/flow/credits`) để người dùng xác nhận; chặn nếu không đủ.
- Mỗi shot: job `GEN_IMAGE` (`character_media_ids` = `referenceMediaId` của `entityRefs`).
- Ảnh xong, nếu bật video: enqueue `GEN_VIDEO` **tạo video từ ảnh** (start = ảnh vừa sinh, 8s).
  (Tùy chọn nối chuỗi bằng `endImage` = ảnh shot kế.)
- Nếu bật upscale: sau video enqueue `UPSCALE`.

**B6 — Xuất XML DaVinci Resolve** (`POST /api/batches/:id/export-resolve`) → mục 8.1.

UI hiển thị tiến độ realtime theo từng bước (script ✓ / entity ✓ / audio ✓ / ảnh ✓ /
video ⏳ / upscale …), cho review & sinh lại từng phần.

**Tham chiếu entity:** ảnh → `character_media_ids`; video từ nhiều ref → `generate-video-refs`
với `reference_media_ids`. Giá trị truyền đi là `referenceMediaId` của entity — **dùng được
nguyên `mediaId` ở bất kỳ dự án nào, không cần upload lại**.

### 8.1 Xuất timeline XML cho DaVinci Resolve

Sau khi shot có video (hoặc chỉ có ảnh), xuất một timeline để dựng tiếp trong Resolve:

- **Định dạng:** FCP7 XML (`.xml`) — Resolve import tốt; (tùy chọn FCPXML `.fcpxml`).
- **Bố cục:** **V1** = các clip video/ảnh của mọi shot theo thứ tự `scene.order` → `shot.order`,
  mỗi clip trim/đặt đúng `durationSec` (8s) và nối tiếp nhau; **A1** = audio narration từng
  scene đặt liên tục theo thời gian. Khung hình & FPS theo `aspectRatio` (vd 1080×1920 @ 30fps).
- **Đường dẫn media:** trỏ tới **file local trong `./media/<projectId>/...`** (đường dẫn
  tuyệt đối) để Resolve relink ngay, không phụ thuộc URL có hạn.
- **Khớp thời lượng:** tổng `durationSec` các shot của một scene = `shotCount × 8s` nên ≈
  duration audio của scene; lệch (do làm tròn `ceil`) thì trim clip cuối hoặc giữ nguyên
  cho Resolve xử lý (ghi chú cho người dùng).
- **Audio video gốc:** clip Veo có thể có sẵn audio. Mặc định đặt **A1 = narration TTS**,
  **A2 = audio gốc của video** (để Resolve tự quyết giữ/tắt). Có cờ "mute video audio".
- **Frame-accurate:** quy ra số frame chính xác theo FPS timeline (8s @ 30fps = 240 frame)
  để start/end clip không lệch.
- Backend dựng XML bằng template (không cần ffmpeg để xuất XML; ffprobe chỉ dùng đo audio),
  trả file cho UI tải về.

---

## 9. API prompt AI-CLI local (trợ lý script/prompt)

Một endpoint để gọi các **AI-CLI cài sẵn trên máy** (claude-code-cli, antigravity-cli,
gemini-cli…) nhằm tự sinh nội dung: chia kịch bản/lời bình thành prompt từng shot, viết
mô tả entity, gợi ý videoPrompt… Kết quả đưa lại vào batch/node.

```
POST /api/ai/prompt
  { cli: "claude" | "antigravity" | <key trong allowlist>,
    prompt: string, system?: string, cwd?: string, timeoutMs?: number,
    json?: boolean }     // json=true: yêu cầu CLI trả JSON để app parse
→ { ok, stdout, stderr, exitCode, durationMs }

GET  /api/ai/clis        // liệt kê các CLI khả dụng (từ allowlist + kiểm tra tồn tại)
```

**Cách chạy:** backend dùng `child_process.spawn` với **allowlist** định nghĩa trong config
(mục 13) — mỗi entry map `key → { cmd, args[], inputMode }`. Prompt truyền qua **stdin**
hoặc file tạm (không nối vào shell), `shell:false` để tránh injection. Có `timeout`,
giới hạn kích thước output, và **cwd nằm trong thư mục cho phép**.

Ví dụ allowlist:
```jsonc
{
  "claude":      { "cmd": "claude",      "args": ["-p", "--output-format", "json"], "inputMode": "stdin" },
  "antigravity": { "cmd": "antigravity", "args": ["prompt"],                         "inputMode": "stdin" }
}
```

> **Bảo mật (xem Rủi ro #7):** chỉ chạy CLI có trong allowlist, không bao giờ dựng lệnh
> từ chuỗi người dùng, không bật shell, chặn cwd ngoài vùng cho phép. Đây là endpoint
> thực thi tiến trình local nên mặc định chỉ nghe trên `127.0.0.1`.

**Dùng trong app (pipeline Batch — mục 8):**
- **B1 Script:** prompt yêu cầu trả JSON `scene[]` (heading, action, narration, location,
  sourceStart/sourceEnd) phủ kín nội dung gốc + đủ duration.
- **B2 Entity:** trả JSON `entity[]` (name, type∈character/location/prop, imagePrompt theo style).
- **B4 Shot prompts:** với mỗi scene trả đúng `shotCount` prompt ảnh, cùng location, ≤10 entityRefs.
- Mọi lời gọi đặt `json:true` để app parse chắc chắn; app **validate ràng buộc** (liên tiếp,
  ≤10 entity, cùng location) trước khi lưu.

---

## 10. Quản lý dự án

- Trang **Projects**: lưới các project (tên, mô tả, số scene/asset, trạng thái, thumbnail).
- Tạo project: nhập tên/mô tả/aspect ratio → backend gọi `create-project` lưu `flowProjectId`.
- Trong project: tab **Batches**, **Flows (node)**, **Entities**, **Assets**.
- Xóa/archive; tùy chọn xóa luôn Flow project (`GET /api/flow/delete-project/{id}`).

> **Tự lưu (auto-save).** Mọi thay đổi (script, entity, shot, prompt…) được ghi thẳng vào
> SQLite qua các API → **không có khái niệm "mất dữ liệu chưa lưu"**; "mở dự án" = chọn từ
> danh sách (load từ DB). Node editor lưu graph khi blur/đổi + nút Lưu thủ công.

### 10.1 Load / Save dự án (export / import bundle)

Để **sao lưu, chia sẻ, hoặc mang sang máy khác**, hỗ trợ xuất/nhập một **bundle .zip** của
nguyên dự án (khác với auto-save vào DB):

- **Save (export)** — `GET /api/projects/:id/export?media=1` → file `.zip` gồm:
  - `project.json`: `{ schemaVersion, project, entities[], projectEntities[], batches[],
    scenes[], shots[], assets[] }` — bao gồm **các entity toàn cục mà dự án tham chiếu**
    (kèm `referenceMediaId`/`referenceImageUrl`).
  - `media/`: *(khi `media=1`)* toàn bộ file trong `./media/<projectId>/` để mở offline ngay.
- **Load (import)** — `POST /api/projects/import` (upload .zip) → khôi phục vào DB:
  - Tạo project mới (id mới) — remap id cho batches/scenes/shots/assets.
  - **Entity:** trùng `id`/`mediaId` thì **dùng lại** (entity toàn cục), chưa có thì tạo
    mới rồi gắn `ProjectEntity` — đúng tinh thần dùng chung xuyên dự án.
  - **Media:** nếu bundle có `media/` → giải nén vào `./media/<newProjectId>/`; nếu không →
    Worker **tải lại từ `mediaId`** qua `GET /api/flow/media/{id}` (URL có thể đã đổi).
  - Kiểm tra `schemaVersion`, báo lỗi nếu lệch lớn.

---

## 11. Quản lý Entity (xuyên dự án)

- **Entity là toàn cục** (bảng `Entity`), liên kết project qua `ProjectEntity` (M:N) →
  cùng một nhân vật dùng lại ở nhiều dự án mà không nhân bản.
- Trang **Entities** (toàn cục): tạo/sửa, lọc theo type, xem ảnh ref, "Sinh ảnh tham chiếu"
  (job `GEN_ENTITY_REF` → `generate-image` từ `imagePrompt`), hoặc **upload ảnh** có sẵn
  (`upload-image`) để lấy `referenceMediaId`.
- Trong project: chọn entity từ thư viện toàn cục để "gắn" vào project; khi sinh scene
  chỉ chọn trong số entity đã gắn.
- Cảnh báo khi xóa entity đang được scene/dự án khác dùng.

> **Dùng xuyên dự án:** `referenceMediaId` của entity dùng được ở **mọi dự án chỉ với
> chính `mediaId` đó — không cần upload lại**. Gắn entity vào project mới chỉ là tạo bản
> ghi `ProjectEntity` (liên kết bằng id); khi sinh ảnh/video, truyền thẳng `referenceMediaId`
> vào `character_media_ids` / `reference_media_ids`.

---

## 12. Cấu trúc thư mục (đề xuất)

```
video-app/
├─ prisma/
│  ├─ schema.prisma
│  └─ dev.db
├─ src/
│  ├─ app/
│  │  ├─ (dashboard)/projects/...      # trang quản lý
│  │  ├─ projects/[id]/node/page.tsx   # node editor
│  │  ├─ projects/[id]/batch/page.tsx  # batch editor
│  │  ├─ entities/page.tsx             # entity manager toàn cục
│  │  └─ api/                          # route handlers (mục 5.2)
│  ├─ components/ (ui, node/*, batch/*, gallery/*)
│  ├─ lib/
│  │  ├─ db.ts          # Prisma client singleton
│  │  ├─ flowkit.ts     # wrapper gọi FlowKit (/api/flow, /api/tts)
│  │  ├─ jobs.ts        # tạo/đọc job
│  │  ├─ media.ts       # tải file về ./media, đo duration audio (ffprobe/WAV)
│  │  ├─ resolve.ts     # dựng FCP7 XML cho DaVinci Resolve
│  │  ├─ ai-cli.ts      # spawn AI-CLI theo allowlist (stdin, timeout)
│  │  ├─ bundle.ts      # export/import dự án (.zip: project.json + media/)
│  │  └─ graph.ts       # topo sort + validate node graph
│  └─ types/
├─ worker/
│  └─ index.ts          # vòng lặp xử lý job (gen + poll + tải media)
├─ media/               # ./media/<projectId>/<kind>_<mediaId>.<ext> (gitignore)
├─ .env
└─ package.json
```

---

## 13. Cấu hình (.env)

```
DATABASE_URL="file:./prisma/dev.db"
FLOWKIT_URL="http://127.0.0.1:8100"   # engine sinh nội dung
WORKER_CONCURRENCY=4
VIDEO_POLL_INTERVAL_MS=10000
VIDEO_POLL_TIMEOUT_MS=420000
MAX_RETRIES=3
MEDIA_DIR="./media"                   # ./media/<projectId>/<kind>_<mediaId>.<ext> — phục vụ hiển thị
SHOT_SEC=8                            # độ dài mặc định mỗi shot (giây)
FFPROBE_PATH="ffprobe"                # đo duration audio TTS (fallback: parse header WAV)
AI_BIND="127.0.0.1"                   # endpoint AI-CLI chỉ nghe local
AI_CLI_ALLOWLIST='{"claude":{"cmd":"claude","args":["-p","--output-format","json"],"inputMode":"stdin"},"antigravity":{"cmd":"antigravity","args":["prompt"],"inputMode":"stdin"}}'
AI_CWD_ROOT="./"                      # giới hạn cwd cho phép khi chạy AI-CLI
```

---

## 14. Lộ trình triển khai (phases)

**Phase 0 — Nền tảng (0.5–1 ngày)**
- Khởi tạo Next.js + Tailwind + Prisma + SQLite. Tạo schema, migrate.
- `lib/flowkit.ts` + health check tới FlowKit. Trang layout cơ bản.

**Phase 1 — Project & Entity (1–2 ngày)**
- CRUD Project (kèm `create-project`). CRUD Entity toàn cục + gắn/gỡ project.
- Sinh ảnh tham chiếu entity (job đơn giản, đồng bộ). Asset + tải về `./media` + gallery.

**Phase 2 — Worker & Job (1–2 ngày)**
- Bảng Job + Worker loop. Sinh ảnh qua job + tải file local. Polling video + upscale.
- UI tiến độ (polling `GET /api/jobs` trước, SSE sau).

**Phase 3 — API AI-CLI local (0.5–1 ngày)** *(tiền đề cho Batch)*
- `/api/ai/prompt` + `/api/ai/clis` (spawn allowlist, stdin, timeout, validate JSON).

**Phase 4 — Batch pipeline AI (3–4 ngày)**  ← ưu tiên
- Form đầu vào (style, sourceContent, targetDuration?). B1 script + validate liên tiếp.
- B2 entity theo style + ảnh ref. B3 plan-shots (TTS đo duration nếu cần). B4 prompt shot
  (cùng location, ≤10 ref). B5 run → ảnh→video→upscale. Review & sinh lại từng bước.

**Phase 5 — Xuất XML DaVinci Resolve (0.5–1 ngày)** *(B6)*
- Dựng FCP7 XML (V1 video/ảnh theo `durationSec`, A1 audio narration), trỏ file `./media`.
- `POST /api/batches/:id/export-resolve` → tải file.

**Phase 6 — Node mode (2–3 ngày)**
- React Flow canvas + các node (mục 7). Lưu graph. Topo sort + validate.
- Run graph → job theo phụ thuộc. Trạng thái node realtime.

**Phase 7 — Hoàn thiện (1–2 ngày)**
- **Load/Save dự án** (export/import bundle .zip, mục 10.1).
- Refresh URL hết hạn, dọn `./media`, TTS node/asset.
- Xử lý lỗi extension (NO_FLOW_KEY/disconnect), trang trạng thái hệ thống.

**Phase 8 (mở rộng, tùy chọn)**
- Ghép video cuối (ffmpeg), text overlay, music, thumbnail, upload YouTube.

---

## 15. Rủi ro & lưu ý kỹ thuật

1. **Video bất đồng bộ + URL hết hạn.** Bắt buộc có Worker poll; sau khi xong **tải file
   về `./media/...` ngay** và hiển thị từ local. `mediaId` là khóa lưu trữ; URL gốc chỉ
   để tải (refresh bằng `GET /api/flow/media/{mediaId}` nếu hết hạn trước khi tải xong).
2. **Phụ thuộc extension.** FlowKit cần Chrome extension kết nối (`extension_connected`).
   App phải kiểm tra `/health` và báo rõ khi chưa kết nối / `NO_FLOW_KEY` / CAPTCHA.
3. **Dọn dẹp `./media`.** File local tích lũy theo thời gian → cần chiến lược dọn khi xóa
   project/asset, và xử lý trường hợp file local bị xóa thủ công (tải lại từ `mediaId`).
4. **Giới hạn đồng thời/giá.** Tôn trọng ~5 request song song, cooldown, theo dõi credits
   (`/api/flow/credits`). Batch có thể là **hàng trăm shot** → ước lượng & xác nhận trước
   khi chạy (B5); cho phép tạm dừng/hủy batch giữa chừng và chạy lại chỉ phần FAILED.
5. **2 tiến trình, 1 SQLite.** SQLite cho phép nhiều tiến trình đọc nhưng ghi tuần tự;
   bật WAL mode, giữ transaction ngắn. Worker là nơi ghi chính.
6. **TTS Colab.** URL ngrok đổi mỗi phiên → nhắc người dùng cập nhật `PUT /api/tts/config`;
   inference chậm → để timeout cao + chạy qua job.
7. **AI-CLI = thực thi tiến trình local (nhạy cảm).** Chỉ chạy CLI trong **allowlist**,
   `shell:false`, prompt qua **stdin/file tạm** (không nối chuỗi lệnh), có timeout + giới
   hạn output, chặn cwd ngoài `AI_CWD_ROOT`, endpoint chỉ nghe `127.0.0.1`.
8. **Khớp thời lượng audio↔shot.** `ceil(duration/8)` có thể dư vài giây ở shot cuối;
   khi xuất XML cần quyết định trim hay để Resolve xử lý — ghi chú rõ cho người dùng.
9. **AI có thể trả sai cấu trúc.** B1/B2/B4 phải **validate bằng Zod + luật nghiệp vụ**:
   các đoạn nội dung **liên tiếp & phủ kín** `[0, len(sourceContent)]`, mỗi shot **≤10 entity**
   và **cùng location** với scene, đủ `shotCount` prompt. Sai → tự sửa (cắt/ghép) hoặc gọi
   lại AI với phản hồi lỗi (retry có hướng dẫn) thay vì lưu mù.

---

## 16. Quyết định đã chốt & giả định

- **Backend:** đặt trong **Next.js** (route handlers), không tách service riêng. ✅
- **Ưu tiên:** build **Batch mode trước**, Node mode sau. ✅
- **Entity xuyên dự án:** chỉ cần `mediaId`/`id`, **không upload lại**. ✅
- **Lưu media:** Worker tải về `./media/<projectId>/<kind>_<mediaId>.<ext>`, app hiển thị
  từ file local cho nhanh. ✅
- **Batch = pipeline AI:** đầu vào (style + nội dung gốc + duration tùy chọn) → B1 script
  Hollywood (mỗi scene gắn 1 địa điểm + 1 đoạn nội dung gốc **liên tiếp, phủ kín**) → B2 sinh
  entity theo style → B3 số shot (duration đặt sẵn, hoặc TTS đo duration) → B4 prompt ảnh
  (cùng scene cùng location, **≤10 entity/shot**) → B5 ảnh→video→upscale. ✅
- **TTS đo duration sinh per-scene:** mỗi `ScriptScene` một đoạn TTS riêng (không TTS gộp
  cả nội dung rồi cắt). ✅
- **Xuất XML DaVinci Resolve** từ batch (V1 clip theo `durationSec`, A1 audio narration). ✅
- **API AI-CLI local** (`/api/ai/prompt`) gọi claude/antigravity… qua allowlist + stdin,
  là **tiền đề** cho pipeline batch (B1/B2/B4). ✅
- **Load/Save dự án:** auto-save vào SQLite; thêm export/import **bundle .zip** (mục 10.1)
  để sao lưu/chia sẻ/chuyển máy. ✅
- **Giả định:** app chạy local 1 người dùng; FlowKit agent + extension đã hoạt động.
- **Còn để ngỏ (chốt khi vào Phase 2):** dùng polling `GET /api/jobs` (đơn giản, khuyến
  nghị) hay làm SSE realtime ngay từ đầu.

---

## 17. Kiểm thử, log & quan sát

- **Unit test (ưu tiên, thuần logic — không cần FlowKit):** `lib/graph.ts` (topo sort,
  phát hiện vòng lặp, kiểm tra kiểu), validate B1/B4 (liên tiếp & phủ kín, ≤10 entity,
  cùng location), `lib/resolve.ts` (số frame, thứ tự clip, đường dẫn), parse duration WAV.
- **Integration:** mock FlowKit (`FLOWKIT_URL` trỏ server giả) để test Worker chạy chuỗi
  job ảnh→video→upscale + cascade + khôi phục sau crash, không tốn credits.
- **Smoke thật:** 1 batch nhỏ (1 scene, 1–2 shot) chạy end-to-end với FlowKit + extension thật.
- **Log per-job:** lưu `error`/`attempts`; trang **System status** hiển thị health FlowKit
  (`/health`), credits, hàng đợi job, lỗi gần nhất → soi nhanh khi pipeline kẹt.
- **Bảo mật cục bộ:** Next.js + Worker chỉ bind `127.0.0.1` (như AI-CLI), không expose ra LAN.
