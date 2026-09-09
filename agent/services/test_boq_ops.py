"""Đối chiếu payload dựng bằng code với payload THẬT bắt từ giao diện Flow.

Đây là phép thử duy nhất thật sự chứng minh được builder đúng. "Gọi thử thấy 200" không
chứng minh gì: server bỏ qua ô thừa và im lặng khi ô thiếu, nên một payload lệch vẫn có
thể chạy rồi cho kết quả sai kiểu khác (đúng chuyện đã xảy ra với upscale — dựng 31 ô
thay vì 32 thì khoá model rơi sang chỗ khác và chỉ nhận được `error [3]` trống rỗng).

Mẫu nằm ở `testdata/boq_payloads.json`, mỗi rpcid một bản ghi thật, token reCAPTCHA đã
thay bằng chỗ trống. So sánh sau khi chuẩn hoá những thứ ĐƯƠNG NHIÊN khác nhau mỗi lượt:
UUID, batch id, seed, và nội dung prompt.
"""
import json
import os
import re

from agent.services import boq_ops as ops

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

with open(os.path.join(os.path.dirname(__file__), "testdata", "boq_payloads.json"),
          encoding="utf-8") as fh:
    GOLDEN = json.load(fh)


def norm(node, keep=()):
    """Thay UUID bằng '<uuid>' để so hình dạng, giữ nguyên id nào được liệt kê ở `keep`."""
    if isinstance(node, list):
        return [norm(x, keep) for x in node]
    if isinstance(node, str) and _UUID_RE.match(node) and node not in keep:
        return "<uuid>"
    return node


def golden(rpcid):
    return GOLDEN[rpcid]["args"]


def test_co_du_tam_mau():
    assert set(GOLDEN) == {"ogiZ0b", "YhhmEf", "MZZa6b", "eb1hJf",
                           "nprQif", "fZytfe", "jIps6", "p0UkFb"}


# ─── Từng thao tác một ───────────────────────────────────────

def test_video_khong_anh():
    real = golden("YhhmEf")
    pid = real[1][5]
    prompt = real[0][0][0][2][0][0][0]
    rpcid, args = ops.generate_video_text(prompt, real[0][0][1], pid, ratio=real[0][0][2])
    assert rpcid == "YhhmEf"
    assert norm(args, keep={pid}) == norm(real, keep={pid})


def test_video_anh_tham_chieu():
    real = golden("MZZa6b")
    pid = real[1][5]
    parts_real = real[0][0][0][2][0]
    parts = []
    for p in parts_real:
        if p[0] is None:                     # mảnh ảnh: [null, [[mediaId, tên]]]
            parts.append({"media_id": p[1][0][0], "name": p[1][0][1]})
        else:
            parts.append({"text": p[0]})
    ids = {p["media_id"] for p in parts if "media_id" in p}
    rpcid, args = ops.generate_video_refs(parts, real[0][0][2], pid, ratio=real[0][0][3])
    assert rpcid == "MZZa6b"
    assert norm(args, keep=ids | {pid}) == norm(real, keep=ids | {pid})


def test_video_mot_khung():
    real = golden("eb1hJf")
    pid, mid = real[1][5], real[0][0][4][1]
    prompt = real[0][0][0][2][0][0][0]
    rpcid, args = ops.generate_video_frame(
        prompt, mid, real[0][0][1], pid, ratio=real[0][0][2], crop=real[0][0][4][5])
    assert rpcid == "eb1hJf"
    assert norm(args, keep={mid, pid}) == norm(real, keep={mid, pid})


def test_video_hai_khung():
    real = golden("nprQif")
    pid = real[1][5]
    a, b = real[0][0][4][1], real[0][0][5][1]
    prompt = real[0][0][0][2][0][0][0]
    rpcid, args = ops.generate_video_frames(
        prompt, a, b, real[0][0][1], pid, ratio=real[0][0][2],
        first_crop=real[0][0][4][5], last_crop=real[0][0][5][5])
    assert rpcid == "nprQif"
    assert norm(args, keep={a, b, pid}) == norm(real, keep={a, b, pid})


def test_noi_dai():
    real = golden("fZytfe")
    pid = real[1][5]
    src, frm, to = real[0][0][0][1], real[0][0][0][2], real[0][0][0][3]
    scene = real[0][0][5][0]
    prompt = real[0][0][1][2][0][0][0]
    rpcid, args = ops.extend_video(
        prompt, src, scene, real[0][0][2], pid, ratio=real[0][0][3],
        from_frame=frm, to_frame=to, position=real[2][3][1])
    assert rpcid == "fZytfe"
    keep = {src, scene, pid}
    assert norm(args, keep=keep) == norm(real, keep=keep)


