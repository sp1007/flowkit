# Chạy bản nào

Hai bản chạy **song song, độc lập hoàn toàn** — khác thư mục, khác cổng, khác tên
extension, khác tài khoản Flow. Cài cả hai không đụng nhau.

|  | bản chính | bản mới |
|---|---|---|
| thư mục | `D:\youtube\editor\flowkit` | `D:\youtube\editor\flowkit-next` |
| nhánh git | `main` | `flow-new-stack` |
| tên extension | **Flow Kit** | **Flow Kit Next** |
| API | `127.0.0.1:8100` | `127.0.0.1:8200` |
| WebSocket | `9222` | `9200` |
| webapp (dev) | `5173` | `5200` |
| xác thực Flow | token `ya29.*` + `aisandbox-pa` | cookie phiên + `at`, qua `batchexecute` |

## Hằng ngày thì chạy bản chính

Bản chính là bản đang chạy sản xuất — storyboard, TTS, dựng shots, xuất DaVinci. Đừng
đổi sang bản mới chỉ vì nó mới hơn: bản mới mới chỉ có tầng gọi Flow, chưa có studio.

```bash
cd D:\youtube\editor\flowkit
python -m agent.main          # 8100 + 9222
```
Trình duyệt chính: bật extension **Flow Kit**, mở một tab Flow.

## Bản mới để làm gì

Nó là **đường thoát cho ngày `labs.google/fx` tắt**. Ngày đó token `ya29` không còn ai
phát nữa và toàn bộ đường cũ chết theo — kể cả khâu sinh, không chỉ mấy endpoint tRPC.

```bash
cd D:\youtube\editor\flowkit-next
set FLOWKIT_FLOW_TIER=3       # 1 = miễn phí, 2 = Pro, 3 = Ultra
python -m agent.main          # 8200 + 9200
```
Trình duyệt thứ hai: bật extension **Flow Kit Next**, mở một tab `flow.google.com`.

`FLOWKIT_FLOW_TIER` quyết định hàng rào giá, và đặt sai hỏng theo hai kiểu khác nhau:
đặt cao hơn thực tế thì lượt gọi chắc chắn hỏng vẫn đi qua rồi chỉ nhận `error [3]`
trống rỗng; đặt thấp hơn thì chặn oan thao tác vẫn làm được.

## Bản mới làm được gì rồi

Nhóm `/api/flow/v2/*`, 24 endpoint, **không dùng token `ya29` ở đâu cả**:

- ảnh: sinh, sửa, tải lên
- video: không ảnh · ảnh tham chiếu · một khung · hai khung · nối dài · sửa · upscale
- dự án: liệt kê (**đi hết mọi trang**), tạo, đổi tên, ảnh bìa, xoá
- workflow: đổi tên, chuyển thùng rác, đặt media chính
- `GET /models` — bảng giá thật theo hạng tài khoản

Đã chạy thật cả chuỗi: tạo dự án → sinh video → poll → upscale 1080p → tạo scene →
nối dài → xoá dự án, **hết 0 credit**.

## Ba thứ bản mới làm được mà bản chính chưa

1. **Nối dài clip** (`POST /v2/video/extend`). Giao diện Flow chỉ cho chọn bản 5 credit;
   API vẫn nhận bản `_low_priority` **0 đồng**, cho ra clip giống hệt.
2. **Sửa video bằng prompt** (`POST /v2/video/edit`). Chỉ Omni Flash làm được.
3. **Một khung 4s/6s** — bản chính chỉ có khoá 8 giây.

Cộng thêm hai chỗ bản chính đang sai mà bản mới đã đúng: **phân trang dự án** (bản chính
chỉ lấy trang đầu, tài khoản 60 dự án thì mất 40 mà không báo lỗi) và **bảng giá**
(`CREDIT_COST.video = 20` phẳng, sai cho gần như mọi model).

## Còn thiếu

Tầng `studio` của bản mới vẫn gọi đường cũ. Muốn chuyển hẳn thì phải trỏ nó sang `/v2`,
và đó là việc chưa làm — nên tới lúc `labs.google` tắt thật, bản mới sinh được media
nhưng chưa dựng được video hoàn chỉnh.
