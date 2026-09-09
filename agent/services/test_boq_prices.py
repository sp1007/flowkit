"""Kiểm bảng giá.

Phép thử đáng giá nhất ở đây là `test_luat_tai_tao_dung_ca_bang`: nó bắt hai luật suy
giá phải dựng lại ĐÚNG từng dòng của bảng 104 khoá. Đó là thứ cho phép tin tưởng dùng
luật cho khoá Google mới thêm — nếu luật chỉ đúng "gần hết" thì nó là phỏng đoán, và
phỏng đoán về tiền thì không nên đưa vào đường chạy.
"""
from agent.services import boq_prices as p


def _la_model_video(key: str) -> bool:
    return key.startswith(("veo_", "abra_", "omni_"))


def test_luat_tai_tao_dung_ca_bang():
    """Luật phải dựng lại đúng TỪNG DÒNG video, cả giá lẫn hạng dùng được.

    Không chấp nhận "gần đúng": luật này là thứ trả lời cho khoá Google mới thêm, và
    một luật sai vài dòng thì hoặc chặn oan một khoá dùng được, hoặc thả cho một lượt
    gọi chắc chắn hỏng đi qua. Bản đầu sai 30 dòng vì chỉ suy giá mà không suy hạng.
    """
    cat = p.catalog()
    assert len(cat) >= 100, f"bảng chỉ có {len(cat)} khoá, nghi bị cắt"
    lech = []
    for key, entry in cat.items():
        if not _la_model_video(key):
            continue
        that = entry["gia_theo_tier"]
        suy = p.derive_tiers(key)
        if suy != that:
            lech.append((key, that, suy))
    assert not lech, "luật suy giá lệch với bảng:\n" + "\n".join(
        f"  {k}: bảng={t} suy={s}" for k, t, s in lech)


def test_moi_khoa_trong_bang_deu_tra_duoc_gia():
    """Kể cả khoá ảnh, vốn không suy được từ tên — chúng phải tra ra từ bảng."""
    for key, entry in p.catalog().items():
        assert p.tiers(key) == entry["gia_theo_tier"], key


def test_khoa_anh_khong_suy_tu_ten():
    # Tên mã, không theo quy luật nào. Ghi lại để ai đó đừng đi bịa luật cho chúng.
    for key in ("GEM_PIX_2", "NARWHAL", "HARBOR_SEAL", "GEM_PIX_2_UPSAMPLE_4K"):
        assert p.derive_tiers(key) is None
        assert p.price(key, 3) == 0        # nhưng tra bảng thì ra, và đều 0 credit


def test_ba_moc_nguoi_dung_doc_tren_prompt_box():
    # Người dùng đọc thẳng trong giao diện Flow, tài khoản Ultra.
    assert p.price("veo_3_1_i2v_lite", 3) == 5
    assert p.price("veo_3_1_i2v_s_fast_ultra", 3) == 10
    assert p.price("veo_3_1_i2v_s", 3) == 100


def test_sau_lan_do_bang_so_du_that():
    # Mỗi dòng là một lượt gọi thật, đo bằng hiệu số dư trước/sau.
    assert p.price("veo_3_1_extension_lite", 3) == 5        # 12439 -> 12434
    assert p.price("abra_edit", 3) == 20                    # 12434 -> 12414
    assert p.price("abra_edit_360p", 3) == 10               # 12414 -> 12404
    assert p.price("veo_3_1_upsampler_4k", 3) == 50         # 12404 -> 12354
    assert p.price("omni_flash_i2v_10s_first_last", 3) == 15  # 12354 -> 12339
    assert p.price("omni_flash_i2v_4s_first_last_360p", 3) == 4  # 12339 -> 12335


def test_khong_ton_tien():
    for key in ("veo_3_1_t2v_lite_low_priority", "veo_3_1_r2v_lite_low_priority",
                "veo_3_1_i2v_s_lite_4s_low_priority",
                "veo_3_1_interpolation_lite_low_priority",
                "veo_3_1_extension_lite_low_priority",
                "veo_3_1_upsampler_1080p"):
        assert p.price(key, 3) == 0, key


def test_ultra_giam_nua_gia_tru_quality():
    assert (p.price("veo_3_1_i2v_lite", 1), p.price("veo_3_1_i2v_lite", 3)) == (10, 5)
    assert (p.price("veo_3_1_r2v_fast_landscape", 1),
            p.price("veo_3_1_r2v_fast_landscape_ultra", 3)) == (20, 10)
    # Quality KHÔNG được giảm — chỗ dễ viết sai nhất.
    assert p.price("veo_3_1_i2v_s", 1) == p.price("veo_3_1_i2v_s", 3) == 100
    # Omni không giảm theo tier.
    for t in (1, 2, 3):
        assert p.price("abra_t2v_8s", t) == 12


