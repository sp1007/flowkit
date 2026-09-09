"""Hết hạn mức phải dừng ở CẢ đường đồ thị, không chỉ đường dựng prompt trực tiếp.

Lần vá đầu tôi chỉ sửa hai vòng thử lại bên `api/studio.py` rồi tưởng xong. Nhưng cả
`_generate_frame_image` lẫn `_generate_entity_image` đều gọi `_gen_via_graph` TRƯỚC, và
người dùng xác nhận storyboard cũng đi qua đồ thị (chạy node sinh nối vào Output). Nghĩa
là với mọi shot/asset có đồ thị — tức phần lớn dự án thật — lượt sinh đi qua `graph.py`
và bản vá kia không chạm tới.

Bốn vòng thử lại gọi Flow nằm ở hai module. Test này khoá hai vòng bên `graph.py`;
`test_jobs_cancel.py` khoá hành vi dừng job ở tầng trên.
"""
import asyncio

import pytest

from agent.studio import graph as graph_mod
from agent.studio import jobs as jobsmod


_QUOTA = {"status": 502,
          "error": "ogiZ0b: RESOURCE_EXHAUSTED — PUBLIC_ERROR_QUOTA_EXCEEDED"}


async def _noop_sleep(_s):
    return None


def test_sinh_anh_qua_do_thi_dung_ngay_khi_het_han_muc():
    """Phải ném JobAbort ở lượt ĐẦU, không thử lại — mỗi lần thử là một lần chờ vô ích."""
    goi = []

    async def call():
        goi.append(1)
        return dict(_QUOTA)

    async def scenario():
        with pytest.raises(jobsmod.JobAbort) as ex:
            await graph_mod._img_gen_retry(call, "p1")
        return ex.value

    err = asyncio.run(scenario())
    assert len(goi) == 1, f"đã gọi Flow {len(goi)} lần — vẫn còn thử lại"
    assert "Hết hạn mức" in str(err)


def test_sinh_video_qua_do_thi_dung_ngay():
    goi = []

    async def submit():
        goi.append(1)
        return dict(_QUOTA)

    async def scenario():
        with pytest.raises(jobsmod.JobAbort):
            await graph_mod._vid_gen_retry(submit, "shot-1", "p1", "shot", "flow-p1")

    asyncio.run(scenario())
    assert len(goi) == 1, f"đã gọi Flow {len(goi)} lần — vẫn còn thử lại"


def test_loi_thuong_van_thu_lai_du_so_lan(monkeypatch):
    """Chỉ quota mới dừng ngay. Lỗi tạm thời vẫn phải thử lại như cũ."""
    # Bỏ qua nhịp chờ 2–5s giữa các lần thử: ở đây đo SỐ LẦN, không đo thời gian.
    monkeypatch.setattr(graph_mod.asyncio, "sleep", _noop_sleep)
    goi = []

    async def call():
        goi.append(1)
        return {"status": 502, "error": "ogiZ0b: INTERNAL"}

    async def scenario():
        with pytest.raises(graph_mod.GraphError):
            await graph_mod._img_gen_retry(call, "p1")

    asyncio.run(scenario())
    assert len(goi) == graph_mod._GRAPH_IMG_RETRIES, \
        f"chỉ thử {len(goi)} lần, đáng ra {graph_mod._GRAPH_IMG_RETRIES}"


def test_nhan_dien_quota_dung_chung_mot_ham():
    """Hai bản nhận diện riêng ở hai module thì sớm muộn cũng lệch nhau."""
    from agent.api import studio as api_studio
    from agent.services.boq_client import is_quota_error
    assert api_studio._is_quota_exhausted is is_quota_error
    assert graph_mod.is_quota_error is is_quota_error
