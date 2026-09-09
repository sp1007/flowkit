"""Dựng payload cho các RPC sinh media của Flow (batchexecute).

Module này CHỈ dựng payload và đọc phản hồi — không gọi mạng. Tách ra như vậy để mỗi
hình dạng payload được kiểm bằng chính payload thật đã bắt từ giao diện
(`testdata/boq_payloads.json`), chứ không phải bằng "chạy thử thấy không lỗi".

Bài học đắt nhất của đợt khảo sát, ghi ở đây vì nó quyết định cách đọc cả file:

    KHÔNG có "một RPC sinh video có nhiều tham số". Mỗi kiểu đầu vào là MỘT RPC RIÊNG.

Bảy thao tác, bảy rpcid, không cái nào dùng lại cái nào. Tôi đã đoán ngược lại hai lần
— rằng ảnh tham chiếu là `YhhmEf` thêm tham số, rằng một-khung là `nprQif` bỏ trống ô
thứ hai — và cả hai lần đều sai, tốn bốn lượt thử vô ích trả về `error [3]`. Thêm khả
năng mới thì BẮT giao diện trước, đừng suy từ payload đã biết.

Vị trí các ô KHÔNG so sánh được giữa các rpcid: chúng là message type khác nhau nên
cùng một khái niệm nằm ở chỉ số khác nhau (prompt ở [0] của `YhhmEf` nhưng ở [1] của
`fZytfe`). Đừng "tối ưu" bằng cách gộp các builder lại.
"""
from __future__ import annotations

import uuid
from typing import Optional, Sequence

# Chỗ trống để extension nhét token reCAPTCHA vào. Agent không cần biết gì về reCAPTCHA;
# extension quét payload tìm đúng chuỗi này rồi thay. Token dùng một lần, sống ~2 phút,
# nên không thể chép lại token bắt được — phải lấy mới mỗi lượt.
CAPTCHA = "__CAPTCHA__"

# Tỉ lệ khung của VIDEO. Chỉ hai giá trị này là ĐO ĐƯỢC; bảng tỉ lệ của ảnh khác hẳn
# (1..5, xem ops.generate_image trong boq_rpcids.json) nên đừng dùng chung.
RATIO_PORTRAIT = 1     # 9:16  — đo: 720x1280
RATIO_LANDSCAPE = 2    # 16:9  — đo: 1280x720

_RATIO_WH = {RATIO_PORTRAIT: (9, 16), RATIO_LANDSCAPE: (16, 9)}

# Kiểu ảnh đầu vào của lượt sinh ẢNH (rpcid ogiZ0b).
IMAGE_INPUT_REFERENCE = 1
IMAGE_INPUT_BASE = 2

# Số hình mỗi giây của mọi clip Flow phát ra. Đo bằng ffprobe trên nhiều file: luôn 24/1.
FPS = 24


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def client_context(project_id: str, workflow_id: Optional[str] = None,
                   captcha: bool = True) -> list:
    """clientContext dùng chung cho mọi lượt sinh.

    Ô [4] là workflowId — null khi tạo mới, có giá trị khi nối kết quả vào một workflow
    đang có (lượt sửa ảnh). Ô [5] là projectId.
    """
    ctx = [None, 22, None, None, workflow_id, project_id,
           None, None, None, None]
    ctx.append([CAPTCHA, 1] if captcha else None)
    return ctx


def prompt_block(prompt: str) -> list:
    """Prompt một mảnh chữ.

    `[[["..."]]]` KHÔNG phải "prompt lồng ba lớp" như tôi ghi lúc đầu — nó là danh sách
    mảnh chỉ có MỘT mảnh chữ. Thấy rõ khi bắt được lượt r2v với năm mảnh xen ảnh.
    """
    return [None, None, [[[prompt]]]]


