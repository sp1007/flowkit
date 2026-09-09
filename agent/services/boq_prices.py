"""Giá credit của một lượt sinh media trên Flow.

Nguồn sự thật là `agent/boq_model_catalog.json` — bảng 104 khoá kèm giá từng tier, lấy
nguyên từ rpcid `HTrJv` của chính giao diện Flow. Bảng ấy đã được đối chiếu với số dư
thật sáu lần liên tiếp (5, 20, 10, 50, 15, 4 credit) nên tra thẳng là đủ.

Ngoài việc tra bảng, module này còn SUY được giá cho khoá chưa từng thấy. Đó không phải
màu mè: Google thêm/đổi khoá bất cứ lúc nào, và một khoá lạ mà trả về "không biết giá"
sẽ làm khâu gọi phải chọn giữa chặn oan hoặc bắn mù. Hai luật dưới đây tái tạo ĐÚNG cả
104 dòng của bảng (xem test_boq_prices.py), nên dùng chúng cho khoá lạ là có cơ sở:

  Veo   — giá chỉ phụ thuộc NHÓM CHẤT LƯỢNG, không phụ thuộc độ dài hay kiểu đầu vào.
          Cả 11 khoá Lite đều 5 trên tier 3, cả 26 khoá Fast đều 10, cả 14 khoá
          Quality đều 100.
  Omni  — giá phụ thuộc ĐỘ DÀI và ĐỘ PHÂN GIẢI, không phụ thuộc kiểu đầu vào
          (t2v/i2v/r2v/first_last giống hệt nhau) và không phụ thuộc tier.

Hai cái bẫy, cả hai đều ngược với trực giác "Ultra thì rẻ hơn":
  * Ultra giảm nửa giá cho Lite và Fast nhưng KHÔNG giảm cho Quality — 100 vẫn là 100.
  * Omni không giảm đồng nào theo tier.
Viết một hệ số giảm giá chung cho Ultra là sai ở 48 trên 104 khoá.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

_CATALOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "boq_model_catalog.json")

# Hạng tài khoản theo cách đánh số của bảng giá mới.
#
# ĐỪNG lẫn với `PAYGATE_TIER_*` của API cũ: nhãn cũ chỉ đếm hạng TRẢ TIỀN nên lệch đúng
# một bậc — API cũ gọi Ultra là `PAYGATE_TIER_TWO` và Pro là `PAYGATE_TIER_ONE`.
#
# Ultra = 3: bảng chỉ cho hạng 3 mua 4K và người dùng xác nhận chỉ Ultra tải được 4K;
# các khoá *_low_priority chỉ có giá ở hạng 3 và tài khoản Ultra dùng được.
#
# Pro = 2, KHÔNG phải 1. Suýt nhầm chỗ này: `veo_3_1_upsampler_1080p` chỉ có giá ở hạng
# 2 và 3, nên nếu coi Pro là hạng 1 thì hàng rào giá sẽ chặn oan — trong khi đã đo thật
# trên tài khoản Pro rằng upscale 1080p chạy và KHÔNG trừ credit (914 → 914). Hạng 1 là
# bản miễn phí.
TIER_FREE = 1
TIER_PRO = 2
TIER_ULTRA = 3

# Nhãn của API cũ → cách đánh số của bảng mới.
PAYGATE_TO_TIER = {
    "PAYGATE_TIER_ONE": TIER_PRO,
    "PAYGATE_TIER_TWO": TIER_ULTRA,
}


TIER_TO_PAYGATE = {TIER_PRO: "PAYGATE_TIER_ONE", TIER_ULTRA: "PAYGATE_TIER_TWO"}


def paygate_from_tier(tier: int) -> str:
    """Số hạng của bảng mới → nhãn `PAYGATE_TIER_*` mà tầng studio đang dùng.

    Studio quyết định độ phân giải upscale và chọn model theo nhãn này, nên thiếu nó là
    âm thầm hạ 4K xuống 2K trên tài khoản Ultra — hỏng lặng lẽ, không lỗi nào báo.
    Hạng miễn phí không có nhãn tương ứng nên xếp chung với Pro, là mức thấp hơn.
    """
    return TIER_TO_PAYGATE.get(tier, "PAYGATE_TIER_ONE")


def tier_from_paygate(paygate: Optional[str], default: int = TIER_PRO) -> int:
    """Đổi nhãn `PAYGATE_TIER_*` của API cũ sang số hạng của bảng giá mới.

    Mặc định rơi về Pro chứ không phải Ultra: đoán cao hơn thực tế thì hàng rào giá thả
    cho một lượt gọi chắc chắn hỏng đi qua, và Flow chỉ trả `error [3]` trống rỗng.
    """
    return PAYGATE_TO_TIER.get(paygate or "", default)

_catalog_cache: Optional[dict] = None
_catalog_mtime: float = 0.0


def catalog() -> dict:
    """Bảng giá, nạp lại khi file đổi.

    Đọc theo mtime chứ không nạp một lần rồi thôi: bảng này là dữ liệu khảo sát, còn
    được cập nhật khi bắt thêm khoá mới, và bắt phải khởi động lại agent chỉ để thấy một
    dòng mới là cái giá không đáng trả.
    """
    global _catalog_cache, _catalog_mtime
    try:
        mtime = os.path.getmtime(_CATALOG_PATH)
    except OSError:
        return _catalog_cache or {}
    if _catalog_cache is None or mtime != _catalog_mtime:
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            _catalog_cache = json.load(fh)
        _catalog_mtime = mtime
    return _catalog_cache


# ─── Suy giá cho khoá chưa có trong bảng ─────────────────────

_OMNI_720P = {"4s": 7, "6s": 10, "8s": 12, "10s": 15}
_OMNI_360P = {"4s": 4, "6s": 5, "8s": 6, "10s": 7}
_OMNI_EDIT = {"720p": 20, "360p": 10}

_DURATION_RE = re.compile(r"_(\d+)s(?:_|$)")


def _is_omni(model_key: str) -> bool:
    # `omni_` chứ không phải `omni_flash`: bộ nâng cấp tên là `omni_upsampler_360p`,
    # không mang chữ flash.
    return model_key.startswith("abra_") or model_key.startswith("omni_")


def _derive_omni(model_key: str) -> Optional[dict]:
    is_360 = model_key.endswith("_360p") or "_360p" in model_key
    if "edit" in model_key:
        cost = _OMNI_EDIT["360p" if is_360 else "720p"]
        return {"1": cost, "2": cost, "3": cost}
    m = _DURATION_RE.search(model_key)
    if not m:
        return None
    dur = f"{m.group(1)}s"
    table = _OMNI_360P if is_360 else _OMNI_720P
    if dur not in table:
        return None
    cost = table[dur]
    return {"1": cost, "2": cost, "3": cost}


def _derive_veo(model_key: str) -> Optional[dict]:
    """Giá + HẠNG DÙNG ĐƯỢC của một khoá Veo, cả hai suy từ tên.

    Giá và quyền dùng là hai câu hỏi khác nhau và phải trả lời riêng. Bản đầu của hàm
    này chỉ suy giá rồi phát cho cả ba tier, và test đối chiếu với bảng thật lôi ra 30
    dòng sai — `veo_3_1_t2v_lite_4s` chẳng hạn, bảng ghi chỉ tier 3 mua được, còn hàm
    thì bảo Pro cũng mua được với giá 10. Sai kiểu ấy không lộ ra lúc chạy trên Ultra;
    nó lộ ra ở tài khoản Pro dưới dạng error [3] khó truy.

    Quy luật hạng, đọc từ chính tên khoá:
      * có độ dài (`_4s`, `_6s`…) → CHỈ tier 3
      * `_ultra` / `_low_priority` → chỉ tier 3
      * Fast trơn → chỉ tier 1 và 2; tier 3 dùng bản `_ultra` sinh đôi với nó
      * Lite và Quality trơn → cả ba hạng
    """
    if "upsampler_4k" in model_key:
        return {"3": 50}
    if "upsampler" in model_key:          # 1080p — Pro không có
        return {"2": 0, "3": 0}

    has_duration = _DURATION_RE.search(model_key + "_") is not None
    ultra_only = has_duration or "_ultra" in model_key or "_low_priority" in model_key

    # Thứ tự KHỚP quan trọng: mọi khoá low_priority đều thuộc họ Lite, xét trước.
    if "_low_priority" in model_key:
        return {"3": 0}
    if "_lite" in model_key:
        return {"3": 5} if ultra_only else {"1": 10, "2": 10, "3": 5}
    if "_fast" in model_key:
        # Fast trơn KHÔNG có giá tier 3 — Ultra phải gọi bản `_ultra`.
        return {"3": 10} if ultra_only else {"1": 20, "2": 20}
    # Quality — không được giảm ở Ultra, 100 là 100 ở mọi hạng.
    return {"3": 100} if ultra_only else {"1": 100, "2": 100, "3": 100}


def derive_tiers(model_key: str) -> Optional[dict]:
    """Giá theo tier suy từ TÊN khoá, cho khoá chưa có trong bảng. None = chịu.

    Chỉ suy được cho model VIDEO. Các khoá ảnh (`GEM_PIX_2`, `NARWHAL`,
    `HARBOR_SEAL`, `GEM_PIX_2_UPSAMPLE_*`) là tên mã không theo quy luật nào — chúng
    phải nằm sẵn trong bảng, và may là mọi thao tác ảnh đều 0 credit nên đoán sai cũng
    không ai mất tiền.
    """
    if _is_omni(model_key):
        if "upsampler" in model_key:
            return {"2": 0, "3": 0}
        return _derive_omni(model_key)
    if model_key.startswith("veo_"):
        return _derive_veo(model_key)
    return None


# ─── Tra giá ─────────────────────────────────────────────────

def tiers(model_key: str) -> Optional[dict]:
    """Bảng giá theo tier của một khoá; None nếu vừa không có trong bảng vừa không suy được."""
    entry = catalog().get(model_key)
    if entry:
        return entry.get("gia_theo_tier")
    return derive_tiers(model_key)


def price(model_key: str, tier: int = TIER_ULTRA) -> Optional[int]:
    """Giá credit cho một lượt, hoặc None nếu tier đó KHÔNG dùng được khoá này.

    None và 0 là hai chuyện khác hẳn nhau, đừng gộp: 0 nghĩa là dùng được và miễn phí
    (`veo_3_1_t2v_lite_low_priority` trên Ultra), còn None nghĩa là hạng tài khoản này
    không được phép gọi (`veo_3_1_upsampler_4k` trên Pro). Gộp lại thì lượt gọi chắc
    chắn hỏng sẽ đi qua mọi hàng rào vì trông như "miễn phí".
    """
    tbl = tiers(model_key)
    if not tbl:
        return None
    v = tbl.get(str(tier))
    return int(v) if v is not None else None


def available(model_key: str, tier: int = TIER_ULTRA) -> bool:
    return price(model_key, tier) is not None


def is_known(model_key: str) -> bool:
    """Khoá có mặt trong bảng thật hay chỉ đang được suy từ tên."""
    return model_key in catalog()


def group(model_key: str) -> Optional[str]:
    entry = catalog().get(model_key)
    return entry.get("nhom") if entry else None


def cheapest(model_keys, tier: int = TIER_ULTRA) -> Optional[str]:
    """Khoá rẻ nhất trong số dùng được ở tier này; None nếu không khoá nào dùng được."""
    usable = [(price(k, tier), k) for k in model_keys if available(k, tier)]
    if not usable:
        return None
    return min(usable)[1]
