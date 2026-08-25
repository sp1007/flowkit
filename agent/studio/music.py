"""Playlist nhạc của dự án — chế độ "music video".

Khác `project.bgm_path` (MỘT bài, trộn chìm dưới lời đọc, lặp cho đủ độ dài video): ở đây
nhạc là tiếng chính. Dự án có một danh sách bài phát nối tiếp nhau, cách nhau
`project.music_gap` giây im lặng, và TỔNG thời lượng playlist quyết định độ dài video —
hình được lặp lại cho phủ kín rồi cắt đúng lúc nhạc dứt.

Hình không gắn với từng bài: cả dãy scene/shot của dự án là một dòng hình chung trải lên
toàn bộ playlist (bài 2 có thể bắt đầu giữa một shot). Đây là lựa chọn có chủ đích — mỗi
bài một nhóm scene riêng sẽ buộc phải chia lại storyboard mỗi lần đổi thứ tự bài hát.
"""
import json
import logging
import os
from pathlib import Path

from agent.studio import db
from agent.studio.assembler import STUDIO_MEDIA_DIR, _run, probe_duration

logger = logging.getLogger(__name__)

# Định dạng nhạc chấp nhận (upload + tải từ Flow Music).
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac"}
DEFAULT_GAP = 3.0
# Hình ngắn hơn nhạc dưới ngưỡng này thì coi như đã phủ kín, khỏi lặp (tránh nối thêm một
# vòng chỉ để bù vài phần trăm giây).
FIT_TOLERANCE = 0.25


def track_dir(project_id: str) -> Path:
    return STUDIO_MEDIA_DIR / project_id / "music"


def web_path(track: dict) -> str | None:
    """Đường dẫn phát được trong trình duyệt (/studio-media/...) từ đường dẫn tuyệt đối đã
    lưu. None nếu file nằm ngoài thư mục studio_media (không phục vụ tĩnh được)."""
    p = (track.get("path") or "").strip()
    if not p:
        return None
    try:
        rel = Path(p).resolve().relative_to(STUDIO_MEDIA_DIR.resolve())
    except (ValueError, OSError):
        return None
    return "/studio-media/" + rel.as_posix()


def gap_of(project: dict) -> float:
    try:
        g = float(project.get("music_gap"))
    except (TypeError, ValueError):
        g = DEFAULT_GAP
    return max(0.0, g)


async def tracks(project_id: str) -> list[dict]:
    return await db.query_all(
        "SELECT * FROM music_track WHERE project_id=? ORDER BY idx, created_at", (project_id,))


def total_duration(rows: list[dict], gap: float) -> float:
    """Tổng thời lượng playlist = các bài + khoảng lặng GIỮA chúng (không có sau bài cuối)."""
    if not rows:
        return 0.0
    songs = sum(float(r.get("duration") or 0.0) for r in rows)
    return songs + gap * (len(rows) - 1)


def target_seconds(project: dict) -> float:
    """Độ dài mong muốn của cả video music, GIÂY (cột lưu bằng phút). 0 = không đặt đích."""
    try:
        return max(0.0, float(project.get("music_target_min") or 0.0) * 60.0)
    except (TypeError, ValueError):
        return 0.0


# Trần số lượt phát, chặn playlist toàn bài hỏng (duration 0) làm vòng lặp chạy mãi.
MAX_PLAYS = 2000


def playlist_plan(rows: list[dict], gap: float, target_s: float = 0.0) -> list[dict]:
    """Thứ tự phát THẬT của playlist: [{track, start, duration}] tính bằng giây.

    Không đặt đích (`target_s <= 0`) → đúng một lượt qua danh sách, y như trước.

    Có đích → lặp lại cả playlist cho tới khi CHẠM MỐC GẦN ĐÍCH NHẤT. Điểm cắt luôn nằm ở
    ranh giới giữa hai bài: bài hát không bao giờ bị cắt ngang, nên tổng chỉ XẤP XỈ đích —
    thà lệch vài chục giây còn hơn kết thúc giữa một câu hát. Luôn có ít nhất một bài, kể cả
    khi đích ngắn hơn bài đầu tiên.
    """
    rows = [r for r in rows if float(r.get("duration") or 0.0) > 0]
    if not rows:
        return []
    plan: list[dict] = []
    pos = 0.0
    k = 0
    while k < MAX_PLAYS:
        r = rows[k % len(rows)]
        dur = float(r["duration"])
        start = pos + (gap if plan else 0.0)
        end = start + dur
        if plan:
            if target_s <= 0:
                if k >= len(rows):        # hết một lượt → dừng
                    break
            # Dừng khi thêm bài này làm tổng RỜI XA đích hơn là dừng ngay tại đây.
            elif abs(pos - target_s) <= abs(end - target_s):
                break
        plan.append({"track": r, "start": start, "duration": dur})
        pos = end
        k += 1
    return plan


