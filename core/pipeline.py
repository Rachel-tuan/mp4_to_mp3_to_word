"""Orchestrates a single job through all pipeline stages.

This module has zero UI knowledge (spec 三: "UI 不允许包含 FFmpeg、STT、
DeepSeek 的具体实现"). It is driven by ``ui/main_window.py`` from a worker
thread; progress is reported through a simple callback so the caller can
push updates onto a ``queue.Queue`` for the Tk mainloop to consume.

Resume logic (spec 十) is file-existence based rather than purely relying
on the persisted status, so it still works if the state file was deleted
but the output files weren't: a valid, non-empty mp3/transcript/manuscript
on disk is treated as "this stage is already done" unless the caller asked
for force_reprocess.
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from core.job import Job, JobStatus
from core.state import JobStore
from services.ffmpeg_service import FFmpegError, FFmpegService, OverwritePolicy
from services.stt_service import STTError, STTProvider
from services.deepseek_service import DeepSeekError, DeepSeekService
from services.document_service import DocumentError, markdown_to_docx, save_text

logger = logging.getLogger("app.pipeline")

ProgressCallback = Optional[Callable[[Job, str], None]]


class JobCancelled(Exception):
    pass


def _valid_file(path: str, min_size: int = 1) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= min_size
    except OSError:
        return False


class Pipeline:
    def __init__(
        self,
        store: JobStore,
        ffmpeg_service: FFmpegService,
        stt_provider: Optional[STTProvider] = None,
        deepseek_service: Optional[DeepSeekService] = None,
    ):
        self.store = store
        self.ffmpeg_service = ffmpeg_service
        self.stt_provider = stt_provider
        self.deepseek_service = deepseek_service

    def _emit(self, job: Job, message: str, on_progress: ProgressCallback):
        self.store.upsert(job)
        if on_progress:
            on_progress(job, message)

    def run_job(
        self,
        job: Job,
        on_progress: ProgressCallback = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Job:
        def check_cancel():
            if cancel_check and cancel_check():
                job.set_status(JobStatus.CANCELLED)
                self._emit(job, "任务已取消", on_progress)
                raise JobCancelled(job.base_name)

        os.makedirs(job.job_dir, exist_ok=True)
        opts = job.options

        try:
            check_cancel()
            # ---- Stage 1: FFmpeg MP4 -> MP3 --------------------------------
            if opts.make_mp3:
                job.set_status(JobStatus.CONVERTING)
                self._emit(job, f"正在转换音频: {job.base_name}", on_progress)
                if opts.force_reprocess or not _valid_file(job.mp3_path):
                    # We already decided above that ffmpeg needs to run, so
                    # always overwrite here; the SKIP policy is what the
                    # _valid_file() check above effectively implements.
                    self.ffmpeg_service.extract_mp3(
                        job.input_path,
                        job.mp3_path,
                        overwrite=OverwritePolicy.OVERWRITE,
                    )
                else:
                    logger.info("Reusing existing mp3 for %s", job.base_name)
                job.set_status(JobStatus.CONVERTED)
                self._emit(job, f"音频转换完成: {job.base_name}", on_progress)

            check_cancel()
            # ---- Stage 2: STT ----------------------------------------------
            if opts.make_transcript:
                job.set_status(JobStatus.TRANSCRIBING)
                self._emit(job, f"正在转写: {job.base_name}", on_progress)
                if opts.force_reprocess or not _valid_file(job.transcript_path):
                    if not self.stt_provider:
                        raise STTError("未配置 STT Provider")
                    transcript = self.stt_provider.transcribe(job.mp3_path)
                    save_text(job.transcript_path, transcript.text, overwrite=True)
                else:
                    logger.info("Reusing existing transcript for %s", job.base_name)
                job.set_status(JobStatus.TRANSCRIBED)
                self._emit(job, f"转写完成: {job.base_name}", on_progress)

            check_cancel()
            # ---- Stage 3: DeepSeek manuscript --------------------------------
            if opts.make_manuscript:
                job.set_status(JobStatus.GENERATING)
                self._emit(job, f"正在生成 AI 文稿: {job.base_name}", on_progress)
                if opts.force_reprocess or not _valid_file(job.manuscript_path):
                    if not self.deepseek_service:
                        raise DeepSeekError("未配置 DeepSeek Service")
                    if not _valid_file(job.transcript_path):
                        raise DeepSeekError("找不到转写稿，无法生成文稿")
                    with open(job.transcript_path, "r", encoding="utf-8") as f:
                        transcript_text = f.read()

                    def _chunk_progress(i, n):
                        self._emit(job, f"AI 整理分块中 ({i}/{n}): {job.base_name}", on_progress)

                    result = self.deepseek_service.generate_manuscript(
                        transcript_text, progress_cb=_chunk_progress
                    )
                    save_text(job.manuscript_path, result.markdown, overwrite=True)
                else:
                    logger.info("Reusing existing manuscript for %s", job.base_name)
                job.set_status(JobStatus.GENERATED)
                self._emit(job, f"AI 文稿生成完成: {job.base_name}", on_progress)

            check_cancel()
            # ---- Stage 4: DOCX export -----------------------------------------
            if opts.make_docx:
                job.set_status(JobStatus.EXPORTING)
                self._emit(job, f"正在导出 Word 文档: {job.base_name}", on_progress)
                if not _valid_file(job.manuscript_path):
                    raise DocumentError("找不到 AI 文稿，无法导出 Word")
                with open(job.manuscript_path, "r", encoding="utf-8") as f:
                    manuscript_text = f.read()
                if opts.force_reprocess or not _valid_file(job.docx_path):
                    markdown_to_docx(manuscript_text, job.docx_path)
                else:
                    logger.info("Reusing existing docx for %s", job.base_name)

            job.set_status(JobStatus.DONE)
            self._emit(job, f"完成: {job.base_name}", on_progress)

        except JobCancelled:
            raise
        except (FFmpegError, STTError, DeepSeekError, DocumentError) as e:
            logger.error("Job %s failed at stage %s: %s", job.base_name, job.status, e)
            job.set_status(JobStatus.FAILED, error=str(e))
            self._emit(job, f"失败: {job.base_name} - {e}", on_progress)
        except Exception as e:  # unexpected -- still must not crash the batch
            logger.exception("Unexpected error processing %s", job.base_name)
            job.set_status(JobStatus.FAILED, error=f"未知错误: {e}")
            self._emit(job, f"失败: {job.base_name} - {e}", on_progress)

        return job


def build_jobs_for_folder(folder: str, output_root: str, options) -> list[Job]:
    mp4_files = sorted(f for f in os.listdir(folder) if f.lower().endswith(".mp4"))
    jobs = []
    for filename in mp4_files:
        jobs.append(Job(input_path=os.path.join(folder, filename), output_root=output_root, options=options))
    return jobs