def structured_prompt_block(parts: Sequence[dict]) -> list:
    """Prompt có ảnh neo trong câu, dùng cho r2v.

    Mỗi mảnh là {"text": "..."} hoặc {"media_id": "...", "name": "..."}. Cấu trúc này
    giống hệt bên ảnh, nên `_build_structured_parts` và `dedupe_refs` của bản chính
    dùng lại được nguyên — kèm cả hai cái bẫy đã biết: token `{tên}` lặp lại sinh nhiều
    part trỏ cùng một mediaId thì Flow trả lỗi, và mảnh chữ bị vụn ra cũng trả lỗi.
    """
    out = []
    for part in parts:
        if "media_id" in part:
            out.append([None, [[part["media_id"], part.get("name") or ""]]])
        else:
            out.append([part["text"]])
    return [None, None, [out]]


# Kích thước thật của ảnh Flow theo từng nhóm tỉ lệ (đo đủ 5 nhóm, xem
# ops.generate_image trong boq_rpcids.json). Chú ý: kích thước thật KHÔNG đúng tỉ lệ
# danh nghĩa — 1376x768 là 1.7917 chứ không phải 16/9 = 1.7778.
IMAGE_RATIO_WH = {
    1: (1, 1),      # 1024x1024
    2: (9, 16),     # 768x1376
    3: (16, 9),     # 1376x768
    4: (3, 4),      # 896x1200
    5: (4, 3),      # 1200x896
}


def nominal_ratio(px_w: int, px_h: int) -> tuple[int, int]:
    """Nhóm tỉ lệ danh nghĩa gần nhất với kích thước pixel thật."""
    actual = px_w / px_h
    return min(IMAGE_RATIO_WH.values(), key=lambda wh: abs(actual - wh[0] / wh[1]))


def crop_rect(src_w: int, src_h: int, ratio: int) -> Optional[list]:
    """Khung cắt chuẩn hoá `[trên, trái, dưới, phải]` để ảnh khớp tỉ lệ video.

    Giao diện tự cắt khi tỉ lệ ảnh khác tỉ lệ video, và nếu ta không gửi thì Flow xử lý
    theo cách ta không kiểm soát.

    TÍNH THEO NHÓM TỈ LỆ DANH NGHĨA, KHÔNG THEO SỐ PIXEL THẬT. Ảnh 1376x768 dựng video
    9:16 thì giao diện gửi `[null, 0.341796875, 1, 0.658203125]`, ứng với bề rộng giữ
    lại (9/16)/(16/9) = 81/256 = 0.31640625. Tính theo pixel thật (1376/768 = 1.7917,
    không phải 16/9) ra 0.34302 — lệch, và khung cắt lệch thì nội dung bị xén sai chỗ.
    Ảnh Flow không bao giờ đúng tỉ lệ danh nghĩa, nên chỗ này luôn sai nếu dùng pixel.

    Trả None khi tỉ lệ đã khớp; ô này bỏ trống được (đã thử, vẫn chạy).
    """
    sw, sh = nominal_ratio(src_w, src_h)
    tw, th = _RATIO_WH[ratio]
    src = sw / sh
    tgt = tw / th
    if abs(src - tgt) < 1e-9:
        return None
    if src > tgt:                       # ảnh rộng hơn khung → cắt hai bên
        frac = tgt / src
        off = (1 - frac) / 2
        return [None, off, 1, off + frac]
    frac = src / tgt                    # ảnh cao hơn khung → cắt trên dưới
    off = (1 - frac) / 2
    return [off, None, off + frac, 1]


def frame(media_id: str, crop: Optional[list] = None) -> list:
    """Một khung hình đầu vào, kèm khung cắt (bỏ trống được)."""
    return [None, media_id, None, None, None, crop]


def seconds_to_frames(seconds: float) -> int:
    return int(round(seconds * FPS))


# ─── Bảy thao tác video ──────────────────────────────────────
# Mỗi hàm trả (rpcid, args). Không hàm nào gọi mạng.

def generate_video_text(prompt: str, model_key: str, project_id: str,
                        ratio: int = RATIO_LANDSCAPE,
                        batch_id: Optional[str] = None) -> tuple[str, list]:
    """Sinh video từ prompt, không ảnh nào."""
    item = [prompt_block(prompt), model_key, ratio, None,
            [None, None, None, None, _uid(), _uid()]]
    return "YhhmEf", [[item], client_context(project_id), [batch_id or _uid(), 2]]


