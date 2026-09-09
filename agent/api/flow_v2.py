"""`/api/flow/v2/*` — đường batchexecute, KHÔNG dùng token ya29.

Các endpoint ở `flow.py` vẫn đi `aisandbox-pa` bằng token `ya29.*` do app Next.js ở
labs.google phát. Ngày labs.google tắt, thứ mất không phải vài endpoint tRPC mà là
NGUỒN TOKEN — nên toàn bộ đường cũ chết theo, kể cả khâu sinh. Nhóm /v2 làm đúng những
việc ấy qua batchexecute, xác thực bằng cookie phiên + `at` của chính tab
flow.google.com.

Giữ song song hai đường trong lúc cả hai còn sống là có chủ ý: đường cũ đang chạy sản
xuất, đường mới cần thời gian chạy thật mới đáng tin.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.services import boq_ops as _ops
from agent.services import boq_prices as _prices
from agent.services.boq_client import (BoqClient, BoqError, QuotaError,
                                       default_tier as _boq_tier)
from agent.services.flow_client import get_flow_client

router = APIRouter(prefix="/flow/v2", tags=["flow-v2"])


def _boq() -> BoqClient:
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")
    return BoqClient(client)


async def _run(coro):
    """Đổi lỗi của Flow thành mã HTTP nói được nguyên nhân.

    QuotaError → 403 vì đó là "hạng tài khoản không được phép", biết TRƯỚC khi gọi. Để
    nó rơi xuống Flow thì chỉ nhận `error [3]` trống rỗng — mà mã ấy còn dùng cho nhiều
    nguyên nhân khác hẳn nhau (ảnh tải lên không upsample được, payload sai số ô), nên
    nhìn vào là không truy được.
    """
    try:
        return await coro
    except QuotaError as e:
        raise HTTPException(403, str(e))
    except BoqError as e:
        raise HTTPException(502, str(e))
    except TimeoutError as e:
        raise HTTPException(504, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── đọc ───────────────────────────────────────────────────────

@router.get("/credits")
async def v2_credits():
    return {"credits": await _run(_boq().credits())}


_RE_NHAT = {
    "video_text": ["veo_3_1_t2v_lite_low_priority", "veo_3_1_t2v_lite"],
    "video_refs": ["veo_3_1_r2v_lite_low_priority", "veo_3_1_r2v_lite"],
    "video_frame": ["veo_3_1_i2v_s_lite_4s_low_priority", "veo_3_1_i2v_lite"],
    "video_frames": ["veo_3_1_interpolation_lite_low_priority",
                     "veo_3_1_interpolation_lite"],
    "extend": ["veo_3_1_extension_lite_low_priority", "veo_3_1_extension_lite"],
    "edit_video": ["abra_edit_360p", "abra_edit"],
    "upscale_1080p": ["veo_3_1_upsampler_1080p"],
    "upscale_4k": ["veo_3_1_upsampler_4k"],
}


@router.get("/models")
async def v2_models(tier: Optional[int] = None):
    """Bảng giá theo hạng tài khoản, kèm khoá rẻ nhất cho từng thao tác.

    `null` nghĩa là hạng này KHÔNG dùng được khoá đó — khác hẳn 0 nghĩa là dùng được và
    miễn phí.
    """
    tier = tier if tier is not None else _boq_tier()
    return {
        "tier": tier,
        "re_nhat": {k: _prices.cheapest(v, tier) for k, v in _RE_NHAT.items()},
        "gia": {k: {m: _prices.price(m, tier) for m in v} for k, v in _RE_NHAT.items()},
    }


@router.get("/projects")
async def v2_projects(page_size: int = 20, limit: Optional[int] = None):
    """Danh sách dự án, ĐI HẾT MỌI TRANG — đường cũ chỉ lấy trang đầu rồi dừng."""
    return {"projects": await _run(_boq().list_projects(page_size, limit))}


@router.get("/project/{project_id}")
async def v2_project(project_id: str):
    return await _run(_boq().get_project(project_id))


@router.get("/media/{media_id}")
async def v2_media(media_id: str):
    """Bản ghi media kèm URL MỚI, hạn 6 giờ.

    Media KHÔNG mất khi URL hết hạn — gọi lại là có URL mới, kể cả dự án ba tháng tuổi.
    Lưu `mediaId`, ĐỪNG lưu URL: cache URL thì sáu giờ sau cả thư viện thành ảnh vỡ, mà
    triệu chứng nhìn giống hệt "media bị xoá".
    """
    return await _run(_boq().get_media(media_id))


@router.get("/poll/{media_id}")
async def v2_poll(media_id: str):
    done, wf = await _run(_boq().poll(media_id))
    return {"done": done,
            "media_id": _ops.workflow_media_id(wf) if wf else None,
            "status": _ops.workflow_status(wf) if wf else None}


# ─── dự án ─────────────────────────────────────────────────────

class V2ProjectBody(BaseModel):
    title: str


@router.post("/project")
async def v2_create_project(body: V2ProjectBody):
    return {"project_id": await _run(_boq().create_project(body.title))}


@router.patch("/project/{project_id}")
async def v2_rename_project(project_id: str, body: V2ProjectBody):
    await _run(_boq().rename_project(project_id, body.title))
    return {"ok": True}


@router.delete("/project/{project_id}")
async def v2_delete_project(project_id: str):
    """Xoá THẬT. Khác `/workflow/trash` vốn chỉ bật cờ archived."""
    await _run(_boq().delete_project(project_id))
    return {"ok": True}


class V2CoverBody(BaseModel):
    title: str
    media_id: str


@router.post("/project/{project_id}/cover")
async def v2_set_cover(project_id: str, body: V2CoverBody):
    """Đặt ảnh bìa. Mặt mask sai KHÔNG báo lỗi — Flow trả 200 rồi lặng lẽ không làm gì."""
    return await _run(_boq().set_project_cover(project_id, body.title, body.media_id))


class V2RenameWorkflowBody(BaseModel):
    project_id: str
    name: str


@router.patch("/workflow/{workflow_id}")
async def v2_rename_workflow(workflow_id: str, body: V2RenameWorkflowBody):
    await _run(_boq().rename_workflow(workflow_id, body.project_id, body.name))
    return {"ok": True}


class V2TrashBody(BaseModel):
    project_id: str
    workflow_ids: list[str]


@router.post("/workflow/trash")
async def v2_trash(body: V2TrashBody):
    """Chuyển vào thùng rác — chỉ bật cờ `metadata.archived`, khôi phục được."""
    await _run(_boq().trash_workflows(body.workflow_ids, body.project_id))
    return {"ok": True}


# ─── ảnh ───────────────────────────────────────────────────────

class V2ImageBody(BaseModel):
    prompt: str
    project_id: str
    ratio: int = 3                  # bảng của ẢNH: 1..5. KHÁC bảng của video.
    model_key: str = "GEM_PIX_2"
    references: Optional[list[dict]] = None    # [{media_id, type}] 1 = ref, 2 = nền
    batch_id: Optional[str] = None


@router.post("/image")
async def v2_generate_image(body: V2ImageBody):
    """Sinh MỘT ảnh. Lô 4 ảnh = bốn lời gọi riêng chung một `batch_id`. 0 credit."""
    return await _run(_boq().generate_image(
        body.prompt, body.project_id, body.ratio, body.model_key,
        body.references, batch_id=body.batch_id))


class V2EditImageBody(BaseModel):
    prompt: str
    source_media_id: str
    workflow_id: str
    project_id: str
    ratio: int = 3
    model_key: str = "GEM_PIX_2"


@router.post("/image/edit")
async def v2_edit_image(body: V2EditImageBody):
    """Sửa ảnh. Prompt phải viết bằng TIẾNG ANH và mô tả KẾT QUẢ mong muốn.

    Flow dịch prompt không phải tiếng Anh, và bản dịch đánh rơi câu PHỦ ĐỊNH — "xoá
    người khỏi ảnh" từng bị dịch thành một câu chú thích rồi cho ra ảnh ĐÔNG người hơn
    ảnh gốc.
    """
    return await _run(_boq().edit_image(
        body.prompt, body.source_media_id, body.workflow_id, body.project_id,
        body.ratio, body.model_key))


class V2UploadBody(BaseModel):
    image_base64: str
    project_id: str
    mime_type: str = "image/jpeg"
    file_name: str = "upload.jpg"


@router.post("/image/upload")
async def v2_upload_image(body: V2UploadBody):
    """Ảnh đi BẰNG BASE64 ngay trong `f.req` — không có endpoint upload riêng."""
    return await _run(_boq().upload_image(
        body.image_base64, body.project_id, body.mime_type, body.file_name))


# ─── video: bảy thao tác, bảy rpcid ────────────────────────────
# Mỗi kiểu đầu vào là một RPC riêng. Không gộp được, và đừng suy payload của cái này từ
# cái kia — chúng là message type khác nhau nên cùng một khái niệm nằm ở ô khác nhau.

class V2VideoTextBody(BaseModel):
    prompt: str
    project_id: str
    model_key: str = "veo_3_1_t2v_lite_low_priority"
    ratio: int = _ops.RATIO_LANDSCAPE      # 1 = 9:16, 2 = 16:9
    batch_id: Optional[str] = None


@router.post("/video/text")
async def v2_video_text(body: V2VideoTextBody):
    return await _run(_boq().generate_video_text(
        body.prompt, body.model_key, body.project_id, body.ratio, body.batch_id))


class V2VideoRefsBody(BaseModel):
    # Mỗi mảnh: {"text": "..."} hoặc {"media_id": "...", "name": "..."}. Ảnh được NEO
    # vào đúng vị trí trong câu — structured prompt, giống hệt bên ảnh.
    parts: list[dict]
    project_id: str
    model_key: str = "veo_3_1_r2v_lite_low_priority"
    ratio: int = _ops.RATIO_LANDSCAPE
    batch_id: Optional[str] = None


@router.post("/video/refs")
async def v2_video_refs(body: V2VideoRefsBody):
    return await _run(_boq().generate_video_refs(
        body.parts, body.model_key, body.project_id, body.ratio, body.batch_id))


class V2VideoFrameBody(BaseModel):
    prompt: str
    media_id: str
    project_id: str
    model_key: str = "veo_3_1_i2v_s_lite_4s_low_priority"
    ratio: int = _ops.RATIO_LANDSCAPE
    # Kích thước THẬT của ảnh, để tự tính khung cắt khi tỉ lệ ảnh khác tỉ lệ video. Bỏ
    # trống thì không cắt, và Flow xử lý theo cách ta không kiểm soát.
    image_size: Optional[list[int]] = None
    batch_id: Optional[str] = None


@router.post("/video/frame")
async def v2_video_frame(body: V2VideoFrameBody):
    crop = (_ops.crop_rect(body.image_size[0], body.image_size[1], body.ratio)
            if body.image_size else None)
    return await _run(_boq().generate_video_frame(
        body.prompt, body.media_id, body.model_key, body.project_id,
        body.ratio, crop, body.batch_id))


class V2VideoFramesBody(BaseModel):
    prompt: str
    first_media_id: str
    last_media_id: str
    project_id: str
    model_key: str = "veo_3_1_interpolation_lite_low_priority"
    ratio: int = _ops.RATIO_LANDSCAPE
    first_size: Optional[list[int]] = None
    last_size: Optional[list[int]] = None
    batch_id: Optional[str] = None


@router.post("/video/frames")
async def v2_video_frames(body: V2VideoFramesBody):
    """Nội suy giữa khung đầu và khung cuối.

    Veo cố định 8 giây; Omni để độ dài trong tên khoá nên chọn được 4/6/8/10.
    """
    fc = (_ops.crop_rect(body.first_size[0], body.first_size[1], body.ratio)
          if body.first_size else None)
    lc = (_ops.crop_rect(body.last_size[0], body.last_size[1], body.ratio)
          if body.last_size else None)
    return await _run(_boq().generate_video_frames(
        body.prompt, body.first_media_id, body.last_media_id, body.model_key,
        body.project_id, body.ratio, fc, lc, body.batch_id))


class V2SceneBody(BaseModel):
    project_id: str
    workflow_id: str


@router.post("/scene")
async def v2_create_scene(body: V2SceneBody):
    """Tạo scene từ một workflow. Nối dài CHỈ chạy được trong scene.

    Nó NHÂN BẢN clip chứ không bọc lại: clip trong scene mang workflow và media mới. Gọi
    lại là thêm một scene nữa — đừng gọi trong vòng thử lại.
    """
    return await _run(_boq().create_scene(body.project_id, body.workflow_id))


class V2ExtendBody(BaseModel):
    prompt: str
    source_media_id: str
    scene_id: str
    project_id: str
    model_key: str = "veo_3_1_extension_lite_low_priority"
    ratio: int = _ops.RATIO_LANDSCAPE
    # Chỉ số khung hình trên clip nguồn ở 24fps. Mặc định lấy ~1 giây cuối làm mồi, đúng
    # như giao diện (169..192 cho clip 8 giây).
    from_frame: Optional[int] = None
    to_frame: int = 8 * _ops.FPS
    position: int = 1
    batch_id: Optional[str] = None


@router.post("/video/extend")
async def v2_extend(body: V2ExtendBody):
    """Nối dài. Giao diện CHỈ cho chọn bản 5 credit; mặc định ở đây là bản 0 đồng."""
    return await _run(_boq().extend_video(
        body.prompt, body.source_media_id, body.scene_id, body.model_key,
        body.project_id, body.ratio, body.from_frame, body.to_frame,
        body.position, body.batch_id))


class V2EditVideoBody(BaseModel):
    prompt: str
    source_media_id: str
    workflow_id: str
    project_id: str
    model_key: str = "abra_edit_360p"
    ratio: int = _ops.RATIO_LANDSCAPE
    from_frame: int = 0
    to_frame: int = 8 * _ops.FPS
    batch_id: Optional[str] = None


@router.post("/video/edit")
async def v2_edit_video(body: V2EditVideoBody):
    """Sửa video bằng prompt. CHỈ Omni Flash làm được — không có bản Veo.

    Đây là thao tác sinh video DUY NHẤT không có đường 0 đồng: rẻ nhất là
    `abra_edit_360p` = 10 credit, bản 720p là 20.
    """
    return await _run(_boq().edit_video(
        body.prompt, body.source_media_id, body.workflow_id, body.model_key,
        body.project_id, body.ratio, body.from_frame, body.to_frame, body.batch_id))


class V2UpscaleBody(BaseModel):
    media_id: str
    workflow_id: str
    project_id: str
    four_k: bool = False        # 1080p = 0 credit; 4K = 50 và CHỈ tài khoản Ultra
    ratio: int = _ops.RATIO_LANDSCAPE
    batch_id: Optional[str] = None


@router.post("/video/upscale")
async def v2_upscale(body: V2UpscaleBody):
    """Nâng độ phân giải.

    Media ra mang id dẫn xuất, đoán trước được: `<mediaId>_upsampled` (1080p) và
    `<mediaId>_4k_upsampled` (4K). Hai bản cùng tồn tại, 4K không đè 1080p.
    """
    return await _run(_boq().upscale_video(
        body.media_id, body.workflow_id, body.project_id, body.four_k,
        body.ratio, body.batch_id))


class V2PrimaryBody(BaseModel):
    workflow_id: str
    media_id: str
    project_id: str


@router.post("/primary-media")
async def v2_set_primary(body: V2PrimaryBody):
    """Trỏ ảnh đại diện của workflow sang media mới — bước 'sửa tại chỗ' của giao diện.

    Sửa ảnh/video qua API KHÔNG tự làm bước này, nên mặc định là cộng thêm chứ không đè.
    """
    await _run(_boq().set_primary_media(
        body.workflow_id, body.media_id, body.project_id))
    return {"ok": True}
