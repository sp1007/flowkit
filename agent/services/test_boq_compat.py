"""Kiểm lớp tương thích: hình dạng trả về và cách dựng structured prompt.

Hình dạng là thứ studio đọc ở hàng chục chỗ, nên sai một trường là hỏng ở nơi khác hẳn
chỗ gây ra. Test ở đây khoá đúng những trường studio thật sự đọc, và mỗi test ghi rõ ai
đọc trường đó — để ai sửa sau biết mình đang phá cái gì.
"""
from agent.services import boq_compat as compat
from agent.services import boq_ops as ops

P = compat.BoqCompat._parts


# ─── đổi tên hằng ────────────────────────────────────────────

def test_bang_ti_le_anh_va_video_la_hai_bang_khac_nhau():
    """Dùng nhầm bảng là ra khung sai mà không có lỗi nào báo."""
    assert compat.image_ratio("IMAGE_ASPECT_RATIO_PORTRAIT") == 2
    assert compat.image_ratio("IMAGE_ASPECT_RATIO_LANDSCAPE") == 3
    assert compat.image_ratio("IMAGE_ASPECT_RATIO_SQUARE") == 1
    assert compat.video_ratio("VIDEO_ASPECT_RATIO_PORTRAIT") == ops.RATIO_PORTRAIT == 1
    assert compat.video_ratio("VIDEO_ASPECT_RATIO_LANDSCAPE") == ops.RATIO_LANDSCAPE == 2
    # Cùng chữ "PORTRAIT" nhưng ra hai số khác nhau — đó chính là chỗ dễ lẫn.
    assert compat.image_ratio("IMAGE_ASPECT_RATIO_PORTRAIT") != \
        compat.video_ratio("VIDEO_ASPECT_RATIO_PORTRAIT")


def test_ti_le_la_khong_roi_ve_gia_tri_ngoai_dai():
    """0 và 6 khiến Flow lặng lẽ trả 1408x768 — phải rơi về giá trị hợp lệ."""
    assert compat.image_ratio("gì đó lạ") in ops.IMAGE_RATIO_WH
    assert compat.image_ratio(None) in ops.IMAGE_RATIO_WH
    assert compat.video_ratio(None) in (ops.RATIO_PORTRAIT, ops.RATIO_LANDSCAPE)


def test_hang_tai_khoan_lech_mot_bac():
    assert compat.tier_of("PAYGATE_TIER_ONE") == 2      # Pro
    assert compat.tier_of("PAYGATE_TIER_TWO") == 3      # Ultra


# ─── hình dạng ───────────────────────────────────────────────

def test_hinh_dang_anh_du_truong_studio_doc():
    """`graph._generated_media_id` đọc media[].image.generatedImage.mediaId."""
    out = compat._image_payload("mid-1", "một con mèo", "https://x/y", [1376, 768])
    m = out["media"][0]
    assert m["image"]["generatedImage"]["mediaId"] == "mid-1"
    assert m["name"] == "mid-1"
    assert m["fifeUrl"] == "https://x/y"
    # media_store._URL_KEYS tìm cả servingBaseUri.
    assert m["servingBaseUri"] == "https://x/y"


def test_hinh_dang_submit_video_du_truong():
    """`studio._extract_video_submit` đọc media[0].name và workflows[0].name."""
    out = compat._video_submit_payload(
        {"media_id": "m", "workflow_id": "w", "credits_left": 5})
    assert out["media"][0]["name"] == "m"
    assert out["workflows"][0]["name"] == "w"
    assert out["workflows"][0]["metadata"]["primaryMediaId"] == "m"


def test_vo_ngoai_khong_co_error():
    """studio coi `res.get("error")` là hỏng — vỏ thành công phải KHÔNG có trường đó."""
    r = compat._ok({"media": []})
    assert r.get("error") is None
    assert r["status"] == 200
    assert r.get("data", r)["media"] == []


# ─── structured prompt ───────────────────────────────────────

def test_khong_co_anh_thi_khong_dung_structured_prompt():
    assert P("một con mèo", None, None) == []
    assert P("một con mèo {ai đó}", None, None) == []


def test_anh_duoc_neo_dung_cho_trong_cau():
    parts = P("chiếc {long} bay trên đầu {meo} rồi hạ xuống",
              [{"handle": "long", "media_id": "A"},
               {"handle": "meo", "media_id": "B"}], None)
    assert parts == [
        {"text": "chiếc "},
        {"media_id": "A", "name": "long"},
        {"text": " bay trên đầu "},
        {"media_id": "B", "name": "meo"},
        {"text": " rồi hạ xuống"},
    ]


def test_nhac_lai_cung_mot_anh_chi_bind_mot_lan():
    """Nhiều mảnh trỏ cùng một mediaId thì Flow trả lỗi — lần nhắc sau hạ xuống chữ."""
    parts = P("{meo} nhìn quanh rồi {meo} chạy đi",
              [{"handle": "meo", "media_id": "A"}], None)
    binds = [p for p in parts if "media_id" in p]
    assert len(binds) == 1
    assert "meo chạy đi" in "".join(p.get("text", "") for p in parts)


def test_manh_chu_duoc_gop_khong_de_vun():
    """Mảnh chữ vụn ra cũng làm Flow trả lỗi, y như reference part trùng."""
    parts = P("{a} {b} xong", [{"handle": "a", "media_id": "A"}], None)
    texts = [i for i, p in enumerate(parts) if "text" in p]
    # Không có hai mảnh chữ nào đứng liền nhau.
    assert all(b - a > 1 for a, b in zip(texts, texts[1:]))


def test_anh_khong_duoc_goi_ten_van_duoc_noi_vao():
    """Reference không xuất hiện trong câu gần như bị model bỏ qua — phải nối vào cuối."""
    parts = P("một cảnh phố", None, ["A", "B"])
    ids = [p["media_id"] for p in parts if "media_id" in p]
    assert ids == ["A", "B"]


def test_khong_bind_trung_khi_vua_co_handle_vua_co_id_roi():
    parts = P("{meo} đi dạo", [{"handle": "meo", "media_id": "A"}], ["A", "B"])
    ids = [p["media_id"] for p in parts if "media_id" in p]
    assert ids == ["A", "B"]        # A chỉ một lần, B nối thêm


# ─── chọn khoá model ─────────────────────────────────────────

def test_khoa_veo_lite_chon_theo_anh_truyen_vao():
    """Chọn sai khoá là đổi hoá đơn — luật là theo ẢNH, không theo cờ."""
    c = compat.BoqCompat.__new__(compat.BoqCompat)
    assert "interpolation" in c._veo_lite_key("a", "b", None, 8)
    assert c._veo_lite_key("a", None, None, 8).endswith("i2v_lite_low_priority")
    assert "r2v" in c._veo_lite_key(None, None, ["x"], 8)
    assert "t2v" in c._veo_lite_key(None, None, None, 8)


def test_hai_khung_4s_6s_dung_khoa_fl():
    """_fl = first-last. Khoá một khung KHÔNG có _fl — nhầm là đổi hẳn thao tác."""
    c = compat.BoqCompat.__new__(compat.BoqCompat)
    assert "_fl_" in c._veo_lite_key("a", "b", None, 4)
    assert "_fl_" in c._veo_lite_key("a", "b", None, 6)
    assert "_fl_" not in c._veo_lite_key("a", None, None, 4)
