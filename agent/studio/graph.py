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
from agent.studio import db, media_store, brain, assembler

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


def _ancestors(node_id: str, edges: list[dict]) -> set[str]:
    """All nodes that can reach node_id (its upstream chain), including node_id itself."""
    rev: dict[str, list[str]] = {}
    for e in edges:
        rev.setdefault(e.get("target"), []).append(e.get("source"))
    seen: set[str] = set()
    stack = [node_id]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        for s in rev.get(x, []):
            if s:
                stack.append(s)
    return seen


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


def _generated_media_id(payload, exclude=None):
    """The GENERATED image id from a generate/edit response. An edit passes the source as a
    BASE_IMAGE input and Flow echoes it back in `media`, so we scan ALL items for a
    generatedImage.mediaId and skip the source (`exclude`) — taking media[0] blindly would
    return the input image. Falls back to a raw media `name` if no generatedImage is present."""
    media = payload.get("media") or []
    found = []
    for m in media:
        if not isinstance(m, dict):
            continue
        mid = ((m.get("image") or {}).get("generatedImage") or {}).get("mediaId")
        if mid and mid != exclude:
            found.append(mid)
    if found:
        return found[-1]            # the generated result comes after any echoed inputs
    for m in media:                 # fallback: first raw media id that isn't the source
        name = m.get("name") if isinstance(m, dict) else None
        if name and name != exclude:
            return name
    return None


async def _img_gen_retry(call, pid, exclude=None):
    """Run an image-producing Flow call, VERIFY a media was made + downloaded, and retry
    on content-policy blocks / transient failures. Returns (media_id, web_path). `exclude`
    is the edit's source id, skipped so the result isn't the (echoed) input image."""
    last = ""
    for attempt in range(_GRAPH_IMG_RETRIES):
        res = await call()
        if res.get("error"):
            last = str(res["error"])
        else:
            p = res.get("data", res)
            mid = _generated_media_id(p, exclude)
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


def _reuse_locked(data: dict, ext: str, handle: str):
    """If a gen node is locked and already has a result, return its stored output so a
    full run won't regenerate media the user is happy with. Else None."""
    mid = data.get("result_media_id")
    if data.get("locked") and mid:
        return {"media_id": mid, "web": data.get("result_web"), "ext": ext, "handle": handle,
                "_reused": True}
    return None


