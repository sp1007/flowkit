# Chuyển hẳn sang bản mới

## Không phải chép dữ liệu — trỏ vào là được

`STUDIO_DB` và `STUDIO_MEDIA_DIR` đều đọc từ biến môi trường, nên bản mới dùng thẳng
dữ liệu của bản chính, khỏi chép 14,5 GB:

```bash
cd D:\youtube\editor\flowkit-next
set STUDIO_DB=D:\youtube\editor\flowkit\agent\studio.db
set STUDIO_MEDIA_DIR=D:\youtube\editor\flowkit\media
set FLOWKIT_USE_BOQ=1
set FLOWKIT_FLOW_TIER=3
python -m agent.main
```

Đã chạy thật: `GET /` trả SPA 200, `/media/<pid>/<mid>.png` trả đúng file 1 MB, dự án
đọc được từ DB 60 MB của bản chính.

**Hai bản KHÔNG được chạy cùng lúc khi dùng chung DB.** SQLite không chịu nổi hai tiến
trình cùng ghi. Tắt bản chính trước.

Muốn tách hẳn để bản chính làm bản lùi thì chép:
```bash
copy D:\youtube\editor\flowkit\agent\studio.db D:\youtube\editor\flowkit-next\agent\
robocopy D:\youtube\editor\flowkit\media D:\youtube\editor\flowkit-next\media /E
```

## Webapp

Chưa build sẵn trong worktree mới. Làm một lần:

```bash
cd D:\youtube\editor\flowkit-next\webapp
npm install
npm run build
```

Xong thì `http://127.0.0.1:8200/` chạy như bản chính (đã kiểm, HTTP 200).

## Thứ THẬT SỰ quyết định: TÀI KHOẢN, không phải code

Dữ liệu hiện tại: **63 dự án**, và chúng thuộc ba tài khoản khác nhau.

| tài khoản | số dự án | hạng |
|---|---|---|
| `sonpham82@gmail.com` | **60** | Pro |
| `sp.creator.hn@gmail.com` | 2 | Ultra |
| `pmh.phuc@gmail.com` | 1 | — |

Mỗi dự án thuộc về một tài khoản Flow; đụng vào dự án của tài khoản khác là 403. Đo
trực tiếp: chạy bản mới trên trình duyệt đang đăng nhập tài khoản Ultra thì
`/api/studio/projects` chỉ trả **2 trên 63 dự án**. Không phải lỗi — đúng như thiết kế.

Nên khi chuyển hẳn phải chọn một trong hai, và **lựa chọn này tốn tiền theo hai hướng
ngược nhau**:

**A. Đăng nhập trình duyệt bản mới vào `sonpham82@gmail.com` (Pro)** — đặt
`FLOWKIT_FLOW_TIER=2`. Được đủ 60 dự án cũ. Mất mọi khoá `_low_priority` (chỉ có ở
Ultra), nên **video hết miễn phí**: nối dài 10 credit thay vì 0, Veo Lite 10/clip thay
vì 0. Ảnh vẫn 0 credit cho mọi hạng.

**B. Giữ `sp.creator.hn@gmail.com` (Ultra)** — video vẫn 0 đồng, nhưng 60 dự án cũ chỉ
xem được phần media ĐÃ TẢI VỀ máy. Không sinh thêm, không resolve lại URL (403), và URL
cũ thì hết hạn sau 6 giờ.

Không có đường thứ ba: Flow không cho chuyển chủ sở hữu dự án.

Nếu phần lớn việc sắp tới là **dựng tiếp 60 dự án cũ** thì chọn A. Nếu là **làm dự án
mới** thì chọn B và để 60 dự án cũ nằm ở bản chính.

## Kiểm lại sau khi chuyển

```bash
curl http://127.0.0.1:8200/health          # extension_connected: true
curl http://127.0.0.1:8200/api/flow/credits # userPaygateTier phải ĐÚNG hạng thật
curl http://127.0.0.1:8200/api/studio/projects | head -c 200
```

`userPaygateTier` sai là hỏng LẶNG LẼ: studio dùng nó để chọn độ phân giải upscale và
khoá model, nên đặt nhầm `FLOWKIT_FLOW_TIER` thì 4K âm thầm bị hạ xuống 2K mà không
lỗi nào báo.
