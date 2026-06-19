"""Node-graph executor for the Studio Node Editor (video-app.md §2.9).

A graph is {nodes:[{id,type,data}], edges:[{source,target}]}. We topo-sort, run each
node (mapping to existing Flow/agent ops), and feed each node the merged outputs of its
upstream nodes. The Output node applies the final media to the target shot/entity.

Self-contained (calls flow_client/media_store directly) to avoid importing the router.
"""
import asyncio
import logging
import random
import time as _t

from agent.config import IMAGE_MODELS
from agent.services.flow_client import get_flow_client
from agent.studio import db, media_store, brain

logger = logging.getLogger(__name__)


class GraphError(Exception):
    pass


def _topo_sort(nodes: list[dict], edges: list[dict]) -> list[dict]:
    by_id = {n["id"]: n for n in nodes}
    indeg = {n["id"]: 0 for n in nodes}
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s in by_id and t in by_id:
            adj[s].append(t)
            indeg[t] += 1
    queue = [nid for nid, d in indeg.items() if d == 0]
    order = []
    while queue:
        nid = queue.pop(0)
        order.append(by_id[nid])
        for nb in adj[nid]:
            indeg[nb] -= 1
            if indeg[nb] == 0:
                queue.append(nb)
    if len(order) != len(nodes):
        raise GraphError("Đồ thị có chu trình (cycle)")
    return order


def _upstream_ids(node_id: str, edges: list[dict]) -> list[str]:
    return [e["source"] for e in edges if e.get("target") == node_id]


from agent.config import OMNI_FLASH_MODELS

# Friendly aspect tokens used by the node UI → Flow enums.
_IMG_ASPECT = {"16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
               "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
               "1:1": "IMAGE_ASPECT_RATIO_SQUARE"}
_VID_ASPECT = {"16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
               "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT"}


def _img_model(project: dict, data: dict | None = None) -> str | None:
    name = (data or {}).get("model") or project.get("image_model")
    return IMAGE_MODELS.get(name, name) if name else None


def _img_aspect(project: dict, data: dict | None = None) -> str:
    a = (data or {}).get("aspect")
    if a in _IMG_ASPECT:
        return _IMG_ASPECT[a]
    return (project.get("aspect_ratio") or "").replace(
        "VIDEO_ASPECT_RATIO_", "IMAGE_ASPECT_RATIO_") or "IMAGE_ASPECT_RATIO_LANDSCAPE"


def _vid_aspect(project: dict, data: dict | None = None) -> str:
    a = (data or {}).get("aspect")
    if a in _VID_ASPECT:
        return _VID_ASPECT[a]
    return project.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"


_GRAPH_IMG_RETRIES = 3
_GRAPH_VID_RETRIES = 2


