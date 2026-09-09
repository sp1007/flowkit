"""Lớp gọi Flow qua batchexecute — thay cho đường `aisandbox-pa` + token `ya29`.

Chỗ đứng của file này: `boq_ops` dựng payload (thuần, có test đối chiếu payload thật),
`FlowClient.boq_request` đẩy một lời gọi qua extension, còn đây là lớp ở giữa — biết
rpcid nào cho việc gì, kiểm giá trước khi bắn, và đọc phản hồi ra thứ dùng được.

Toàn bộ đường này KHÔNG dùng `Authorization: Bearer ya29.*`. Giao diện mới xác thực
bằng cookie phiên cộng `at`, và chính điều đó là lý do phải dựng lại: token `ya29` là
do app Next.js ở labs.google phát, nên ngày labs.google tắt thì thứ mất không phải vài
endpoint tRPC mà là NGUỒN TOKEN.
"""
from __future__ import annotations

import asyncio
from typing import Optional, Sequence

from . import boq_ops as ops
from . import boq_prices as prices


class BoqError(RuntimeError):
    """Lỗi từ chính Flow (nằm trong `wrb.fr` slot [5]), không phải lỗi vận chuyển."""

    def __init__(self, rpcid: str, error, message: str = ""):
        self.rpcid = rpcid
        self.error = error
        self.code = error[0] if isinstance(error, list) and error else None
        super().__init__(message or f"{rpcid} lỗi {self.code}: {error}")


class QuotaError(RuntimeError):
    """Hạng tài khoản không được phép dùng khoá model này."""