def plan_duration(plan: list[dict]) -> float:
    """Tổng thời lượng của một kế hoạch phát (tới lúc bài cuối dứt, không có khoảng lặng đuôi)."""
    return (plan[-1]["start"] + plan[-1]["duration"]) if plan else 0.0


async def next_idx(project_id: str) -> int:
    row = await db.query_one(
        "SELECT MAX(idx) AS m FROM music_track WHERE project_id=?", (project_id,))
    return (row["m"] + 1) if row and row["m"] is not None else 0


async def add_track(project_id: str, src: Path, *, title: str, source: str,
                    audio_url: str | None = None, meta: dict | None = None) -> dict:
    """Ghi một file nhạc đã nằm sẵn trong thư mục music của dự án thành một track."""
    dur = await probe_duration(src)
    row = {
        "id": db.new_id(), "project_id": project_id, "idx": await next_idx(project_id),
        "title": title or src.stem, "path": str(src), "duration": dur,
        "source": source, "audio_url": audio_url,
        "meta_json": json.dumps(meta) if meta else None,
        "created_at": db.now(),
    }
    await db.insert("music_track", row)
    return row


async def delete_track(track: dict) -> None:
    p = (track.get("path") or "").strip()
    if p:
        Path(p).unlink(missing_ok=True)
    await db.delete("music_track", track["id"])


async def reorder(project_id: str, ids: list[str]) -> None:
    """Đặt lại thứ tự phát theo đúng danh sách id truyền vào; id lạ bị bỏ qua, bài không có
    trong danh sách dồn xuống cuối (giữ thứ tự cũ)."""
    rows = await tracks(project_id)
    by_id = {r["id"]: r for r in rows}
    ordered = [i for i in ids if i in by_id]
    ordered += [r["id"] for r in rows if r["id"] not in set(ordered)]
    for i, tid in enumerate(ordered):
        await db.update("music_track", tid, {"idx": i})


# ─── Dựng dải âm thanh ──────────────────────────────────────

async def build_soundtrack(project: dict) -> tuple[Path, float] | None:
    """Nối playlist thành MỘT file audio (bài + khoảng lặng + bài …) → soundtrack.m4a.

    Trả (đường dẫn, thời lượng thật đo lại) hoặc None nếu dự án chưa có bài nào. Nối bằng
    filter `concat` chứ không phải demuxer concat vì các bài có thể khác codec/sample rate
    (bài sinh từ Flow Music là m4a, bài người dùng tải lên có thể là mp3/wav)."""
    pid = project["id"]
    rows = [r for r in await tracks(pid) if (r.get("path") or "") and Path(r["path"]).exists()]
    if not rows:
        return None
    gap = gap_of(project)
    plan = playlist_plan(rows, gap, target_seconds(project))
    if not plan:
        return None
    out_dir = STUDIO_MEDIA_DIR / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "soundtrack.m4a"

    # Mỗi FILE vào đúng một lần rồi `asplit` ra đủ số lượt phát: đặt đích 60 phút với bài 30
    # giây là 120 lượt, mở 120 input chỉ để phát lại cùng một file sẽ đội dòng lệnh lên quá
    # giới hạn của Windows.
    files: list[str] = []
    uses: dict[str, int] = {}
    for e in plan:
        f = str(Path(e["track"]["path"]))
        if f not in uses:
            files.append(f)
            uses[f] = 0
        uses[f] += 1

    args: list[str] = ["ffmpeg", "-y"]
    for f in files:
        args += ["-i", f]
    parts: list[str] = []
    for i, f in enumerate(files):
        outs = "".join(f"[a{i}_{j}]" for j in range(uses[f]))
        parts.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp:"
                     f"channel_layouts=stereo,asplit={uses[f]}{outs}")
    taken: dict[str, int] = {f: 0 for f in files}
    labels: list[str] = []
    for k, e in enumerate(plan):
        f = str(Path(e["track"]["path"]))
        i = files.index(f)
        labels.append(f"[a{i}_{taken[f]}]")
        taken[f] += 1
        if gap > 0 and k < len(plan) - 1:
            parts.append(f"anullsrc=channel_layout=stereo:sample_rate=44100,"
                         f"atrim=duration={gap:.3f}[g{k}]")
            labels.append(f"[g{k}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")
    args += ["-filter_complex", ";".join(parts), "-map", "[out]",
             "-c:a", "aac", "-b:a", "192k", str(out)]
    await _run(args)
    return out, await probe_duration(out)


