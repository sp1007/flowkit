"""Đồ thị mặc định dựng ở server phải KHỚP bản `defaultGraph()` của Node Editor.

Vì sao phải khoá bằng test: người dùng chỉ ra rằng mọi ảnh/video đều cần đồ thị, vì họ có
thể vào tinh chỉnh bất cứ lúc nào — và hai đường sinh khác nhau thì kết quả lệch nhau mà
không ai biết ảnh cũ đã sinh bằng đường nào.

Trước đây `defaultGraph()` chỉ có ở TSX, server không có gì tương đương nên khi hàng chưa
có đồ thị thì nó rơi về một đường dựng prompt riêng. Đo trên kho thật: 25/12005 shot có
đồ thị, tức gần như MỌI ảnh đang đi đường kia. Nay server dựng đồ thị mặc định và chạy
chính nó, còn giao diện lấy đồ thị ấy qua endpoint thay vì tự dựng.

Các phép so dưới đây đối chiếu từng chi tiết mà bản TSX tạo ra — id node, dây nối, và ba
quyết định dễ sai: có gieo node ảnh nguồn hay không, tỉ lệ khung của ảnh tham chiếu, và
hai node bọc prompt.
"""
from agent.studio.graph import default_graph, output_gen_node, _seed_from_row


def _ids(g):
    return sorted(n["id"] for n in g["nodes"])


def _edges(g):
    return sorted((e["source"], e["target"]) for e in g["edges"])


def _node(g, nid):
    return next(n for n in g["nodes"] if n["id"] == nid)


# ─── ảnh ─────────────────────────────────────────────────────

def test_do_thi_anh_khong_co_anh_tham_chieu():
    seed = {"kind": "shot", "goal": "image", "title": "s1", "prompt": "một con mèo",
            "ref_entity_ids": [], "image_src": ""}
    g = default_graph(seed, [], engine="", aspect="16:9")
    assert _ids(g) == ["i", "o", "p", "pf", "ph"]
    assert _edges(g) == [("i", "o"), ("p", "i"), ("pf", "i"), ("ph", "i")]
    # Node sinh nối vào Output phải tìm ra được, nếu không _gen_via_graph bó tay.
    assert output_gen_node(g) == "i"


def test_moi_anh_tham_chieu_mot_node_nguon():
    ents = [{"id": "e1", "name": "Mai", "media_id": "m1", "image_path": "/a.png"},
            {"id": "e2", "name": "Phố", "media_id": "m2", "image_path": "/b.png"},
            {"id": "e3", "name": "Chưa có ảnh", "media_id": None}]
    seed = {"kind": "shot", "goal": "image", "title": "s1", "prompt": "{Mai} ở {Phố}",
            "ref_entity_ids": ["e1", "e2", "e3"], "image_src": ""}
    g = default_graph(seed, ents, engine="", aspect="16:9")
    # e3 chưa có ảnh thì KHÔNG gieo node — một node Nguồn ảnh rỗng chẳng dùng được việc gì.
    assert _ids(g) == ["i", "o", "p", "pf", "ph", "src0", "src1"]
    assert _node(g, "src0")["data"]["media_id"] == "m1"
    assert _node(g, "src1")["data"]["entity_id"] == "e2"


def test_ti_le_anh_tham_chieu_theo_loai_asset():
    """Người và đồ vật lấy khung DỌC; bối cảnh giữ khung NGANG.

    Đây không phải chuyện thẩm mỹ: ảnh người ở khung ngang phí gần nửa ảnh vào nền hai bên
    và bóp nhỏ chủ thể, ít điểm ảnh trên khuôn mặt thì model lược nét.
    """
    for typ, want in (("character", "9:16"), ("prop", "9:16"), ("location", "16:9")):
        seed = {"kind": "entity", "goal": "image", "entity_type": typ, "title": "x",
                "prompt": "p", "ref_entity_ids": []}
        g = default_graph(seed, [], engine="", aspect="16:9")
        assert _node(g, "i")["data"]["aspect"] == want, typ