def generate_video_refs(parts: Sequence[dict], model_key: str, project_id: str,
                        ratio: int = RATIO_LANDSCAPE,
                        batch_id: Optional[str] = None) -> tuple[str, list]:
    """Sinh video từ ẢNH THAM CHIẾU neo trong prompt.

    `parts` theo định dạng của `structured_prompt_block`. Danh sách ảnh ở ô [1] được
    suy ra từ chính các mảnh, theo đúng thứ tự xuất hiện và bỏ trùng — phải liệt kê đủ
    mọi ảnh được neo, và liệt kê thừa thì không có ảnh tương ứng trong câu.
    """
    seen, refs = set(), []
    for part in parts:
        mid = part.get("media_id")
        if mid and mid not in seen:
            seen.add(mid)
            refs.append([None, mid])
    item = [structured_prompt_block(parts), refs, model_key, ratio, None,
            [None, None, None, None, _uid(), _uid()]]
    return "MZZa6b", [[item], client_context(project_id), [batch_id or _uid(), 2]]


def generate_video_frame(prompt: str, media_id: str, model_key: str, project_id: str,
                         ratio: int = RATIO_LANDSCAPE, crop: Optional[list] = None,
                         batch_id: Optional[str] = None) -> tuple[str, list]:
    """Sinh video từ MỘT khung đầu (i2v).

    Không phải `nprQif` bỏ trống ô thứ hai — đó là điều tôi đoán và sai. Một khung có ô
    ảnh ở [4] và đích ở [5]; hai khung có ảnh ở [4],[5] và đích ở [6].
    """
    item = [prompt_block(prompt), model_key, ratio, None,
            frame(media_id, crop),
            [None, None, None, None, _uid(), _uid()]]
    return "eb1hJf", [[item], client_context(project_id), [batch_id or _uid(), 2]]


def generate_video_frames(prompt: str, first_media_id: str, last_media_id: str,
                          model_key: str, project_id: str,
                          ratio: int = RATIO_LANDSCAPE,
                          first_crop: Optional[list] = None,
                          last_crop: Optional[list] = None,
                          batch_id: Optional[str] = None) -> tuple[str, list]:
    """Sinh video nội suy giữa khung ĐẦU và khung CUỐI.

    Hậu tố `_fl` trong tên khoá model nghĩa là FIRST-LAST, tức đúng thao tác này; khoá
    một-khung không có `_fl`. Veo nội suy cố định 8 giây, còn Omni để độ dài trong tên
    khoá nên chọn được 4/6/8/10.
    """
    item = [prompt_block(prompt), model_key, ratio, None,
            frame(first_media_id, first_crop),
            frame(last_media_id, last_crop),
            [None, None, None, None, _uid(), _uid()]]
    return "nprQif", [[item], client_context(project_id), [batch_id or _uid(), 2]]


def extend_video(prompt: str, source_media_id: str, scene_id: str, model_key: str,
                 project_id: str, ratio: int = RATIO_LANDSCAPE,
                 from_frame: Optional[int] = None, to_frame: int = 8 * FPS,
                 position: int = 1,
                 batch_id: Optional[str] = None) -> tuple[str, list]:
    """Nối dài một clip.

    Chỉ chạy TRONG một scene: phải tạo scene trước (rpcid `rqZuUc`) rồi mới gọi được.

    `from_frame`..`to_frame` là chỉ số khung hình trên clip nguồn ở 24fps — giao diện
    gửi 169..192 cho clip 8 giây, tức lấy ~1 giây cuối làm mồi. Clip giao ra dài 7 giây
    chứ không phải 8 (8 giây sinh ra trừ 1 giây mồi); đó là suy diễn khớp số liệu, chưa
    có xác nhận từ Google.
    """
    if from_frame is None:
        from_frame = to_frame - FPS + 1
    item = [[None, source_media_id, from_frame, to_frame],
            prompt_block(prompt), model_key, ratio, None,
            [scene_id, None, None, None, _uid(), _uid()]]
    return "fZytfe", [[item], client_context(project_id),
                      [batch_id or _uid(), 2, None, [scene_id, position]]]


