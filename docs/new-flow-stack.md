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