async def run_graph(graph: dict, target: dict, project: dict, kind: str,
                    only_node: str | None = None) -> dict:
    """Execute the graph; return {media_id, image_path|video_path} of the Output.

    only_node: when set, run only that node + its upstream chain and return its media
    (no Output node required, target not modified) — used by the per-node "tạo nhanh".
    Locked gen nodes reuse their stored result instead of regenerating (except the node
    explicitly requested via only_node, which always regenerates)."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        raise GraphError("Đồ thị rỗng")
    if only_node and not any(n.get("id") == only_node for n in nodes):
        raise GraphError("Không tìm thấy node cần tạo")
    client = get_flow_client()
    if not client.connected:
        raise GraphError("Extension chưa kết nối")

    outputs: dict[str, dict] = {}        # node_id -> output dict
    pid = project["id"]
    flow_pid = project["flow_project_id"]
    final = None
    allowed = _ancestors(only_node, edges) if only_node else None

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
        if allowed is not None and nid not in allowed:
            continue  # per-node run: only this node + its upstream chain
        inp = merged_inputs(nid)

        # Locked gen nodes reuse their stored result (the explicitly-requested only_node
        # always regenerates).
        if t in ("image", "editImage", "video") and nid != only_node:
            reused = _reuse_locked(data, "mp4" if t == "video" else "png",
                                   "video" if t == "video" else "image")
            if reused:
                if not reused.get("web") and reused.get("media_id"):
                    reused["web"] = await media_store.ensure_local(
                        reused["media_id"], pid, reused["ext"])
                outputs[nid] = reused
                continue

        if t == "prompt":
            outputs[nid] = {"text": data.get("text", "")}

        elif t == "source":
            # A source node bound to an entity (entity_id) must use the entity's CURRENT image,
            # so regenerating that entity propagates into the graph instead of using the stale
            # media_id snapshotted when the node was created. Plain (uploaded) sources keep their
            # stored media_id.
            mid = data.get("media_id")
            web = data.get("web")
            handle = data.get("label") or "source"
            eid = data.get("entity_id")
            if eid:
                ent = await db.query_one(
                    "SELECT name, media_id, image_path FROM entity WHERE id=?", (eid,))
                if ent and ent.get("media_id"):
                    mid, web = ent["media_id"], ent.get("image_path")
                    handle = ent.get("name") or handle
            if not web and mid:
                web = await media_store.ensure_local(mid, pid)
            outputs[nid] = {"media_id": mid, "web": web, "ext": "png", "handle": handle}

        elif t == "refs":
            ids = data.get("entity_ids") or []
            rows = await db.query_all("SELECT * FROM entity WHERE project_id=?", (pid,))
            by_id = {r["id"]: r for r in rows}
            refs = [{"handle": by_id[i]["name"], "media_id": by_id[i]["media_id"]}
                    for i in ids if by_id.get(i) and by_id[i].get("media_id")][:10]
            outputs[nid] = {"references": refs}

        elif t == "image":
            body = inp["text"] or data.get("text") or ""
            if kind == "entity" and target.get("type"):
                # Entity reference: apply the SAME per-type sheet rule as quick-gen so a
                # node-built reference matches (e.g. a location comes out as the 2x2 grid,
                # not a single plain view).
                img_prompt = brain.compose_prompt(project, brain.ref_image_prompt(
                    target["type"], target.get("name") or "", body))
            else:
                # Shot frame: single-frame guard (don't copy the location grid layout) so a
                # node-built frame matches the storyboard table.
                img_prompt = brain.compose_prompt(project, body, single_frame=(kind == "shot"))
            mid, web = await _img_gen_retry(lambda: client.generate_images(
                prompt=img_prompt,
                project_id=flow_pid,
                aspect_ratio=_img_aspect(project, data),
                user_paygate_tier=project["paygate_tier"],
                references=inp["references"] or None,
                image_model=_img_model(project, data)), pid)
            outputs[nid] = {"media_id": mid, "web": web, "ext": "png", "handle": "image"}

        elif t == "editImage":
            src = inp["media_id"]
            if not src:
                raise GraphError("editImage cần ảnh nguồn")
            # The edit prompt is used VERBATIM (no compose_prompt wrapping) — the user's exact
            # instruction edits the source. `exclude=src` skips the echoed input so the result
            # is the edited image, not the original.
            mid, web = await _img_gen_retry(lambda: client.edit_image(
                inp["text"] or data.get("text") or "", src, flow_pid,
                aspect_ratio=_img_aspect(project, data),
                user_paygate_tier=project["paygate_tier"]), pid, exclude=src)
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

    # node_id -> {web, media_id, ext}; the frontend uses this to fill previews and to
    # remember each gen node's media_id (so a locked node can be reused on the next run).
    node_outputs = {k: {"web": o.get("web"), "media_id": o.get("media_id"),
                        "ext": o.get("ext", "png")}
                    for k, o in outputs.items() if o.get("web")}

    if only_node:
        o = outputs.get(only_node) or {}
        if not o.get("media_id"):
            raise GraphError("Node này không tạo ra ảnh/video")
        return {"media_id": o.get("media_id"), "path": o.get("web"),
                "ext": o.get("ext", "png"), "node_outputs": node_outputs,
                "only_node": only_node}

    if not any(n.get("type") == "output" for n in nodes):
        raise GraphError("Đồ thị phải có node Output để chỉ định kết quả")
    if not final or not final.get("media_id"):
        raise GraphError("Node Output chưa được nối tới một node tạo ảnh/video có kết quả")

    # apply to target
    web = final.get("web") or await media_store.ensure_local(
        final["media_id"], pid, final.get("ext", "png"))
    display_path = web  # what the entity/shot SHOWS (may differ from the raw media, e.g. labels)
    if kind == "entity":
        await db.update("entity", target["id"], {
            "media_id": final["media_id"], "primary_media_id": final["media_id"],
            "image_path": web, "updated_at": db.now()})
        # A location's reference is a 2x2 grid → overlay the position labels for display,
        # same as the quick-gen path (media_id stays the clean grid).
        if target.get("type") == "location" and web:
            src = media_store.MEDIA_DIR / web.replace("/media/", "", 1)
            if src.exists():
                out_rel = f"{pid}/loc_{target['id']}_labeled.png"
                ok = await asyncio.to_thread(
                    assembler.label_quadrants, src, media_store.MEDIA_DIR / out_rel,
                    brain.LOCATION_GRID_LABELS, assembler._caption_font())
                if ok:
                    display_path = f"/media/{out_rel}"
                    await db.update("entity", target["id"], {"image_path": display_path})
    else:
        col = "video" if final.get("ext") == "mp4" else "image"
        await db.update("shot", target["id"], {
            f"{col}_media_id": final["media_id"], f"{col}_primary_id": final["media_id"],
            f"{col}_path": web, "updated_at": db.now()})
    return {"media_id": final["media_id"], "path": web, "ext": final.get("ext", "png"),
            "display_path": display_path, "node_outputs": node_outputs}