async def fit_video_to_soundtrack(video: Path, sound: Path, target: float, out: Path) -> dict:
    """Ghép `video` với `sound` sao cho ra đúng `target` giây: hình ngắn hơn thì LẶP lại từ
    đầu cho tới khi phủ kín, dài hơn thì cắt bớt phần đuôi. Tiếng của video bị bỏ (nhạc là
    tiếng chính — xem quyết định ở docstring module).

    Thử `-c:v copy` trước vì rẻ; nếu bản copy lệch thời lượng quá nhiều (cắt phải rơi vào
    keyframe) thì encode lại cho đúng.
    """
    src = await probe_duration(video)
    loops = 0
    if src > 0 and src < target - FIT_TOLERANCE:
        # -stream_loop chỉ áp cho input NGAY SAU nó → nhạc không bị lặp theo.
        loops = max(1, int(target // src) + 1)

    async def _mux(copy: bool) -> float:
        args = ["ffmpeg", "-y"]
        if loops:
            args += ["-stream_loop", str(loops)]
        args += ["-i", str(video), "-i", str(sound),
                 "-map", "0:v:0", "-map", "1:a:0", "-t", f"{target:.3f}"]
        args += ["-c:v", "copy"] if copy else [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
        args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
        await _run(args)
        return await probe_duration(out)

    got = await _mux(copy=True)
    reencoded = False
    if abs(got - target) > 0.5:
        logger.info("fit copy lệch %.2fs (%.2f/%.2f) → encode lại", got - target, got, target)
        got = await _mux(copy=False)
        reencoded = True
    return {"duration": got, "target": target, "source_duration": src,
            "loops": loops, "reencoded": reencoded}


async def status(project: dict) -> dict:
    """Số liệu cho UI: playlist dài bao nhiêu, hình đang có bao nhiêu, thiếu/thừa bao nhiêu.

    `video_duration` ưu tiên đo file final.mp4 đã ghép; chưa ghép lần nào thì ước lượng từ
    cột `shot.duration` (kế hoạch) — đủ để biết còn thiếu bao nhiêu giây trước khi ghép."""
    pid = project["id"]
    rows = await tracks(pid)
    gap = gap_of(project)
    # `music` = độ dài THẬT của dải nhạc sẽ dựng (đã tính lặp theo đích), vì đó mới là con số
    # quyết định độ dài video; `playlist` = một lượt qua danh sách, để đối chiếu.
    target = target_seconds(project)
    plan = playlist_plan(rows, gap, target)
    music = plan_duration(plan)
    playlist = total_duration(rows, gap)

    final = STUDIO_MEDIA_DIR / pid / "final.mp4"
    video, measured = 0.0, False
    if final.exists():
        video = await probe_duration(final)
        measured = video > 0
    if not measured:
        est = await db.query_one(
            "SELECT SUM(COALESCE(duration, 0)) AS s FROM shot "
            "WHERE scene_id IN (SELECT id FROM scene WHERE project_id=?)", (pid,))
        video = float((est or {}).get("s") or 0.0)
    return {
        "tracks": [{**r, "web_path": web_path(r)} for r in rows],
        "gap": gap,
        "music_mode": bool(project.get("music_mode")),
        "music_duration": music,
        # Đích người dùng đặt (phút, 0 = không đặt) + kế hoạch phát thật để UI nói rõ playlist
        # phải lặp mấy lượt và lệch đích bao nhiêu (điểm cắt luôn rơi vào ranh giới bài).
        "target_min": round(target / 60.0, 2),
        "playlist_duration": playlist,
        "plays": len(plan),
        "video_duration": video,
        "video_measured": measured,
        # >0 = hình còn thiếu bấy nhiêu giây so với nhạc (sẽ được lặp để bù khi ghép).
        "shortfall": max(0.0, music - video),
    }


def safe_ext(name: str, default: str = ".m4a") -> str:
    ext = os.path.splitext((name or "").split("?")[0])[1].lower()
    return ext if ext in AUDIO_EXTS else default
