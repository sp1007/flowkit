"""Đường BOQ mang HÌNH DẠNG của API cũ, để tầng `studio` chạy nguyên không phải sửa.

Vì sao làm thế này thay vì sửa studio: studio đọc kết quả Flow ở hàng chục chỗ
(`_extract_video_submit`, `_generated_media_id`, `videopoll._status_of`,
`hires._upsample_operation`, `media_store.resolve_url`…). Sửa hết là một lượt thay đổi
lớn trên đúng phần đang chạy sản xuất, mà lại không kiểm được bằng gì ngoài chạy thử.
Đổi ĐỘNG CƠ mà giữ nguyên DÂY thì bề mặt thay đổi chỉ còn một file, và bật/tắt được
bằng một biến môi trường — nên so sánh hai đường trên cùng một đầu vào là chuyện dễ.

Bật bằng `FLOWKIT_USE_BOQ=1`. Tắt (mặc định) thì `FlowClient` chạy y như cũ.

Hình dạng phải khớp, đọc ngược từ chỗ studio dùng:

  sinh/sửa ảnh   {"media": [{"name", "image": {"generatedImage": {"mediaId", "prompt"}},
                             "fifeUrl"}]}
  submit video   {"media": [{"name"}], "workflows": [{"name", "metadata": {…}}]}
  poll video     {"media": [{"name", "mediaMetadata": {"mediaStatus":
                             {"mediaGenerationStatus", "failureReasons"}}}],
                  "remainingCredits"}
  upscale video  {"operations": [{"operation": {"name": "<mediaId>_upsampled"},
                                  "status"}]}
  resolve media  {"redirected": true, "url"}

`generatedImage.prompt` là chỗ DUY NHẤT đọc được prompt mà Flow THẬT SỰ nhận (nó dịch
prompt không phải tiếng Anh, và bản dịch đánh rơi câu phủ định). Đường BOQ không trả
trường ấy, nên ở đây điền lại bằng prompt đã gửi — ghi rõ để đừng ai dùng nó làm bằng
chứng "Flow nhận đúng thế này".
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from . import boq_ops as ops
from . import boq_prices as prices
from .boq_client import BoqClient, BoqError, QuotaError

_MODELS_PATH = Path(__file__).resolve().parent.parent / "models.json"


def enabled() -> bool:
    return os.environ.get("FLOWKIT_USE_BOQ", "").strip() in ("1", "true", "yes", "on")


def _models() -> dict:
    try:
        return json.loads(_MODELS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ─── đổi tên hằng của API cũ sang số của BOQ ─────────────────

# Ảnh: bảng 1..5. Giá trị ngoài dải KHÔNG báo lỗi mà lặng lẽ trả 1408x768.
_IMAGE_RATIO = {
    "IMAGE_ASPECT_RATIO_SQUARE": 1,
    "IMAGE_ASPECT_RATIO_PORTRAIT": 2,
    "IMAGE_ASPECT_RATIO_LANDSCAPE": 3,
    "IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR": 4,
    "IMAGE_ASPECT_RATIO_LANDSCAPE_FOUR_THREE": 5,
}

# Video: bảng RIÊNG, chỉ hai giá trị đo được. Đừng dùng chung với bảng ảnh.
_VIDEO_RATIO = {
    "VIDEO_ASPECT_RATIO_PORTRAIT": ops.RATIO_PORTRAIT,
    "VIDEO_ASPECT_RATIO_LANDSCAPE": ops.RATIO_LANDSCAPE,
}


def image_ratio(aspect: Optional[str]) -> int:
    return _IMAGE_RATIO.get(aspect or "", 2)


def video_ratio(aspect: Optional[str]) -> int:
    return _VIDEO_RATIO.get(aspect or "", ops.RATIO_LANDSCAPE)


def tier_of(paygate: Optional[str]) -> int:
    return prices.tier_from_paygate(paygate)


# ─── dựng lại hình dạng cũ ───────────────────────────────────

_OK = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
_FAIL = "MEDIA_GENERATION_STATUS_FAILED"
_PENDING = "MEDIA_GENERATION_STATUS_PENDING"


def _image_payload(media_id: str, prompt: str, url: Optional[str],
                   size: Optional[list] = None) -> dict:
    return {"media": [{
        "name": media_id,
        "image": {"generatedImage": {
            "mediaId": media_id,
            # KHÔNG phải prompt Flow thật sự nhận — đường BOQ không trả trường đó. Điền
            # lại prompt đã gửi để studio có cái đọc; đừng dùng làm bằng chứng.
            "prompt": prompt,
        }},
        "fifeUrl": url,
        "servingBaseUri": url,
        "size": size,
    }]}


def _video_submit_payload(out: dict) -> dict:
    return {
        "media": [{"name": out["media_id"]}],
        "workflows": [{"name": out.get("workflow_id"),
                       "metadata": {"primaryMediaId": out["media_id"]}}],
        "remainingCredits": out.get("credits_left"),
    }


def _ok(data: dict) -> dict:
    """Vỏ ngoài giống `_send`: studio đọc `res.get("data", res)` và `res.get("error")`."""
    return {"status": 200, "data": data}


def _soft(fn):
    """Đổi lỗi của Flow thành `{"error": …}` thay vì ném exception.

    Studio bọc mọi lượt sinh trong vòng THỬ LẠI và nhận biết hỏng bằng `res.get("error")`
    — nó không bắt exception. Để BoqError bay lên thì một ảnh bị lọc nội dung làm hỏng cả
    job thay vì thử lại bằng seed khác. Đo trực tiếp: prompt bạo lực gửi qua
    `POST /api/flow/generate-image` trả HTTP 500 thay vì một lượt thử lại.

    Giữ nguyên văn mã lỗi, KHÔNG dịch `[3]` thành "bị lọc nội dung": cùng mã ấy còn dùng
    cho payload sai số ô và cho ảnh tải lên không upsample được. Gắn nhãn "vi phạm chính
    sách" lên một lỗi lập trình là giấu mất bug thật.
    """
    import functools

    @functools.wraps(fn)
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except QuotaError as e:
            return {"status": 403, "error": str(e)}
        except BoqError as e:
            return {"status": 502, "error": str(e)}
        except (ValueError, KeyError, TypeError, IndexError) as e:
            return {"status": 500, "error": f"{type(e).__name__}: {e}"}
    return wrapper


# ─── các thao tác ────────────────────────────────────────────

class BoqCompat:
    """Cài lại các method của FlowClient trên BOQ, giữ nguyên chữ ký và hình dạng trả về."""

    def __init__(self, flow_client):
        self._flow = flow_client
        self.boq = BoqClient(flow_client)

    # ---- ảnh ----

    @_soft
    async def generate_images(self, prompt, project_id, aspect_ratio=None,
                              user_paygate_tier=None, character_media_ids=None,
                              references=None, image_model=None, seed=None,
                              batch_id=None, **_ignored) -> dict:
        """Sinh MỘT ảnh. `character_media_ids` và `references` đều thành ảnh THAM CHIẾU.

        Hai cờ `bind_unreferenced` / `dedupe_refs` của đường cũ không dùng ở đây: chúng
        điều khiển việc dựng `structuredPrompt`, mà lượt sinh ảnh của BOQ gửi prompt
        phẳng cộng một danh sách ảnh vào — không có part để trùng hay để vụn. Nuốt lặng
        thay vì báo lỗi, vì người gọi truyền chúng theo thói quen.
        """
        refs = [{"media_id": m, "type": ops.IMAGE_INPUT_REFERENCE}
                for m in (character_media_ids or [])]
        refs += [{"media_id": r["media_id"], "type": ops.IMAGE_INPUT_REFERENCE}
                 for r in (references or []) if r.get("media_id")]
        model = image_model or _models().get("image_models", {}).get(
            "NANO_BANANA_PRO", "GEM_PIX_2")
        out = await self.boq.generate_image(
            prompt, project_id, image_ratio(aspect_ratio), model,
            references=refs or None, seed=seed, batch_id=batch_id)
        return _ok(_image_payload(out["media_id"], prompt, out.get("url"),
                                  out.get("size")))

    @_soft
    async def edit_image(self, prompt, source_media_id, project_id, aspect_ratio=None,
                         user_paygate_tier=None, character_media_ids=None,
                         references=None, base_handle="base", **_ignored) -> dict:
        """Sửa ảnh.

        Đường cũ cần `workflow_id` của ảnh nguồn để nối kết quả vào cùng workflow; ở đây
        người gọi chỉ đưa `source_media_id`, nên tra ngược bằng `as29s`. Không tra được
        thì vẫn sửa nhưng kết quả nằm ở workflow mới — vẫn ra ảnh đúng, chỉ là trên giao
        diện Flow nó đứng riêng thay vì nằm cùng ảnh gốc.
        """
        wf = await self._workflow_of(source_media_id)
        extra = [{"media_id": r["media_id"], "type": ops.IMAGE_INPUT_REFERENCE}
                 for r in (references or []) if r.get("media_id")]
        extra += [{"media_id": m, "type": ops.IMAGE_INPUT_REFERENCE}
                  for m in (character_media_ids or [])]
        model = _models().get("image_models", {}).get("NANO_BANANA_PRO", "GEM_PIX_2")
        out = await self.boq.generate_image(
            prompt, project_id, image_ratio(aspect_ratio), model,
            references=[{"media_id": source_media_id, "type": ops.IMAGE_INPUT_BASE}] + extra,
            workflow_id=wf)
        return _ok(_image_payload(out["media_id"], prompt, out.get("url"),
                                  out.get("size")))

    async def _workflow_of(self, media_id: str) -> Optional[str]:
        try:
            rec = await self.boq.get_media(media_id)
        except Exception:
            return None
        return ops._dig(rec, 0, 2) or ops._dig(rec, 2)

    @_soft
    async def upload_image(self, image_base64, mime_type="image/jpeg", project_id="",
                           file_name="image.jpg") -> dict:
        out = await self.boq.upload_image(image_base64, project_id, mime_type, file_name)
        return _ok({"media": [{"name": out["media_id"]}]})

    @_soft
    async def upscale_image(self, media_id, project_id, target_resolution=None,
                            user_paygate_tier=None) -> dict:
        """Xin bản nét. Mức 1 = 2K, 2 = 4K (4K chỉ Ultra).

        Ảnh do người dùng TẢI LÊN bị Flow từ chối với `error [3]`, và tài khoản Pro xin
        4K nhiều khả năng cũng `error [3]`. Hai nguyên nhân khác hẳn nhau, cùng một mã —
        nên đừng suy nguyên nhân từ mã lỗi ở đây.
        """
        level = 2 if (target_resolution or "").endswith("4K") else 1
        out = await self.boq.upsample_image(media_id, level)
        return _ok({"media": [{"name": ops.upsampled_media_id(media_id, level == 2)}],
                    "raw": out.get("raw")})

    # ---- video ----

    def _veo_lite_key(self, start, end, refs, duration_s) -> str:
        """Khoá theo ẢNH TRUYỀN VÀO, không theo cờ. Chọn sai là đổi hoá đơn."""
        m = _models()
        lite = m.get("veo_lite_models", {})
        frames = m.get("veo_lite_frame_models", {})
        if start and end:
            return frames.get(str(duration_s)) or lite.get("start_end_frame_2_video")
        if start:
            return lite.get("frame_2_video")
        if refs:
            return lite.get("reference_frame_2_video")
        return lite.get("text_2_video")

    @_soft
    async def generate_video_veo_lite(self, prompt, project_id, scene_id="",
                                      start_media_id=None, end_media_id=None,
                                      reference_media_ids=None, references=None,
                                      duration_s=8, aspect_ratio=None,
                                      user_paygate_tier=None, batch_id=None) -> dict:
        ratio = video_ratio(aspect_ratio)
        refs = list(reference_media_ids or [])
        start = start_media_id or (refs[0] if refs and not start_media_id else None)
        key = self._veo_lite_key(start_media_id, end_media_id,
                                 references or reference_media_ids, duration_s)
        out = await self._dispatch_video(prompt, project_id, key, ratio,
                                         start_media_id, end_media_id,
                                         references, reference_media_ids, batch_id)
        return _ok(_video_submit_payload(out))

    @_soft
    async def generate_video_omni(self, prompt, project_id, reference_media_ids,
                                  duration_s=8, aspect_ratio=None,
                                  user_paygate_tier=None, references=None,
                                  batch_id=None) -> dict:
        m = _models()
        has_ref = bool(reference_media_ids or references)
        table = "omni_flash_models" if has_ref else "omni_flash_t2v_models"
        key = m.get(table, {}).get(str(duration_s)) or f"abra_t2v_{duration_s}s"
        out = await self._dispatch_video(prompt, project_id, key,
                                         video_ratio(aspect_ratio),
                                         None, None, references, reference_media_ids,
                                         batch_id)
        return _ok(_video_submit_payload(out))

    @_soft
    async def generate_video(self, start_image_media_id, prompt, project_id, scene_id,
                             aspect_ratio=None, end_image_media_id=None,
                             user_paygate_tier=None, video_model=None,
                             references=None, batch_id=None) -> dict:
        """Veo trả tiền. Khoá lấy từ `video_models` theo tier + tỉ lệ, như đường cũ."""
        m = _models()
        tier_name = user_paygate_tier or "PAYGATE_TIER_TWO"
        kind = "start_end_frame_2_video" if end_image_media_id else "frame_2_video"
        key = video_model or (m.get("video_models", {}).get(tier_name, {})
                              .get(kind, {}).get(aspect_ratio))
        if not key:
            raise ValueError(f"không có khoá model cho {tier_name}/{kind}/{aspect_ratio}")
        out = await self._dispatch_video(prompt, project_id, key,
                                         video_ratio(aspect_ratio),
                                         start_image_media_id, end_image_media_id,
                                         references, None, batch_id)
        return _ok(_video_submit_payload(out))

    async def _dispatch_video(self, prompt, project_id, key, ratio, start, end,
                              references, reference_media_ids, batch_id) -> dict:
        """Chọn ĐÚNG rpcid theo đầu vào.

        Mỗi kiểu đầu vào là một RPC riêng — bảy thao tác, bảy rpcid, không cái nào dùng
        lại cái nào. Đây là chỗ duy nhất biết luật ấy; đừng rải thêm nhánh chỗ khác.
        """
        if start and end:
            return await self.boq.generate_video_frames(
                prompt, start, end, key, project_id, ratio,
                await self._crop(start, ratio), await self._crop(end, ratio), batch_id)
        if start:
            return await self.boq.generate_video_frame(
                prompt, start, key, project_id, ratio,
                await self._crop(start, ratio), batch_id)
        parts = self._parts(prompt, references, reference_media_ids)
        if parts:
            return await self.boq.generate_video_refs(parts, key, project_id, ratio,
                                                      batch_id)
        return await self.boq.generate_video_text(prompt, key, project_id, ratio, batch_id)

    @staticmethod
    def _parts(prompt: str, references, reference_media_ids) -> list:
        """Prompt + ảnh tham chiếu → structured prompt, ảnh NEO đúng chỗ trong câu.

        Token `{tên}` trong prompt được thay bằng mảnh ảnh. Mỗi ảnh chỉ bind MỘT lần
        (lần nhắc đầu) — nhắc lại cùng một ảnh ở nhiều chỗ sinh nhiều mảnh trỏ cùng một
        mediaId và Flow trả lỗi. Ảnh không được prompt gọi tên thì nối vào cuối, vì
        reference không xuất hiện trong câu gần như bị model bỏ qua.
        """
        by_handle = {r["handle"]: r["media_id"]
                     for r in (references or []) if r.get("media_id") and r.get("handle")}
        loose = [m for m in (reference_media_ids or []) if m]
        if not by_handle and not loose:
            return []
        parts: list = []
        used: set = set()

        def push_text(s: str):
            if not s:
                return
            if parts and "text" in parts[-1]:
                parts[-1]["text"] += s          # gộp, đừng để mảnh chữ vụn ra
            else:
                parts.append({"text": s})

        pos = 0
        for mt in re.finditer(r"\{([^{}]+)\}", prompt or ""):
            handle = mt.group(1)
            mid = by_handle.get(handle)
            push_text((prompt or "")[pos:mt.start()])
            pos = mt.end()
            if mid and mid not in used:
                used.add(mid)
                parts.append({"media_id": mid, "name": handle})
            else:
                push_text(handle)               # nhắc lại → hạ xuống chữ thường
        push_text((prompt or "")[pos:])
        for mid in list(by_handle.values()) + loose:
            if mid not in used:
                used.add(mid)
                push_text(" ")
                parts.append({"media_id": mid, "name": ""})
        return parts if any("media_id" in p for p in parts) else []

    async def _crop(self, media_id: str, ratio: int):
        """Khung cắt cho một ảnh đầu vào — cần kích thước thật của nó.

        Không đọc được kích thước thì trả None (không cắt) thay vì đoán: cắt sai chỗ còn
        tệ hơn không cắt, vì nó xén mất nội dung mà không mã lỗi nào báo.
        """
        try:
            rec = await self.boq.get_media(media_id)
        except Exception:
            return None
        size = ops._dig(rec, 0, 6, 2) or ops._dig(rec, 6, 2)
        if isinstance(size, list) and len(size) == 2 and all(size):
            return ops.crop_rect(size[0], size[1], ratio)
        return None

    @_soft
    async def upscale_video(self, media_id, scene_id, aspect_ratio=None,
                            resolution=None, project_id="", user_paygate_tier=None,
                            workflow_id=None) -> dict:
        four_k = (resolution or "").endswith("4K")
        wf = workflow_id or await self._workflow_of(media_id) or media_id
        out = await self.boq.upscale_video(media_id, wf, project_id, four_k,
                                           video_ratio(aspect_ratio))
        return _ok({"operations": [{"operation": {"name": out["media_id"]},
                                    "status": _PENDING}],
                    "media": [{"name": out["media_id"]}]})

    @_soft
    async def check_video_status(self, media: list[dict]) -> dict:
        """Poll. Trả HÌNH DẠNG của contract mới bên đường cũ (`media[]` + trạng thái).

        BOQ báo HỎNG đầy đủ, kèm mã và lý do — nên `videopoll` bỏ cuộc ngay khi Flow từ
        chối thay vì chờ hết 420 giây. Trước đây tôi kết luận nhầm là nó không phân biệt
        được, chỉ vì chưa từng bắt được lượt nào hỏng.

        Mã LẠ vẫn coi là chưa xong (chờ tiếp), cố ý: đã gặp ba mã "đang chạy" khác nhau
        nên nhiều khả năng còn mã chưa gặp, và báo hỏng oan thì studio tạo lại một bản
        nữa — tính tiền hai lần.
        """
        out = []
        for item in media or []:
            mid = item.get("name")
            status, reasons = _PENDING, []
            try:
                _done, wf = await self.boq.poll(mid)
            except Exception:
                wf = None
            if wf is not None:
                if ops.workflow_failed(wf):
                    code, reasons = ops.workflow_failure(wf)
                    status = _FAIL
                    reasons = reasons or ([code] if code else [])
                elif ops.workflow_done(wf):
                    status = _OK
            st = {"mediaGenerationStatus": status}
            if reasons:
                st["failureReasons"] = reasons
            out.append({"name": mid, "projectId": item.get("projectId"),
                        "mediaMetadata": {"mediaStatus": st}})
        return _ok({"media": out})

    # ---- đọc + dự án ----

    @_soft
    async def get_credits(self) -> dict:
        c = await self.boq.credits()
        return _ok({"credits": c, "remainingCredits": c})

    @_soft
    async def get_direct_media(self, primary_media_id: str) -> dict:
        """media_id → URL mới. Hình dạng `{"redirected": true, "url": …}` như đường cũ."""
        rec = await self.boq.get_media(primary_media_id)
        blob = json.dumps(rec, ensure_ascii=False)
        urls = re.findall(r'https://[^"\\]+', blob)
        video = [u for u in urls if "/video/" in u]
        image = [u for u in urls if "/image/" in u]
        url = (video or image or [None])[0]
        return _ok({"redirected": bool(url), "url": url})

    @_soft
    async def get_media(self, media_id: str) -> dict:
        return _ok({"media": await self.boq.get_media(media_id)})

    async def validate_media_id(self, media_id: str) -> bool:
        try:
            await self.boq.get_media(media_id)
            return True
        except Exception:
            return False

    @_soft
    async def get_project(self, project_id: str) -> dict:
        return _ok({"project": await self.boq.get_project(project_id)})

    @_soft
    async def get_projects(self) -> dict:
        """ĐI HẾT MỌI TRANG — đường cũ chỉ lấy trang đầu rồi dừng."""
        return _ok({"projects": await self.boq.list_projects()})

    @_soft
    async def create_project(self, project_title: str, tool_name="PINHOLE") -> dict:
        pid = await self.boq.create_project(project_title)
        return _ok({"projectId": pid, "project": {"projectId": pid,
                                                  "projectTitle": project_title}})

    @_soft
    async def change_display_name(self, media_name_id, project_id, display_name) -> dict:
        await self.boq.rename_workflow(media_name_id, project_id, display_name)
        return _ok({"ok": True})

    @_soft
    async def change_project_cover(self, project_id, media_name_id) -> dict:
        await self.boq.set_project_cover(project_id, "", media_name_id)
        return _ok({"ok": True})