def test_veo_khong_tinh_tien_theo_do_dai():
    # Clip Veo Lite 4 giây và 8 giây cùng giá — cắt ngắn không tiết kiệm gì.
    assert p.price("veo_3_1_i2v_s_lite_4s", 3) == p.price("veo_3_1_i2v_lite", 3) == 5


def test_omni_tinh_tien_theo_do_dai():
    assert [p.price(f"abra_t2v_{d}s", 3) for d in (4, 6, 8, 10)] == [7, 10, 12, 15]
    assert [p.price(f"abra_t2v_{d}s_360p", 3) for d in (4, 6, 8, 10)] == [4, 5, 6, 7]


def test_none_khac_khong():
    # 4K chỉ Ultra. Pro phải nhận None (không được phép), không phải 0 (miễn phí) —
    # gộp hai cái này thì lượt gọi chắc chắn hỏng lại lọt qua mọi hàng rào.
    assert p.price("veo_3_1_upsampler_4k", 1) is None
    assert p.price("veo_3_1_upsampler_4k", 3) == 50
    assert p.available("veo_3_1_upsampler_4k", 1) is False
    assert p.price("veo_3_1_t2v_lite_low_priority", 3) == 0
    assert p.available("veo_3_1_t2v_lite_low_priority", 3) is True


def test_khoa_la_van_suy_duoc():
    # Khoá bịa theo đúng cách đặt tên của Google — mai họ thêm thật thì vẫn có giá.
    assert not p.is_known("veo_3_1_i2v_s_lite_12s_low_priority")
    assert p.price("veo_3_1_i2v_s_lite_12s_low_priority", 3) == 0
    assert p.price("abra_t2v_6s_360p_lol", 3) == 5
    assert p.price("mot_khoa_khong_phai_cua_flow", 3) is None


def test_cheapest():
    assert p.cheapest(["veo_3_1_extension_lite",
                       "veo_3_1_extension_lite_low_priority"], 3) \
        == "veo_3_1_extension_lite_low_priority"
    # Trên Pro thì bản low_priority không dùng được, phải rơi về bản trả tiền.
    assert p.cheapest(["veo_3_1_extension_lite",
                       "veo_3_1_extension_lite_low_priority"], 1) \
        == "veo_3_1_extension_lite"
    assert p.cheapest(["veo_3_1_upsampler_4k"], 1) is None


def test_anh_xa_hang_lech_mot_bac_so_voi_paygate():
    """Nhãn PAYGATE_TIER_* của API cũ chỉ đếm hạng TRẢ TIỀN nên lệch một bậc.

    Bằng chứng: API cũ gọi Ultra là PAYGATE_TIER_TWO (CLAUDE.md bản chính), còn bảng giá
    mới cho Ultra là 3. Và veo_3_1_upsampler_1080p chỉ có giá ở hạng 2 và 3, trong khi
    đã đo thật trên tài khoản Pro rằng upscale 1080p chạy và không trừ credit — nên Pro
    phải là 2, không phải 1.
    """
    assert p.tier_from_paygate("PAYGATE_TIER_ONE") == p.TIER_PRO == 2
    assert p.tier_from_paygate("PAYGATE_TIER_TWO") == p.TIER_ULTRA == 3
    assert p.tier_from_paygate(None) == p.TIER_PRO       # đoán thấp, không đoán cao
    assert p.tier_from_paygate("gì đó lạ") == p.TIER_PRO


def test_pro_upscale_1080p_duoc_4k_thi_khong():
    assert p.price("veo_3_1_upsampler_1080p", p.TIER_PRO) == 0
    assert p.price("veo_3_1_upsampler_4k", p.TIER_PRO) is None
    assert p.price("veo_3_1_upsampler_4k", p.TIER_ULTRA) == 50
    # Hạng miễn phí thì không có cả hai.
    assert p.price("veo_3_1_upsampler_1080p", p.TIER_FREE) is None


def test_pro_khong_co_ban_0_dong():
    """Mọi khoá *_low_priority chỉ có ở Ultra — Pro phải rơi về bản trả tiền."""
    for key in ("veo_3_1_t2v_lite_low_priority", "veo_3_1_r2v_lite_low_priority",
                "veo_3_1_extension_lite_low_priority"):
        assert p.price(key, p.TIER_PRO) is None
    assert p.cheapest(["veo_3_1_extension_lite_low_priority",
                       "veo_3_1_extension_lite"], p.TIER_PRO) == "veo_3_1_extension_lite"
    assert p.price("veo_3_1_extension_lite", p.TIER_PRO) == 10
