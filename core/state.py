"""Lightweight JSON-backed persistence for job state.

We deliberately avoid a database dependency. One JSON file lives inside the
output root (``output/.pipeline_state.json``) and maps job id -> job dict.
This lets the app resume after a restart: on startup we can look at a job's
input file + output paths and figure out which stage to resume from, even if
the state file itself was lost (see ``core.pipeline.resolve_resume_status``).
"""
from __future__ import annotations

import json
import os
import threading
from typing import Dict, Optional

from core.job import Job

STATE_FILENAME = ".pipeline_state.json"


class JobStore:
    """Thread-safe JSON store for :class:`Job` objects."""

    def __init__(self, output_root: str):
        self.output_root = output_root
        self.path = os.path.join(output_root, STATE_FILENAME)
        self._lock = threading.Lock()
        self._jobs: Dict[str, Job] = {}
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for job_id, job_dict in raw.items():
                try:
                    self._jobs[job_id] = Job.from_dict(job_dict)
                except Exception:
                    # Corrupt single entry shouldn't kill the whole store.
                    continue
        except (json.JSONDecodeError, OSError):
            # Corrupt/unreadable state file: start fresh rather than crash.
            pass

    def _save_locked(self):
        os.makedirs(self.output_root, exist_ok=True)
        tmp_path = self.path + ".tmp"
        data = {job_id: job.to_dict() for job_id, job in self._jobs.items()}
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    def upsert(self, job: Job):
        with self._lock:
            self._jobs[job.id] = job
            self._save_locked()

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def find_by_input(self, input_path: str) -> Optional[Job]:
        input_path = os.path.abspath(input_path)
        with self._lock:
            for job in self._jobs.values():
                if os.path.abspath(job.input_path) == input_path:
                    return job
        return None

    def all(self):
        with self._lock:
            return list(self._jobs.values())
