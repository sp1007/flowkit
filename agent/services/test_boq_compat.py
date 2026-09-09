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


# ─── ca HỎNG qua lớp tương thích ─────────────────────────────

class _FakeBoq:
    def __init__(self, wf):
        self._wf = wf

    async def poll(self, media_id):
        return False, self._wf


def _compat_with(wf):
    c = compat.BoqCompat.__new__(compat.BoqCompat)
    c.boq = _FakeBoq(wf)
    return c


def _wf(status):
    w = [None] * 8
    w[5] = [None] * 9
    w[5][8] = status
    return w


def test_hong_thi_studio_nhan_FAILED_kem_ly_do():
    """`videopoll._status_of` đọc mediaStatus.mediaGenerationStatus + failureReasons."""
    import asyncio
    c = _compat_with(_wf([4, [3, "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED"],
                          ["PROMINENT_PERSON"]]))
    res = asyncio.run(compat.BoqCompat.check_video_status(
        c, [{"name": "m", "projectId": "p"}]))
    st = res["data"]["media"][0]["mediaMetadata"]["mediaStatus"]
    assert st["mediaGenerationStatus"] == "MEDIA_GENERATION_STATUS_FAILED"
    assert st["failureReasons"] == ["PROMINENT_PERSON"]


def test_ma_la_bao_PENDING_chu_khong_bao_hong():
    """Báo hỏng oan thì studio tạo lại một bản nữa — tính tiền hai lần."""
    import asyncio
    for st_raw in ([1], [2], [6], [99]):
        c = _compat_with(_wf(st_raw))
        res = asyncio.run(compat.BoqCompat.check_video_status(
            c, [{"name": "m", "projectId": "p"}]))
        st = res["data"]["media"][0]["mediaMetadata"]["mediaStatus"]
        assert st["mediaGenerationStatus"] == "MEDIA_GENERATION_STATUS_PENDING", st_raw
        assert "failureReasons" not in st


# ─── tự dò hạng tài khoản ────────────────────────────────────

class _FakeCredits:
    """Giả BoqClient chỉ để kiểm việc đọc hạng từ phản hồi nzlxg."""

    def __init__(self, data, tier_env):
        self._data = data
        self.tier = tier_env

    async def credits_and_tier(self):
        from agent.services import boq_prices as pr
        t = self._data[2] if len(self._data) > 2 else None
        if t not in (pr.TIER_FREE, pr.TIER_PRO, pr.TIER_ULTRA):
            t = None
        return self._data[0], t


def _credits_with(data, tier_env=3):
    c = compat.BoqCompat.__new__(compat.BoqCompat)
    c.boq = _FakeCredits(data, tier_env)
    return c


def test_doc_hang_tu_chinh_phan_hoi_nzlxg():
    """Hai mẫu THẬT, đo trên hai tài khoản khác nhau.

        Pro    [1050,  1, 2, 2, null, 1050]
        Ultra  [12331, 2, 3, 3, null, 12331]

    Ô [1] là số của nhãn PAYGATE_TIER_ONE/TWO, ô [2] là số của bảng giá mới — đúng chỗ
    lệch một bậc giữa hai cách đếm, nay có bằng chứng chứ không còn suy diễn.
    """
    import asyncio
    r = asyncio.run(compat.BoqCompat.get_credits(
        _credits_with([1050, 1, 2, 2, None, 1050], tier_env=3)))
    assert r["data"]["credits"] == 1050
    assert r["data"]["userPaygateTier"] == "PAYGATE_TIER_ONE"     # Pro

    r = asyncio.run(compat.BoqCompat.get_credits(
        _credits_with([12331, 2, 3, 3, None, 12331], tier_env=2)))
    assert r["data"]["userPaygateTier"] == "PAYGATE_TIER_TWO"     # Ultra


def test_flow_khai_gi_thi_tin_cai_do_khong_tin_bien_moi_truong():
    """Đặt nhầm FLOWKIT_FLOW_TIER là hỏng lặng lẽ — nên số của Flow phải thắng."""
    import asyncio
    c = _credits_with([1050, 1, 2, 2, None, 1050], tier_env=3)   # env khai Ultra, thật là Pro
    r = asyncio.run(compat.BoqCompat.get_credits(c))
    assert r["data"]["userPaygateTier"] == "PAYGATE_TIER_ONE"
    assert c.boq.tier == 2                                       # sửa lại luôn cho lần sau


def test_khong_doc_duoc_thi_roi_ve_bien_moi_truong():
    import asyncio
    c = _credits_with([500, None, 99, None, None, 500], tier_env=2)
    r = asyncio.run(compat.BoqCompat.get_credits(c))
    assert r["data"]["userPaygateTier"] == "PAYGATE_TIER_ONE"     # tier_env=2 → Pro


def test_khong_con_co_bat_tat():
    """Đường batchexecute là đường DUY NHẤT — không còn `enabled()` để ai đó tắt nhầm."""
    assert not hasattr(compat, "enabled")


# ─── thông điệp lỗi phải đọc được ────────────────────────────

def test_loi_flow_hien_thanh_dong_doc_duoc():
    """`[8,null,[["type.googleapis.` là thứ hiện lên side panel trước bản vá này —
    cắt cụt đúng chỗ vô nghĩa nhất, giấu mất phần duy nhất nói lên chuyện gì xảy ra.
    """
    from agent.services.boq_client import BoqError, error_reasons
    e = BoqError("ogiZ0b", [8, None, [["type.googleapis.com/google.rpc.ErrorInfo",
                                       ["PUBLIC_ERROR_QUOTA_EXCEEDED"]]]])
    assert str(e) == "ogiZ0b: RESOURCE_EXHAUSTED — PUBLIC_ERROR_QUOTA_EXCEEDED"
    assert e.code == 8
    assert e.reasons == ["PUBLIC_ERROR_QUOTA_EXCEEDED"]
    # Tên kiểu không phải lý do.
    assert error_reasons([7, None, [["type.googleapis.com/google.rpc.ErrorInfo",
                                     ["PUBLIC_ERROR_MODEL_ACCESS_DENIED"]]]]) \
        == ["PUBLIC_ERROR_MODEL_ACCESS_DENIED"]


def test_loi_khong_kem_ly_do_van_doc_duoc():
    from agent.services.boq_client import BoqError, RPC_CODE
    assert str(BoqError("SPrCad", [3])) == "SPrCad: INVALID_ARGUMENT"
    assert RPC_CODE.get(99) is None
    assert str(BoqError("x", [99, None, []])) == "x: CODE_99"