def edit_video(prompt: str, source_media_id: str, workflow_id: str, model_key: str,
               project_id: str, ratio: int = RATIO_LANDSCAPE,
               from_frame: int = 0, to_frame: int = 8 * FPS,
               batch_id: Optional[str] = None) -> tuple[str, list]:
    """Sửa một clip bằng prompt. CHỈ Omni Flash làm được (`abra_edit*`).

    Kết quả là bản ghi MỚI nằm trong cùng workflow; ảnh đại diện của workflow không đổi
    cho tới khi gọi `set_primary_media`. Giao diện tự gọi ngay sau khi render xong, ta
    thì không bắt buộc — nên sửa video qua API là thao tác cộng thêm, không đè bản gốc.
    """
    item = [[None, source_media_id, from_frame, to_frame],
            prompt_block(prompt), model_key, ratio,
            [None, workflow_id, None, None, _uid()]]
    return "jIps6", [[item], client_context(project_id), [batch_id or _uid(), 2]]


def upscale_video(media_id: str, workflow_id: str, model_key: str, project_id: str,
                  ratio: int = RATIO_LANDSCAPE,
                  batch_id: Optional[str] = None) -> tuple[str, list]:
    """Nâng độ phân giải một clip lên 1080p (0 credit) hoặc 4K (50, chỉ Ultra).

    Ô đầu là mảng 32 phần tử mà gần như toàn null. SỐ Ô PHẢI ĐÚNG 32 — dựng 31 thì khoá
    model rơi về chỉ số 30 và server trả `error [3]` không nói gì thêm.

    Media kết quả KHÔNG mang UUID mới mà là id dẫn xuất: `<mediaId>_upsampled` cho
    1080p và `<mediaId>_4k_upsampled` cho 4K. Hai bản cùng tồn tại, 4K không đè 1080p.
    Vì id đoán trước được nên không cần lưu id trả về.
    """
    level = 3 if "4k" in model_key else 2
    first = [None] * 32
    first[0] = [None, media_id]
    first[2] = ratio
    first[4] = [None, workflow_id, None, None, _uid()]
    first[6] = level
    first[31] = model_key
    # Đuôi CHỈ có một phần tử ở đây, khác edit_video và extend_video.
    return "p0UkFb", [[first], client_context(project_id), [batch_id or _uid()]]


def upsampled_media_id(media_id: str, four_k: bool = False) -> str:
    """Id của bản đã nâng cấp — đoán trước được, khỏi lưu."""
    return f"{media_id}_4k_upsampled" if four_k else f"{media_id}_upsampled"


# ─── Đọc phản hồi ────────────────────────────────────────────

# Trạng thái nằm ở workflow[5][8]. Đã đo: [6] khi đang chờ/đang chạy, [3] khi xong.
STATUS_RUNNING = 6
STATUS_DONE = 3


def _dig(node, *path):
    for key in path:
        if not isinstance(node, list) or key >= len(node):
            return None
        node = node[key]
    return node


def workflow_status(workflow: list) -> Optional[int]:
    st = _dig(workflow, 5, 8)
    return st[0] if isinstance(st, list) and st else None


def workflow_done(workflow: list) -> bool:
    return workflow_status(workflow) == STATUS_DONE


def workflow_media_id(workflow: list) -> Optional[str]:
    """mediaId của một bản ghi workflow.

    CHỈ SỐ 7, không phải 6. Ô [6] luôn null, và tôi từng lấy nó làm cờ "đang render" —
    nên mọi lượt poll đều báo chưa xong và tôi kết luận nhầm rằng hàng đợi 0 đồng chậm
    hơn hẳn. Nó không hề chậm.
    """
    block = _dig(workflow, 7, 2)
    return block[0] if isinstance(block, list) and block else None


def credits_left(data: list) -> Optional[int]:
    """Số dư credit đi kèm phản hồi của mọi lượt submit — khỏi gọi `nzlxg` riêng."""
    return _dig(data, 1)
