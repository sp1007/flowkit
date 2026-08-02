"""Quy tắc gom frame storyboard thành CLIP — đơn vị render video.

Tab Storyboard cắt scene thành các FRAME: những khoảnh khắc chính của MỘT cú máy liên tục
(xem `brain._CONTINUITY`). Một frame KHÔNG phải một clip — nó chỉ là một tư thế. Tab Shots gom
các frame liền nhau thành clip, mỗi clip render bằng MỘT lượt Omni Flash r2v với toàn bộ frame
của nhóm làm reference `frame 1..N` và prompt là timeline đi xuyên chúng. Đoạn CHUYỂN TIẾP
giữa hai frame — thứ storyboard không vẽ ra được — do model dựng.

Số shot ở tab Shots vì thế KHÁC số frame ở tab Storyboard.

Quy tắc nằm riêng ở đây vì cả tầng API (gom/ render) lẫn `assembler` (ghép video cuối) đều
phải chia nhóm y hệt nhau; lệch nhau một nhịp là lời đọc của các frame sau trong nhóm biến mất
khỏi video cuối.
"""
from __future__ import annotations

import itertools
import os

# TRẦN CỨNG do model quy định, không phải lựa chọn thẩm mỹ: clip dài nhất Omni Flash cho phép
# là 10s (các model khác 8s), quá 6 frame thì mỗi frame còn chưa tới 1.7s — model không kịp
# chạm tới frame cuối trước khi hết giờ. Số frame/clip THỰC DÙNG là `project.clip_frames`, đặt
# ở ⚙ Cấu hình dự án và luôn bị kẹp xuống trần này.
HARD_MAX_CLIP_FRAMES = max(1, min(6, int(os.environ.get("FLOWKIT_MAX_CLIP_FRAMES", "6"))))

DEFAULT_CLIP_FRAMES = HARD_MAX_CLIP_FRAMES


def frames_per_clip(project: dict | None) -> int:
    """Số frame tối đa mỗi clip của dự án, đã kẹp vào [1, HARD_MAX_CLIP_FRAMES].

    Dự án cũ (chưa có cột) và giá trị rác đều rơi về mặc định thay vì làm hỏng lượt render."""
    try:
        n = int((project or {}).get("clip_frames") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = DEFAULT_CLIP_FRAMES
    return max(1, min(HARD_MAX_CLIP_FRAMES, n))


def split_clips(shots: list[dict], n_max: int = DEFAULT_CLIP_FRAMES) -> list[list[dict]]:
    """Shot của MỘT scene (đã sắp theo idx) → danh sách clip.

    Frame không có `clip_id` đứng một mình. Trần `n_max` được ép lại ở đây chứ không chỉ lúc
    gom: hạ số frame/clip trong cấu hình dự án là các nhóm đang có tự tách ra theo, và một
    nhóm quá dài (sửa tay trong DB) không đẩy được request quá tải lên Flow."""
    n_max = max(1, min(HARD_MAX_CLIP_FRAMES, n_max))
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for s in shots:
        cid = s.get("clip_id")
        if cur and cid and cur[-1].get("clip_id") == cid and len(cur) < n_max:
            cur.append(s)
            continue
        if cur:
            groups.append(cur)
        cur = [s]
    if cur:
        groups.append(cur)
    return groups


def clip_groups(shots: list[dict], n_max: int = DEFAULT_CLIP_FRAMES) -> list[list[dict]]:
    """Như `split_clips` nhưng cho shot của NHIỀU scene (đã sắp theo scene.idx, shot.idx).

    Clip không bao giờ vắt qua ranh giới scene — mỗi scene là một địa điểm/thời điểm riêng."""
    out: list[list[dict]] = []
    for _, group in itertools.groupby(shots, key=lambda s: s["scene_id"]):
        out.extend(split_clips(list(group), n_max))
    return out


def pack_clips(shots: list[dict], n_max: int, budget: float) -> list[list[dict]]:
    """Xếp các frame liên tiếp vào clip: tối đa `n_max` frame và không quá `budget` giây.

    `n_max` đến từ `frames_per_clip(project)`.

    `budget` chỉ ràng buộc khi frame có lời đọc ĐÃ ĐO (`narration_duration`) — chế độ kể
    chuyện, ở đó mỗi frame đã tự chiếm 8–10s nên nhóm ra đúng một frame như trước khi có clip.
    Frame không có lời đọc (autofill thường) chỉ bị giới hạn bởi số lượng."""
    out: list[list[dict]] = []
    cur: list[dict] = []
    acc = 0.0
    for s in shots:
        d = float(s.get("narration_duration") or 0)
        if cur and (len(cur) >= n_max or (d and acc + d > budget + 1e-6)):
            out.append(cur)
            cur, acc = [], 0.0
        cur.append(s)
        acc += d
    if cur:
        out.append(cur)
    return out