# ─── video ───────────────────────────────────────────────────

def test_do_thi_video_khong_co_frame():
    """Shot chưa có ảnh vẫn phải render được — Veo Lite và Omni đều làm text-to-video.

    Gieo node "Nguồn ảnh" rỗng ở đây là bắt người dùng xoá tay, và tệ hơn là làm đồ thị
    trông như đang cần một ảnh không tồn tại.
    """
    seed = {"kind": "shot", "goal": "video", "title": "s1", "prompt": "gió thổi",
            "ref_entity_ids": [], "image_media_id": None, "video_src": ""}
    g = default_graph(seed, [], engine="veo_lite", aspect="16:9")
    assert "src" not in _ids(g)
    assert _edges(g) == [("p", "v"), ("pf", "v"), ("ph", "v"), ("v", "o")]
    assert output_gen_node(g) == "v"


def test_do_thi_video_co_frame_thi_noi_vao():
    seed = {"kind": "shot", "goal": "video", "title": "s1", "prompt": "gió thổi",
            "ref_entity_ids": [], "image_media_id": "m9", "image_src": "/f.png",
            "video_src": ""}
    g = default_graph(seed, [], engine="veo_lite", aspect="9:16")
    assert _node(g, "src")["data"]["media_id"] == "m9"
    assert ("src", "v") in _edges(g)
    # Engine + tỉ lệ lấy từ ⚙ Cấu hình dự án, không phải hằng số.
    assert _node(g, "v")["data"]["model"] == "veo_lite"
    assert _node(g, "v")["data"]["aspect"] == "9:16"
    # KHÔNG đặt `duration` → độ dài lấy từ cấu hình dự án.
    assert "duration" not in _node(g, "v")["data"]


def test_luon_co_hai_node_boc_prompt():
    """Header/footer đi bằng NODE chứ không chèn ngầm — thiếu là prompt ra trần trụi."""
    for goal in ("image", "video"):
        seed = {"kind": "shot", "goal": goal, "title": "s", "prompt": "p",
                "ref_entity_ids": []}
        g = default_graph(seed, [], engine="veo_lite", aspect="16:9")
        assert {"ph", "pf"} <= set(_ids(g)), goal
        assert _node(g, "ph")["data"]["text"] == ""


# ─── suy seed từ hàng DB ─────────────────────────────────────

def test_seed_shot_video_gop_motion_va_visual():
    row = {"id": "s1", "title": "T", "motion_prompt": "chạy",
           "visual_prompt": "trên đồng cỏ", "description": "mô tả"}
    seed = _seed_from_row("shot", row, "video")
    assert seed["prompt"] == "chạy\n\ntrên đồng cỏ"


def test_seed_shot_anh_uu_tien_description():
    row = {"id": "s1", "title": "T", "description": "mô tả", "visual_prompt": "vp"}
    assert _seed_from_row("shot", row, "image")["prompt"] == "mô tả"


def test_seed_doc_duoc_ref_entity_ids_dang_chuoi_JSON():
    """Cột này lưu JSON trong DB; quên parse là mất sạch ảnh tham chiếu mà không báo lỗi."""
    row = {"id": "s1", "title": "T", "ref_entity_ids": '["e1","e2"]'}
    assert _seed_from_row("shot", row, "image")["ref_entity_ids"] == ["e1", "e2"]
    row_bad = {"id": "s1", "title": "T", "ref_entity_ids": "không phải JSON"}
    assert _seed_from_row("shot", row_bad, "image")["ref_entity_ids"] == []


def test_seed_entity():
    row = {"id": "e1", "name": "Mai", "type": "character",
           "description": "cô gái", "media_id": "m1", "image_path": "/a.png"}
    seed = _seed_from_row("entity", row, "image")
    assert (seed["kind"], seed["entity_type"], seed["prompt"]) == \
        ("entity", "character", "cô gái")
