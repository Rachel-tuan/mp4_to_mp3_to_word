"""Job model: represents a single video going through the pipeline."""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    PENDING = "pending"
    CONVERTING = "converting"
    CONVERTED = "converted"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    GENERATING = "generating"
    GENERATED = "generated"
    EXPORTING = "exporting"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Terminal / "successfully finished a stage" statuses used for resume logic.
STAGE_ORDER = [
    JobStatus.PENDING,
    JobStatus.CONVERTED,
    JobStatus.TRANSCRIBED,
    JobStatus.GENERATED,
    JobStatus.DONE,
]


@dataclass
class JobOptions:
    make_mp3: bool = True
    make_transcript: bool = True
    make_manuscript: bool = True
    make_docx: bool = True
    force_reprocess: bool = False  # user clicked "重新处理"
    stt_provider: str = "alibaba_dashscope"
    llm_provider: str = "deepseek"


@dataclass
class Job:
    input_path: str
    output_root: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    base_name: str = ""
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    options: JobOptions = field(default_factory=JobOptions)

    # Derived output paths, filled in by __post_init__ / pipeline.
    job_dir: str = ""
    mp3_path: str = ""
    transcript_path: str = ""
    manuscript_path: str = ""
    docx_path: str = ""

    def __post_init__(self):
        if not self.base_name:
            self.base_name = os.path.splitext(os.path.basename(self.input_path))[0]
        if not self.job_dir:
            self.job_dir = os.path.join(self.output_root, self.base_name)
        if not self.mp3_path:
            self.mp3_path = os.path.join(self.job_dir, f"{self.base_name}.mp3")
        if not self.transcript_path:
            self.transcript_path = os.path.join(self.job_dir, f"{self.base_name}.transcript.md")
        if not self.manuscript_path:
            self.manuscript_path = os.path.join(self.job_dir, f"{self.base_name}.manuscript.md")
        if not self.docx_path:
            self.docx_path = os.path.join(self.job_dir, f"{self.base_name}.manuscript.docx")

    def touch(self):
        self.updated_at = time.time()

    def set_status(self, status: JobStatus, error: Optional[str] = None):
        self.status = status
        self.error = error
        self.touch()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "Job":
        d = dict(d)
        opts = d.pop("options", {}) or {}
        status = d.pop("status", JobStatus.PENDING.value)
        job = Job(
            input_path=d.pop("input_path"),
            output_root=d.pop("output_root"),
            id=d.pop("id"),
            base_name=d.pop("base_name", ""),
            job_dir=d.pop("job_dir", ""),
            mp3_path=d.pop("mp3_path", ""),
            transcript_path=d.pop("transcript_path", ""),
            manuscript_path=d.pop("manuscript_path", ""),
            docx_path=d.pop("docx_path", ""),
            created_at=d.pop("created_at", time.time()),
            updated_at=d.pop("updated_at", time.time()),
            progress=d.pop("progress", 0.0),
            error=d.pop("error", None),
            options=JobOptions(**opts),
        )
        job.status = JobStatus(status)
        return job
