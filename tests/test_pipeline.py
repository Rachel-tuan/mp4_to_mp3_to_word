"""Covers spec 十五 items 2, 11, 12, 13 and the "one failure doesn't affect
others" requirement from spec 二/十.
"""
import os
from unittest import mock

import pytest

from core.job import Job, JobOptions, JobStatus
from core.pipeline import Pipeline
from core.state import JobStore
from services.ffmpeg_service import FFmpegError
from services.deepseek_service import ManuscriptResult


class FakeFFmpeg:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on or set()
        self.calls = []

    def extract_mp3(self, input_path, output_path, overwrite=None, quality="2"):
        self.calls.append(input_path)
        if os.path.basename(input_path) in self.fail_on:
            raise FFmpegError("模拟 ffmpeg 失败")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake mp3")
        from services.ffmpeg_service import FFmpegResult
        return FFmpegResult(output_path=output_path)


class FakeSTT:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_path):
        self.calls += 1
        from services.stt_service import Transcript
        return Transcript(text="这是转写结果")


class FakeDeepSeek:
    def __init__(self):
        self.calls = 0

    def generate_manuscript(self, text, progress_cb=None):
        self.calls += 1
        return ManuscriptResult(markdown="# 标题\n\n整理稿正文", chunk_count=1)


def make_job(tmp_path, name="video1.mp4", **opt_kwargs):
    input_path = tmp_path / name
    input_path.write_bytes(b"fake mp4")
    options = JobOptions(**opt_kwargs)
    return Job(input_path=str(input_path), output_root=str(tmp_path / "output"), options=options)


def test_batch_one_failure_does_not_affect_others(tmp_path):
    store = JobStore(str(tmp_path / "output"))
    ffmpeg = FakeFFmpeg(fail_on={"bad.mp4"})
    pipeline = Pipeline(store, ffmpeg, FakeSTT(), FakeDeepSeek())

    jobs = [
        make_job(tmp_path, "a.mp4"),
        make_job(tmp_path, "bad.mp4"),
        make_job(tmp_path, "c.mp4"),
    ]
    results = [pipeline.run_job(j) for j in jobs]

    assert results[0].status == JobStatus.DONE
    assert results[1].status == JobStatus.FAILED
    assert results[2].status == JobStatus.DONE
    # the failure didn't stop ffmpeg from being invoked for the third file
    assert any(os.path.basename(p) == "c.mp4" for p in ffmpeg.calls)


def test_skip_ffmpeg_when_mp3_already_valid(tmp_path):
    store = JobStore(str(tmp_path / "output"))
    ffmpeg = FakeFFmpeg()
    stt = FakeSTT()
    pipeline = Pipeline(store, ffmpeg, stt, FakeDeepSeek())

    job = make_job(tmp_path, make_transcript=False, make_manuscript=False, make_docx=False)
    os.makedirs(job.job_dir, exist_ok=True)
    with open(job.mp3_path, "wb") as f:
        f.write(b"pre-existing valid mp3")

    pipeline.run_job(job)
    assert ffmpeg.calls == []  # never called ffmpeg, reused existing file
    assert job.status == JobStatus.DONE


def test_skip_stt_when_transcript_already_exists(tmp_path):
    store = JobStore(str(tmp_path / "output"))
    ffmpeg = FakeFFmpeg()
    stt = FakeSTT()
    pipeline = Pipeline(store, ffmpeg, stt, FakeDeepSeek())

    job = make_job(tmp_path, make_manuscript=False, make_docx=False)
    os.makedirs(job.job_dir, exist_ok=True)
    with open(job.transcript_path, "w", encoding="utf-8") as f:
        f.write("已经存在的转写稿")

    pipeline.run_job(job)
    assert stt.calls == 0
    assert job.status == JobStatus.DONE


def test_skip_llm_when_manuscript_already_exists(tmp_path):
    store = JobStore(str(tmp_path / "output"))
    ffmpeg = FakeFFmpeg()
    stt = FakeSTT()
    deepseek = FakeDeepSeek()
    pipeline = Pipeline(store, ffmpeg, stt, deepseek)

    job = make_job(tmp_path, make_docx=False)
    os.makedirs(job.job_dir, exist_ok=True)
    with open(job.transcript_path, "w", encoding="utf-8") as f:
        f.write("转写稿")
    with open(job.manuscript_path, "w", encoding="utf-8") as f:
        f.write("已经存在的文稿")

    pipeline.run_job(job)
    assert deepseek.calls == 0
    assert job.status == JobStatus.DONE


def test_state_persists_and_reloads_after_restart(tmp_path):
    output_root = str(tmp_path / "output")
    store1 = JobStore(output_root)
    ffmpeg = FakeFFmpeg()
    pipeline = Pipeline(store1, ffmpeg, FakeSTT(), FakeDeepSeek())

    job = make_job(tmp_path, make_transcript=False, make_manuscript=False, make_docx=False)
    pipeline.run_job(job)
    assert job.status == JobStatus.DONE

    # simulate app restart: brand new JobStore reading the same output dir
    store2 = JobStore(output_root)
    reloaded = store2.get(job.id)
    assert reloaded is not None
    assert reloaded.status == JobStatus.DONE
    assert reloaded.input_path == job.input_path
