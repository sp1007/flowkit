"""Lô ảnh phải chạy SONG SONG, không nối đuôi nhau.

Ca thật: người dùng đổi sang Nano Banana Lite và tạo được ảnh, nhưng ảnh sau chỉ khởi
tạo khi ảnh trước xong hẳn. Tầng job vốn đã bắn theo lô 4 (IMAGE_BATCH_SIZE), nhưng
`boq_request` đi qua một asyncio.Lock nên cả 4 bị xếp hàng lại. Với dự án vài trăm frame
đó là chênh lệch hàng giờ.

Song song được vì hai lẽ đo được: giao thức WS ghép kênh theo `req_id`, và chính giao
diện Flow bắn 4 lời gọi ogiZ0b đồng thời cho một lô 4 — bắt tận tay trong nhật ký recon.
"""
import asyncio
import time

from agent.services.flow_client import FlowClient


class _FakeWS:
    """WS giả: nhận lệnh, trả lời sau `delay` giây — mô phỏng độ trễ của Flow."""

    def __init__(self, client, delay=0.3):
        self.client = client
        self.delay = delay
        self.overlap_max = 0
        self._live = 0

    async def send(self, raw):
        import json as _json
        msg = _json.loads(raw)

        async def reply():
            self._live += 1
            self.overlap_max = max(self.overlap_max, self._live)
            await asyncio.sleep(self.delay)
            self._live -= 1
            fut = self.client._pending.get(msg["id"])
            if fut and not fut.done():
                fut.set_result({"status": 200, "data": {"ok": True}})

        asyncio.create_task(reply())


def _client(delay=0.3):
    c = FlowClient()
    ws = _FakeWS(c, delay)
    c._extension_ws = ws
    return c, ws


def test_bon_luot_chay_cung_luc():
    async def scenario():
        c, ws = _client(delay=0.3)
        t0 = time.monotonic()
        await asyncio.gather(*(c.boq_request("ogiZ0b", [i]) for i in range(4)))
        return time.monotonic() - t0, ws.overlap_max

    elapsed, overlap = asyncio.run(scenario())
    assert overlap == 4, f"chỉ có {overlap} lượt chồng nhau — vẫn đang nối đuôi"
    # Nối đuôi thì mất ~1,2s; song song thì ~0,3s.
    assert elapsed < 0.8, f"mất {elapsed:.2f}s — nghi vẫn còn khoá single-flight"


def test_van_co_TRAN_khong_tha_tu_do():
    """Bắn vài chục lượt cùng lúc là mời Google chặn vì 'hoạt động bất thường'."""
    async def scenario():
        c, ws = _client(delay=0.05)
        await asyncio.gather(*(c.boq_request("ogiZ0b", [i]) for i in range(20)))
        return ws.overlap_max

    overlap = asyncio.run(scenario())
    assert overlap <= 4, f"{overlap} lượt cùng lúc — trần không có tác dụng"


def test_khong_con_khoa_single_flight():
    c, _ = _client()
    assert not hasattr(c, "_flow_lock")
