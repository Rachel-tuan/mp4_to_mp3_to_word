"""FFmpeg wrapper: the only place in the codebase that shells out to ffmpeg.

Design goals (see 二 in the spec):
  * absolute paths, -y, -nostdin
  * always check returncode
  * always capture stderr and surface the real ffmpeg error
  * one failing file must never affect the others (caller's job, but we
    make sure we never raise something uncatchable / leave zombie procs)
  * explicit overwrite policy for existing output files
  * never block a Tk mainloop -- this module has zero UI knowledge, it is
    called from a worker thread by core.pipeline
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("app.ffmpeg")

if sys.platform == "win32":
    _CREATE_NO_WINDOW = 0x08000000
else:
    _CREATE_NO_WINDOW = 0


class OverwritePolicy(str, Enum):
    SKIP = "skip"        # leave existing file alone, treat as already done
    OVERWRITE = "overwrite"
    FAIL = "fail"         # raise if the output already exists


class FFmpegError(RuntimeError):
    """Raised when ffmpeg exits non-zero or cannot be launched."""

    def __init__(self, message: str, returncode: Optional[int] = None, stderr: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class FFmpegResult:
    output_path: str
    skipped: bool = False  # True when we skipped because output already existed


def get_ffmpeg_path() -> str:
    """Resolve ffmpeg.exe next to the script, or next to the frozen exe."""
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # project root = parent of the "services" package
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    candidate = os.path.join(base_dir, exe_name)
    if os.path.isfile(candidate):
        return candidate
    # Fall back to whatever is on PATH.
    return exe_name


class FFmpegService:
    def __init__(self, ffmpeg_path: Optional[str] = None):
        self.ffmpeg_path = os.path.abspath(ffmpeg_path) if ffmpeg_path else get_ffmpeg_path()

    def extract_mp3(
        self,
        input_path: str,
        output_path: str,
        overwrite: OverwritePolicy = OverwritePolicy.SKIP,
        quality: str = "2",
    ) -> FFmpegResult:
        input_path = os.path.abspath(input_path)
        output_path = os.path.abspath(output_path)

        if not os.path.isfile(input_path):
            raise FFmpegError(f"输入文件不存在: {input_path}")

        if os.path.exists(output_path):
            if overwrite == OverwritePolicy.SKIP:
                logger.info("MP3 already exists, skipping ffmpeg: %s", output_path)
                return FFmpegResult(output_path=output_path, skipped=True)
            if overwrite == OverwritePolicy.FAIL:
                raise FFmpegError(f"输出文件已存在: {output_path}")
            # OVERWRITE: fall through, -y handles it.

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-nostdin",
            "-i", input_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-q:a", quality,
            output_path,
        ]
        logger.info("Running ffmpeg command: %s", " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except FileNotFoundError as e:
            raise FFmpegError(f"找不到 ffmpeg 可执行文件: {self.ffmpeg_path}") from e
        except OSError as e:
            raise FFmpegError(f"启动 ffmpeg 失败: {e}") from e

        stderr_text = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        logger.info("ffmpeg returncode=%s", proc.returncode)
        if stderr_text:
            logger.debug("ffmpeg stderr for %s:\n%s", input_path, stderr_text)

        if proc.returncode != 0:
            # Clean up a partial/corrupt output file so a retry doesn't see
            # a "valid" existing file.
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
            raise FFmpegError(
                f"ffmpeg 转换失败 (returncode={proc.returncode})",
                returncode=proc.returncode,
                stderr=stderr_text,
            )

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise FFmpegError(
                "ffmpeg 报告成功但没有生成有效的输出文件（可能源文件没有音轨）",
                returncode=proc.returncode,
                stderr=stderr_text,
            )

        return FFmpegResult(output_path=output_path, skipped=False)