def _deep_find(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _deep_find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find(v, key)
            if r is not None:
                return r
    return None


def _block_reason(payload):
    for k in ("raiFilteredReason", "filteredReason", "raiFilterReason", "blockReason"):
        v = _deep_find(payload, k)
        if v:
            return str(v)
    return None


async def _img_gen_retry(call, pid):
    """Run an image-producing Flow call, VERIFY a media was made + downloaded, and retry
    on content-policy blocks / transient failures. Returns (media_id, web_path)."""
    last = ""
    for attempt in range(_GRAPH_IMG_RETRIES):
        res = await call()
        if res.get("error"):
            last = str(res["error"])
        else:
            p = res.get("data", res)
            mid = (p.get("media") or [{}])[0].get("image", {}).get("generatedImage", {}).get("mediaId")
            if mid:
                web = await media_store.ensure_local(mid, pid)
                if web:
                    return mid, web
                last = "tải ảnh lỗi"
            else:
                last = _block_reason(p) or "Flow không trả media (có thể bị chặn)"
        if attempt < _GRAPH_IMG_RETRIES - 1:
            await asyncio.sleep(random.uniform(2, 5))
    raise GraphError(f"Tạo ảnh thất bại sau {_GRAPH_IMG_RETRIES} lần: {last}")


async def _vid_gen_retry(submit, scene_key, pid):
    """Submit a video, poll, download — verify the clip exists and retry on failure.
    Returns (media_id, web_path)."""
    client = get_flow_client()
    last = ""
    for attempt in range(_GRAPH_VID_RETRIES):
        res = await submit()
        if res.get("error"):
            last = str(res["error"])
        else:
            p = res.get("data", res)
            mid = (p.get("media") or [{}])[0].get("name")
            if not mid:
                last = _block_reason(p) or "Flow không trả media"
            else:
                url = await _poll_video(client, mid, scene_key)
                if not url:
                    last = "video chưa xong trong thời gian chờ"
                else:
                    web = await media_store.save_from_url(mid, pid, "mp4", url)
                    if web:
                        return mid, web
                    last = "tải video lỗi"
        if attempt < _GRAPH_VID_RETRIES - 1:
            await asyncio.sleep(random.uniform(5, 10))
    raise GraphError(f"Tạo video thất bại sau {_GRAPH_VID_RETRIES} lần: {last}")


async def _poll_video(client, media_id, scene_key, timeout=240, interval=8):
    op = {"operation": {"name": media_id}, "sceneId": scene_key}
    deadline = _t.monotonic() + timeout
    while _t.monotonic() < deadline:
        await asyncio.sleep(interval)
        st = await client.check_video_status([op])
        data = st.get("data", st)
        ops = data.get("operations") or []
        if ops:
            v = ops[0].get("operation", {}).get("metadata", {}).get("video", {})
            if v.get("fifeUrl"):
                return v["fifeUrl"]
    return None


async def run_graph(graph: dict, target: dict, project: dict, kind: str) -> dict:
    """Execute the graph; return {media_id, image_path|video_path} of the Output."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        raise GraphError("Đồ thị rỗng")
    client = get_flow_client()
    if not client.connected:
        raise GraphError("Extension chưa kết nối")

    outputs: dict[str, dict] = {}        # node_id -> output dict
    pid = project["id"]
    flow_pid = project["flow_project_id"]
    final = None

    def merged_inputs(nid):
        """Collect from upstream nodes: text, reference images (refs + any source/generated
        media), the best start image for i2v, and the latest produced media of ANY kind
        (so an Output node can pick up an image OR a video result)."""
        text = None
        refs: list[dict] = []
        start = None
        start_ext = "png"
        result = result_web = None
        result_ext = "png"
        for up in _upstream_ids(nid, edges):
            o = outputs.get(up, {})
            if o.get("text"):
                text = o["text"]
            for r in o.get("references", []):
                refs.append(r)
            if o.get("media_id"):
                refs.append({"handle": o.get("handle", "source"), "media_id": o["media_id"]})
                result = o["media_id"]
                result_ext = o.get("ext", "png")
                result_web = o.get("web")
                if o.get("ext", "png") != "mp4":   # only images can be a start frame
                    start = o["media_id"]
                    start_ext = o.get("ext", "png")
        seen, uniq = set(), []
        for r in refs:
            if r.get("media_id") and r["media_id"] not in seen:
                uniq.append(r)
                seen.add(r["media_id"])
        return {"text": text, "references": uniq[:10], "media_id": start, "ext": start_ext,
                "result": result, "result_ext": result_ext, "result_web": result_web}

    for node in _topo_sort(nodes, edges):
        t = node.get("type")
        data = node.get("data") or {}
        nid = node["id"]
        inp = merged_inputs(nid)

        if t == "prompt":
            outputs[nid] = {"text": data.get("text", "")}

        elif t == "source":
            mid = data.get("media_id")
            web = data.get("web") or (await media_store.ensure_local(mid, pid) if mid else None)
            outputs[nid] = {"media_id": mid, "web": web, "ext": "png",
                            "handle": data.get("label") or "source"}

        elif t == "refs":
            ids = data.get("entity_ids") or []
            rows = await db.query_all("SELECT * FROM entity WHERE project_id=?", (pid,))
            by_id = {r["id"]: r for r in rows}
            refs = [{"handle": by_id[i]["name"], "media_id": by_id[i]["media_id"]}
                    for i in ids if by_id.get(i) and by_id[i].get("media_id")][:10]
            outputs[nid] = {"references": refs}

        elif t == "image":
            body = inp["text"] or data.get("text") or ""
            mid, web = await _img_gen_retry(lambda: client.generate_images(
                prompt=brain.compose_prompt(project, body), project_id=flow_pid,
                aspect_ratio=_img_aspect(project, data),
                user_paygate_tier=project["paygate_tier"],
                references=inp["references"] or None,
                image_model=_img_model(project, data)), pid)
            outputs[nid] = {"media_id": mid, "web": web, "ext": "png", "handle": "image"}

        elif t == "editImage":
            src = inp["media_id"]
            if not src:
                raise GraphError("editImage cần ảnh nguồn")
            mid, web = await _img_gen_retry(lambda: client.edit_image(
                inp["text"] or data.get("text") or "", src, flow_pid,
                aspect_ratio=_img_aspect(project, data),
                user_paygate_tier=project["paygate_tier"]), pid)
            outputs[nid] = {"media_id": mid, "web": web, "ext": "png", "handle": "image"}

        elif t == "video":
            prompt = inp["text"] or data.get("text") or ""
            aspect_v = _vid_aspect(project, data)
            kind_v = (data.get("model") or "omni").lower()
            if kind_v == "omni" or kind_v in OMNI_FLASH_MODELS.values():
                ref_ids = [r["media_id"] for r in inp["references"]]
                if not ref_ids and inp["media_id"]:
                    ref_ids = [inp["media_id"]]
                if not ref_ids:
                    raise GraphError("Omni Flash cần ít nhất 1 ảnh tham chiếu/nguồn")
                submit = lambda: client.generate_video_omni(
                    prompt=prompt, project_id=flow_pid, reference_media_ids=ref_ids,
                    duration_s=int(data.get("duration") or 8), aspect_ratio=aspect_v,
                    user_paygate_tier=project["paygate_tier"],
                    references=inp["references"] or None)
            else:   # Veo i2v — needs a start frame
                if not inp["media_id"]:
                    raise GraphError("Veo i2v cần ảnh start (nối từ Nguồn ảnh / Tạo ảnh)")
                start = inp["media_id"]
                submit = lambda: client.generate_video(
                    start_image_media_id=start, prompt=prompt,
                    project_id=flow_pid, scene_id=target["id"],
                    aspect_ratio=aspect_v, user_paygate_tier=project["paygate_tier"])
            mid, web = await _vid_gen_retry(submit, target["id"], pid)
            outputs[nid] = {"media_id": mid, "web": web, "ext": "mp4", "handle": "video"}

        elif t == "output":
            # The Output node designates the final result: whatever media flows into it.
            if inp["result"]:
                final = {"media_id": inp["result"], "web": inp["result_web"],
                         "ext": inp["result_ext"]}
                outputs[nid] = {"media_id": inp["result"], "web": inp["result_web"],
                                "ext": inp["result_ext"]}

        else:
            logger.warning("Unknown node type: %s", t)

    if not any(n.get("type") == "output" for n in nodes):
        raise GraphError("Đồ thị phải có node Output để chỉ định kết quả")
    if not final or not final.get("media_id"):
        raise GraphError("Node Output chưa được nối tới một node tạo ảnh/video có kết quả")

    node_outputs = {k: o.get("web") for k, o in outputs.items() if o.get("web")}

    # apply to target
    web = final.get("web") or await media_store.ensure_local(
        final["media_id"], pid, final.get("ext", "png"))
    if kind == "entity":
        await db.update("entity", target["id"], {
            "media_id": final["media_id"], "primary_media_id": final["media_id"],
            "image_path": web, "updated_at": db.now()})
    else:
        col = "video" if final.get("ext") == "mp4" else "image"
        await db.update("shot", target["id"], {
            f"{col}_media_id": final["media_id"], f"{col}_primary_id": final["media_id"],
            f"{col}_path": web, "updated_at": db.now()})
    return {"media_id": final["media_id"], "path": web, "ext": final.get("ext", "png"),
            "node_outputs": node_outputs}