class BoqClient:
    def __init__(self, flow_client, tier: int = prices.TIER_ULTRA):
        self._flow = flow_client
        self.tier = tier

    # ─── nền ─────────────────────────────────────────────────

    async def call(self, rpcid: str, args=None, *, project_id: Optional[str] = None,
                   source_path: Optional[str] = None,
                   captcha_action: str = "VIDEO_GENERATION",
                   timeout: float = 120):
        """Gọi một rpcid, trả về `data` đã bóc vỏ. Ném BoqError khi Flow báo lỗi.

        `captcha_action` mặc định là VIDEO_GENERATION chứ không phải IMAGE_GENERATION:
        khâu ảnh gọi qua các hàm riêng ở dưới và tự truyền action của nó. Lưu ý một chỗ
        CHƯA chắc chắn — một lượt submit từng hỏng `PUBLIC_ERROR_UNUSUAL_ACTIVITY` với
        IMAGE_GENERATION rồi chạy khi đổi sang VIDEO_GENERATION, nhưng một lần thử không
        tách được "do action" với "trùng nhịp chống lạm dụng".
        """
        if source_path is None and project_id:
            source_path = f"/project/{project_id}"
        res = await self._flow.boq_request(rpcid, args, source_path, captcha_action,
                                           timeout=timeout)
        if res.get("error"):
            raise BoqError(rpcid, None, str(res["error"]))
        data = res.get("data", res)
        results = (data or {}).get("results") or []
        for entry in results:
            if entry.get("rpcid") == rpcid:
                if entry.get("error"):
                    raise BoqError(rpcid, entry["error"])
                return entry.get("data")
        raise BoqError(rpcid, None, f"{rpcid}: phản hồi không có kết quả")

    def _check_price(self, model_key: str) -> int:
        cost = prices.price(model_key, self.tier)
        if cost is None:
            raise QuotaError(
                f"{model_key} không dùng được ở tier {self.tier}. "
                f"Giá theo tier: {prices.tiers(model_key)}")
        return cost

    # ─── đọc ─────────────────────────────────────────────────

    async def credits(self) -> int:
        data = await self.call("nzlxg", [], captcha_action="IMAGE_GENERATION")
        return data[0]

    async def list_projects(self, page_size: int = 20,
                            limit: Optional[int] = None) -> list:
        """Toàn bộ dự án, ĐI HẾT MỌI TRANG.

        Bản chính chỉ lấy trang đầu rồi dừng, nên tài khoản có 60 dự án thì 40 cái biến
        mất khỏi mọi màn hình mà không có lỗi nào.

        Con trỏ trả về ở ô [1] của phản hồi và phải gửi lại ở ô [2] của lượt sau. CÓ MỘT
        Ô CON TRỎ THỨ HAI Ở [3] và đặt nhầm vào đó thì Flow trả 200 kèm ĐÚNG TRANG ĐẦU —
        không lỗi, không cảnh báo. Vòng lặp ngây thơ sẽ quay mãi trên trang 1; vì vậy
        dưới đây còn chặn thêm bằng cách theo dõi id đã thấy.
        """
        out: list = []
        seen: set = set()
        cursor = None
        while True:
            data = await self.call("UpteDb",
                                   ["projects/*", page_size, cursor, None, None, None, [1]],
                                   captcha_action="IMAGE_GENERATION")
            page = (data or [None])[0] or []
            moi = [p for p in page if p and p[0] not in seen]
            if not moi:
                break                      # không tiến thêm được — thoát thay vì quay vòng
            seen.update(p[0] for p in moi)
            out.extend(moi)
            cursor = data[1] if isinstance(data, list) and len(data) > 1 else None
            if not cursor:
                break
            if limit is not None and len(out) >= limit:
                break
        return out[:limit] if limit is not None else out

    async def get_project(self, project_id: str) -> list:
        return await self.call("Zzl0ze", [f"projects/{project_id}", None, None, None, [1]],
                               project_id=project_id, captcha_action="IMAGE_GENERATION")

    async def get_media(self, media_id: str) -> list:
        """Bản ghi media kèm URL MỚI.

        URL sống 6 giờ rồi hết hạn, nhưng media KHÔNG mất — gọi lại là có URL mới, kể cả
        với dự án ba tháng tuổi (đã đo). Hệ quả: lưu `mediaId`, ĐỪNG lưu URL. Cache URL
        để đỡ một lời gọi thì sáu giờ sau cả thư viện thành ảnh vỡ, mà triệu chứng lại
        giống hệt "media bị xoá".
        """
        return await self.call("as29s", [media_id], captcha_action="IMAGE_GENERATION")

    async def poll(self, media_id: str) -> tuple[bool, Optional[list]]:
        """(đã xong, bản ghi workflow). Không chứa URL — xong rồi phải gọi `get_media`."""
        data = await self.call("jwpduf", [None, None, [[media_id]]],
                               captcha_action="IMAGE_GENERATION")
        wf = ops._dig(data, 2, 0)
        return (ops.workflow_done(wf) if wf else False), wf

    async def wait(self, media_id: str, interval: float = 5.0,
                   timeout: float = 600.0) -> list:
        """Chờ tới khi render xong, trả bản ghi workflow."""
        waited = 0.0
        while True:
            done, wf = await self.poll(media_id)
            if done:
                return wf
            if waited >= timeout:
                raise TimeoutError(f"{media_id} chưa xong sau {timeout:.0f}s")
            await asyncio.sleep(interval)
            waited += interval

    # ─── sinh video ──────────────────────────────────────────

    async def _submit(self, rpcid: str, args, project_id: str, model_key: str) -> dict:
        data = await self.call(rpcid, args, project_id=project_id)
        wf = ops._dig(data, 3, 0)
        return {
            "media_id": ops.workflow_media_id(wf) or ops._dig(wf, 0),
            "workflow_id": ops._dig(wf, 0),
            "credits_left": ops.credits_left(data),
            "model_key": model_key,
            "raw": data,
        }

    async def generate_video_text(self, prompt: str, model_key: str, project_id: str,
                                  ratio: int = ops.RATIO_LANDSCAPE,
                                  batch_id: Optional[str] = None) -> dict:
        self._check_price(model_key)
        rpcid, args = ops.generate_video_text(prompt, model_key, project_id, ratio, batch_id)
        return await self._submit(rpcid, args, project_id, model_key)

    async def generate_video_refs(self, parts: Sequence[dict], model_key: str,
                                  project_id: str, ratio: int = ops.RATIO_LANDSCAPE,
                                  batch_id: Optional[str] = None) -> dict:
        self._check_price(model_key)
        rpcid, args = ops.generate_video_refs(parts, model_key, project_id, ratio, batch_id)
        return await self._submit(rpcid, args, project_id, model_key)

    async def generate_video_frame(self, prompt: str, media_id: str, model_key: str,
                                   project_id: str, ratio: int = ops.RATIO_LANDSCAPE,
                                   crop: Optional[list] = None,
                                   batch_id: Optional[str] = None) -> dict:
        self._check_price(model_key)
        rpcid, args = ops.generate_video_frame(prompt, media_id, model_key, project_id,
                                               ratio, crop, batch_id)
        return await self._submit(rpcid, args, project_id, model_key)

    async def generate_video_frames(self, prompt: str, first_media_id: str,
                                    last_media_id: str, model_key: str, project_id: str,
                                    ratio: int = ops.RATIO_LANDSCAPE,
                                    first_crop: Optional[list] = None,
                                    last_crop: Optional[list] = None,
                                    batch_id: Optional[str] = None) -> dict:
        self._check_price(model_key)
        rpcid, args = ops.generate_video_frames(prompt, first_media_id, last_media_id,
                                                model_key, project_id, ratio,
                                                first_crop, last_crop, batch_id)
        return await self._submit(rpcid, args, project_id, model_key)

    # ─── scene + nối dài ─────────────────────────────────────

    async def create_scene(self, project_id: str, workflow_id: str) -> dict:
        """Tạo scene (dòng thời gian) từ một workflow. Nối dài CHỈ chạy trong scene.

        Nó KHÔNG chỉ bọc workflow lại — nó NHÂN BẢN. Đo trên lượt gọi thật: truyền
        workflow `4229da67` vào thì clip trong scene mang workflow `d1a66177` và media
        `f950337d`, đều mới. Nghĩa là sửa/nối trong scene không đụng tới bản gốc, và
        cũng nghĩa là mỗi lần gọi lại là thêm một scene nữa — đừng gọi trong vòng lặp
        thử lại.

        Phản hồi: `[[sceneId, "Untitled Scene …", null, ts, ts, 2, null, []], [<clip>…]]`.
        """
        data = await self.call("rqZuUc", [f"projects/{project_id}", [workflow_id],
                                          None, None, 2],
                               project_id=project_id, captcha_action="IMAGE_GENERATION")
        scene_id = ops._dig(data, 0, 0)
        if not scene_id:
            raise BoqError("rqZuUc", None, f"không đọc được sceneId từ {data!r}")
        clip = ops._dig(data, 1, 0, 0)
        return {
            "scene_id": scene_id,
            "name": ops._dig(data, 0, 1),
            "clip_workflow_id": ops._dig(clip, 0),
            "clip_media_id": ops._dig(clip, 3, 4),
        }

    async def get_scene(self, scene_id: str, project_id: str) -> list:
        """Nội dung scene. Poll MỘT scene rẻ hơn poll từng mediaId khi nối nhiều clip."""
        return await self.call("uwAyfb", [scene_id, project_id],
                               project_id=project_id, captcha_action="IMAGE_GENERATION")

    async def extend_video(self, prompt: str, source_media_id: str, scene_id: str,
                           model_key: str, project_id: str,
                           ratio: int = ops.RATIO_LANDSCAPE,
                           from_frame: Optional[int] = None, to_frame: int = 8 * ops.FPS,
                           position: int = 1,
                           batch_id: Optional[str] = None) -> dict:
        self._check_price(model_key)
        rpcid, args = ops.extend_video(prompt, source_media_id, scene_id, model_key,
                                       project_id, ratio, from_frame, to_frame,
                                       position, batch_id)
        return await self._submit(rpcid, args, project_id, model_key)

    # ─── sửa + nâng cấp ──────────────────────────────────────

    async def edit_video(self, prompt: str, source_media_id: str, workflow_id: str,
                         model_key: str, project_id: str,
                         ratio: int = ops.RATIO_LANDSCAPE,
                         from_frame: int = 0, to_frame: int = 8 * ops.FPS,
                         batch_id: Optional[str] = None) -> dict:
        """Sửa clip bằng prompt. Chỉ Omni Flash làm được — không có bản Veo."""
        if not model_key.startswith(("abra_edit", "omni")):
            raise ValueError(f"{model_key} không sửa được video; chỉ abra_edit* làm được")
        self._check_price(model_key)
        rpcid, args = ops.edit_video(prompt, source_media_id, workflow_id, model_key,
                                     project_id, ratio, from_frame, to_frame, batch_id)
        return await self._submit(rpcid, args, project_id, model_key)

    async def upscale_video(self, media_id: str, workflow_id: str, project_id: str,
                            four_k: bool = False,
                            ratio: int = ops.RATIO_LANDSCAPE,
                            batch_id: Optional[str] = None) -> dict:
        """Nâng lên 1080p (0 credit) hoặc 4K (50, chỉ Ultra).

        Kết quả mang id dẫn xuất `<mediaId>_upsampled` / `<mediaId>_4k_upsampled`, đoán
        trước được nên trả luôn ở đây. Hai bản cùng tồn tại, 4K không đè 1080p.
        """
        model_key = "veo_3_1_upsampler_4k" if four_k else "veo_3_1_upsampler_1080p"
        self._check_price(model_key)
        rpcid, args = ops.upscale_video(media_id, workflow_id, model_key, project_id,
                                        ratio, batch_id)
        out = await self._submit(rpcid, args, project_id, model_key)
        out["media_id"] = ops.upsampled_media_id(media_id, four_k)
        return out

    async def set_primary_media(self, workflow_id: str, media_id: str,
                                project_id: str) -> list:
        """Trỏ ảnh đại diện của workflow sang media mới — bước 'sửa tại chỗ' của giao diện."""
        return await self.call(
            "mYWVGd",
            [[workflow_id, None, None, [None, None, None, None, media_id], project_id],
             [["metadata.primary_media_id"]]],
            project_id=project_id, captcha_action="IMAGE_GENERATION")
