"""Bấm Dừng phải CẮT NGANG item đang chạy, không phải đợi nó xong.

Ca thật khiến phải viết mấy test này: đang tạo ảnh hàng loạt thì hết hạn mức Flow. Lỗi
quota bị `_ABUSE_RE` nhận nhầm thành "bị chặn vì bắn quá nhanh", nên mỗi ảnh ngủ 30–60
giây rồi thử lại, tới sáu lần. Bấm Dừng chỉ bật một cờ mà không đụng tới task đang ngủ,
nên người dùng bấm rồi ngồi nhìn nó chạy tiếp vài phút — và kết luận là nút hỏng.

Hai phép đo dưới đây khoá lại cả hai đầu: huỷ phải cắt được một item đang ngủ dài, và hết
hạn mức phải dừng cả job thay vì kiên nhẫn thử lại.
"""
import asyncio
import time

import pytest

from agent.studio import jobs as jobsmod


@pytest.fixture
def mgr():
    m = jobsmod.JobManager()
    m._persist = _noop            # không đụng CSDL trong test
    m._broadcast = _noop
    return m


async def _noop(*a, **kw):
    return None


def test_huy_cat_ngang_item_dang_ngu():
    """Item ngủ 30 giây; huỷ sau 0,2 giây phải dừng gần như tức thì.

    Trước bản vá, `_run_item` chỉ chờ worker nên phép thử này mất trọn 30 giây.
    """
    async def scenario():
        m = jobsmod.JobManager()
        m._persist = _noop
        m._broadcast = _noop
        job = jobsmod.Job("j1", "p1", "storyboard", total=3)
        m._jobs[job.id] = job

        async def worker(_item):
            await asyncio.sleep(30)          # nhịp lùi chống-chặn của đời thật

        t0 = time.monotonic()
        runner = asyncio.create_task(
            m._run(job, [1, 2, 3], worker, throttle=(0, 0), item_label=str))
        await asyncio.sleep(0.2)
        assert m.cancel(job.id) is True
        await asyncio.wait_for(runner, timeout=5)
        return time.monotonic() - t0, job

    elapsed, job = asyncio.run(scenario())
    assert elapsed < 3, f"huỷ mất {elapsed:.1f}s — không cắt được item đang ngủ"
    assert job.status == "cancelled"
    assert job.done == 0
    # Bị cắt ngang KHÔNG phải lỗi — đừng đếm vào errors rồi báo "1 lỗi" cho người dùng.
    assert job.errors == []


def test_huy_bao_ngay_dang_dung():
    """Người bấm nút cần thấy nút đã ăn; im lặng là thứ khiến người ta bấm đi bấm lại."""
    async def scenario():
        m = jobsmod.JobManager()
        m._persist = _noop
        m._broadcast = _noop
        job = jobsmod.Job("j2", "p1", "storyboard", total=1)
        m._jobs[job.id] = job
        m.cancel(job.id)
        return job

    job = asyncio.run(scenario())
    assert job.current == "Đang dừng…"


def test_het_han_muc_dung_ca_job_khong_thu_lai():
    """JobAbort dừng HẲN, và nói lý do — khác người dùng bấm huỷ.

    Không có nó thì job cứ thử lại từng ảnh một trong hàng giờ, và suốt thời gian đó nút
    Auto gen bị khoá vì job vẫn đang chạy.
    """
    async def scenario():
        m = jobsmod.JobManager()
        m._persist = _noop
        m._broadcast = _noop
        job = jobsmod.Job("j3", "p1", "storyboard", total=5)
        m._jobs[job.id] = job
        chay = []

        async def worker(item):
            chay.append(item)
            if item == 2:
                raise jobsmod.JobAbort("Hết hạn mức Flow (PUBLIC_ERROR_QUOTA_EXCEEDED)")

        await m._run(job, [1, 2, 3, 4, 5], worker, throttle=(0, 0), item_label=str)
        return job, chay

    job, chay = asyncio.run(scenario())
    assert chay == [1, 2], f"phải dừng ngay ở item 2, nhưng đã chạy {chay}"
    assert job.status == "error"
    assert "Hết hạn mức" in job.abort_reason
    assert "DỪNG" in job.message
    assert job.done == 1              # ảnh đã tạo được vẫn được tính, không mất


def test_loi_thuong_thi_van_chay_tiep():
    """Chỉ JobAbort mới dừng cả job. Một ảnh hỏng lẻ thì phải làm nốt các ảnh còn lại."""
    async def scenario():
        m = jobsmod.JobManager()
        m._persist = _noop
        m._broadcast = _noop
        job = jobsmod.Job("j4", "p1", "storyboard", total=3)
        m._jobs[job.id] = job

        async def worker(item):
            if item == 2:
                raise RuntimeError("một lượt hỏng lẻ")

        await m._run(job, [1, 2, 3], worker, throttle=(0, 0), item_label=str)
        return job

    job = asyncio.run(scenario())
    assert job.done == 2 and len(job.errors) == 1
    assert job.abort_reason == ""
    # Luật sẵn có: "error" chỉ khi KHÔNG item nào thành công. Hỏng lẻ mà vẫn ra ảnh thì
    # là "done" kèm số lỗi trong message — ghi lại để đừng ai "sửa" thành error rồi làm
    # banner đỏ lòm mỗi lần một ảnh bị lọc nội dung.
    assert job.status == "done"
    assert "1 lỗi" in job.message
