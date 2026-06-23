"""AI Agent CLI endpoints — chạy các agent CLI headless như subprocess.

Cho phép gọi các coding-agent CLI (Claude Code, Antigravity, ...) qua HTTP để
tự động hóa: viết script, sinh prompt, sửa file trong một thư mục làm việc.
Agent chạy non-interactive (headless), mặc định bypass permission nên có thể
ghi file / chạy lệnh tự do — chỉ expose trên 127.0.0.1.

Registry agent + cờ mặc định nằm ở agent/config.py (AI_AGENTS), override được
qua env nếu binary/cờ của CLI thay đổi.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.config import (
    AGENT_CLI_TIMEOUT,
    AGENT_PROMPT_ARG_MAX,
    AGENT_PTY_COLS,
    AGENT_PTY_ROWS,
    AGENT_SKIP_PERMISSIONS,
    AI_AGENTS,
)
from agent.services.pty_runner import PtyTimeout, run_pty

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])


# ─── Models ──────────────────────────────────────────────────

class RunRequest(BaseModel):
    agent: str                              # key trong AI_AGENTS (vd "claude")
    prompt: str                             # nội dung giao cho agent
    cwd: Optional[str] = None               # thư mục làm việc (mặc định: hiện tại)
    model: Optional[str] = None             # override model key của CLI
    timeout: Optional[float] = None         # giây; mặc định AGENT_CLI_TIMEOUT
    extra_args: Optional[list[str]] = None  # cờ thêm truyền thẳng cho CLI
    skip_permissions: Optional[bool] = None  # override bypass permission
    env: Optional[dict[str, str]] = None    # biến môi trường thêm cho tiến trình


# ─── Helpers ─────────────────────────────────────────────────

def _resolve_bin(cfg: dict) -> Optional[str]:
    """Trả đường dẫn binary đã resolve qua PATH, hoặc None nếu không tìm thấy."""
    return shutil.which(cfg["bin"])


def _build_command(cfg: dict, body: RunRequest,
                   cwd: str) -> tuple[list[str], Optional[str], Optional[str]]:
    """Dựng (argv, nội-dung-stdin, đường-dẫn-temp-prompt-cần-xoá).

    base_args được đặt CUỐI cùng (ngay trước prompt) vì một số CLI dùng cờ
    nhận-giá-trị như `-p <prompt>` — cờ phải kề ngay prompt, không để cờ khác
    chen vào giữa.

    prompt_mode "arg" mà prompt quá dài (chương dài) sẽ vượt giới hạn dòng lệnh
    Windows → ghi prompt ra temp file trong cwd và thay arg bằng một chỉ dẫn ngắn
    bảo agent đọc file đó (agent có quyền đọc file trong cwd).
    """
    argv = [cfg["bin"]]

    if body.model and cfg.get("model_flag"):
        argv += [cfg["model_flag"], body.model]

    skip = AGENT_SKIP_PERMISSIONS if body.skip_permissions is None else body.skip_permissions
    if skip:
        argv += cfg.get("skip_perm", [])

    if body.extra_args:
        argv += body.extra_args

    argv += cfg.get("base_args", [])

    if cfg.get("prompt_mode") == "arg":
        if len(body.prompt) > AGENT_PROMPT_ARG_MAX:
            fd, path = tempfile.mkstemp(prefix="flowkit_prompt_", suffix=".txt", dir=cwd)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(body.prompt)
            directive = (
                "Your full task is in this UTF-8 text file (read it completely):\n"
                f"{path}\n"
                "Do EXACTLY what that file asks and output ONLY what it requests "
                "(e.g. raw JSON if it asks for JSON). Do not mention this file."
            )
            argv.append(directive)
            return argv, None, path
        argv.append(body.prompt)
        return argv, None, None
    return argv, body.prompt, None  # stdin mode


async def _run_via_pty(body: "RunRequest", argv: list[str],
                       cwd: str, env: dict, timeout: float) -> dict:
    """Chạy agent TUI dưới PTY trong thread executor; trả cùng shape với /run."""
    started = time.monotonic()
    try:
        exit_code, output = await asyncio.to_thread(
            run_pty, argv, cwd=cwd, env=env, timeout=timeout,
            cols=AGENT_PTY_COLS, rows=AGENT_PTY_ROWS,
        )
    except PtyTimeout:
        raise HTTPException(504, f"Agent '{body.agent}' vượt quá timeout {timeout}s")
    except ModuleNotFoundError as e:
        raise HTTPException(
            500, f"Thiếu backend PTY ({e}). Cài 'pywinpty' (Windows): "
                 f"pip install pywinpty")
    except Exception as e:
        raise HTTPException(500, f"Lỗi chạy PTY cho '{body.agent}': {e}")

    duration = round(time.monotonic() - started, 2)
    logger.info("agent/run done (pty): %s exit=%s in %ss",
                body.agent, exit_code, duration)
    return {
        "ok": exit_code == 0,
        "agent": body.agent,
        "exit_code": exit_code,
        "stdout": output,
        "stderr": "",          # PTY gộp chung stdout/stderr
        "duration": duration,
        "cwd": cwd,
        "pty": True,
    }


# ─── Endpoints ───────────────────────────────────────────────

@router.get("/agents")
async def list_agents():
    """Liệt kê agent đã cấu hình + binary có sẵn trên máy hay không."""
    return {
        "skip_permissions_default": AGENT_SKIP_PERMISSIONS,
        "timeout_default": AGENT_CLI_TIMEOUT,
        "agents": [
            {
                "key": key,
                "bin": cfg["bin"],
                "available": _resolve_bin(cfg) is not None,
                "path": _resolve_bin(cfg),
                "prompt_mode": cfg.get("prompt_mode"),
                "supports_model": bool(cfg.get("model_flag")),
                "pty": bool(cfg.get("pty")),
            }
            for key, cfg in AI_AGENTS.items()
        ],
    }


@router.post("/run")
async def run_agent(body: RunRequest):
    """Chạy một agent CLI headless và trả về stdout/stderr/exit_code.

    Lỗi: 404 agent không tồn tại; 503 binary chưa cài; 400 cwd sai;
    504 quá timeout. Tiến trình bị kill khi timeout.
    """
    cfg = AI_AGENTS.get(body.agent)
    if cfg is None:
        raise HTTPException(404, f"Agent '{body.agent}' không tồn tại. "
                                 f"Có: {', '.join(AI_AGENTS)}")
    if _resolve_bin(cfg) is None:
        raise HTTPException(503, f"Binary '{cfg['bin']}' chưa cài hoặc không có trong PATH")

    cwd = body.cwd or os.getcwd()
    if not Path(cwd).is_dir():
        raise HTTPException(400, f"cwd không phải thư mục hợp lệ: {cwd}")

    argv, stdin_text, tmp_prompt = _build_command(cfg, body, cwd)
    timeout = body.timeout or AGENT_CLI_TIMEOUT
    proc_env = {**os.environ, **(body.env or {})}

    logger.info("agent/run: %s argv=%s cwd=%s timeout=%ss pty=%s prompt_file=%s",
                body.agent, argv, cwd, timeout, bool(cfg.get("pty")), bool(tmp_prompt))

    try:
        # Agent dạng TUI (vd agy) chỉ in ra terminal → chạy dưới PTY, bắt + strip ANSI.
        if cfg.get("pty"):
            return await _run_via_pty(body, argv, cwd, proc_env, timeout)

        started = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=proc_env,
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as e:
            raise HTTPException(503, f"Không khởi chạy được '{cfg['bin']}': {e}")

        stdin_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(504, f"Agent '{body.agent}' vượt quá timeout {timeout}s")

        duration = round(time.monotonic() - started, 2)
        out = stdout.decode("utf-8", "replace") if stdout else ""
        err = stderr.decode("utf-8", "replace") if stderr else ""
        logger.info("agent/run done: %s exit=%s in %ss", body.agent, proc.returncode, duration)

        return {
            "ok": proc.returncode == 0,
            "agent": body.agent,
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "duration": duration,
            "cwd": cwd,
        }
    finally:
        if tmp_prompt:
            try:
                os.unlink(tmp_prompt)
            except OSError:
                pass