def test_sua_video():
    real = golden("jIps6")
    pid = real[1][5]
    src, frm, to = real[0][0][0][1], real[0][0][0][2], real[0][0][0][3]
    wf = real[0][0][4][1]
    prompt = real[0][0][1][2][0][0][0]
    rpcid, args = ops.edit_video(prompt, src, wf, real[0][0][2], pid,
                                 ratio=real[0][0][3], from_frame=frm, to_frame=to)
    assert rpcid == "jIps6"
    keep = {src, wf, pid}
    assert norm(args, keep=keep) == norm(real, keep=keep)


def test_upscale():
    real = golden("p0UkFb")
    pid = real[1][5]
    mid, wf = real[0][0][0][1], real[0][0][4][1]
    rpcid, args = ops.upscale_video(mid, wf, real[0][0][31], pid, ratio=real[0][0][2])
    assert rpcid == "p0UkFb"
    keep = {mid, wf, pid}
    assert norm(args, keep=keep) == norm(real, keep=keep)


def test_upscale_dung_32_o():
    """Số ô phải đúng 32 — dựng 31 thì khoá model rơi về [30] và chỉ nhận error [3]."""
    _, args = ops.upscale_video("m", "w", "veo_3_1_upsampler_4k", "p")
    assert len(args[0][0]) == 32
    assert args[0][0][31] == "veo_3_1_upsampler_4k"
    assert args[0][0][6] == 3                       # 3 = 4K
    assert len(args[2]) == 1                        # đuôi một phần tử, khác các op khác
    _, args1080 = ops.upscale_video("m", "w", "veo_3_1_upsampler_1080p", "p")
    assert args1080[0][0][6] == 2                   # 2 = 1080p


# ─── Khung cắt ───────────────────────────────────────────────

def test_khung_cat_khop_giao_dien():
    """Ảnh 1376x768 dựng video 9:16 — phải ra đúng con số giao diện gửi."""
    assert ops.crop_rect(1376, 768, ops.RATIO_PORTRAIT) == \
        [None, 0.341796875, 1, 0.658203125]


def test_khung_cat_bo_trong_khi_da_khop():
    assert ops.crop_rect(1376, 768, ops.RATIO_LANDSCAPE) is None
    assert ops.crop_rect(720, 1280, ops.RATIO_PORTRAIT) is None


def test_khung_cat_anh_doc_sang_video_ngang():
    rect = ops.crop_rect(768, 1376, ops.RATIO_LANDSCAPE)
    top, left, bottom, right = rect
    assert left is None and right == 1              # cắt trên dưới, giữ nguyên bề ngang
    assert abs((bottom - top) - (9 / 16) / (16 / 9)) < 1e-12
    assert abs((top + bottom) / 2 - 0.5) < 1e-12    # căn giữa


def test_khung_cat_tinh_theo_nhom_ti_le_khong_theo_pixel():
    """Ảnh Flow không bao giờ đúng tỉ lệ danh nghĩa — dùng pixel thật là lệch.

    1376/768 = 1.7917 chứ không phải 16/9 = 1.7778. Tính theo pixel ra 0.34302, trong
    khi giao diện gửi 0.341796875.
    """
    assert ops.nominal_ratio(1376, 768) == (16, 9)
    assert ops.nominal_ratio(768, 1376) == (9, 16)
    assert ops.nominal_ratio(1024, 1024) == (1, 1)
    assert ops.nominal_ratio(1200, 896) == (4, 3)
    assert ops.nominal_ratio(896, 1200) == (3, 4)
    theo_pixel = (1 - (9 / 16) / (1376 / 768)) / 2
    assert abs(theo_pixel - 0.34302325) < 1e-6      # con số SAI, ghi lại để đối chứng
    assert ops.crop_rect(1376, 768, ops.RATIO_PORTRAIT)[1] == 0.341796875


# ─── Đọc phản hồi ────────────────────────────────────────────

def test_media_id_o_chi_so_7():
    """Ô [6] LUÔN null; lấy nó làm cờ 'đang render' là mọi lượt poll đều báo chưa xong."""
    wf = [None] * 8
    wf[5] = [None] * 9
    wf[5][8] = [ops.STATUS_DONE]
    wf[6] = None
    wf[7] = [[None, 123], None, ["media-abc"]]
    assert ops.workflow_media_id(wf) == "media-abc"
    assert ops.workflow_done(wf) is True
    wf[5][8] = [ops.STATUS_RUNNING]
    assert ops.workflow_done(wf) is False


def test_id_ban_nang_cap_doan_truoc_duoc():
    assert ops.upsampled_media_id("abc") == "abc_upsampled"
    assert ops.upsampled_media_id("abc", four_k=True) == "abc_4k_upsampled"
