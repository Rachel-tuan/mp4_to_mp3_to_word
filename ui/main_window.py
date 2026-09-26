"""Main Tkinter window.

Pure UI: no FFmpeg/STT/DeepSeek implementation details live here (spec 三).
All actual work happens on a background worker thread; the thread only
ever talks back to Tk through a ``queue.Queue`` that we drain with
``root.after()`` -- never ``app.update()`` from a background thread, and
never a direct subprocess call on the main thread.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from core.job import Job, JobOptions, JobStatus
from core.pipeline import Pipeline, build_jobs_for_folder, JobCancelled
from core.state import JobStore
from services.ffmpeg_service import FFmpegService
from services.stt_service import get_stt_provider
from services.deepseek_service import DeepSeekService

logger = logging.getLogger("app.ui")

STT_PROVIDERS = ["alibaba_dashscope", "openai_compatible", "faster_whisper"]
LLM_PROVIDERS = ["deepseek"]

# Default model names shown next to each provider so the user knows which
# model is actually being called (kept in sync with services/* defaults).
STT_MODEL_LABELS = {
    "alibaba_dashscope": "qwen-audio-3.1-asr-flash-filetrans",
    "openai_compatible": "whisper-1",
    "faster_whisper": "(本地，未实现)",
}
LLM_MODEL_LABELS = {
    "deepseek": "deepseek-v4-flash",
}


class MainWindow:
    def __init__(self, root: tk.Tk, output_root: str = "output"):
        self.root = root
        self.root.title("视频 AI 文稿流水线工具")
        self.root.geometry("900x620")

        self.output_root = os.path.abspath(output_root)
        os.makedirs(self.output_root, exist_ok=True)
        self.store = JobStore(self.output_root)

        self.msg_queue: "queue.Queue" = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.job_rows: dict[str, str] = {}  # job.id -> treeview item id

        self._build_ui()
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        # Input selection -------------------------------------------------
        input_frame = ttk.LabelFrame(top, text="输入", padding=10)
        input_frame.pack(fill="x", pady=5)

        self.input_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.input_var, width=70).grid(row=0, column=0, columnspan=3, sticky="we", padx=5, pady=3)
        ttk.Button(input_frame, text="选择单个 MP4", command=self._select_single).grid(row=1, column=0, padx=5, pady=3)
        ttk.Button(input_frame, text="选择文件夹（批量）", command=self._select_folder).grid(row=1, column=1, padx=5, pady=3)

        ttk.Label(input_frame, text="输出目录：").grid(row=2, column=0, sticky="w", padx=5)
        self.output_var = tk.StringVar(value=self.output_root)
        ttk.Entry(input_frame, textvariable=self.output_var, width=55).grid(row=2, column=1, sticky="we", padx=5)
        ttk.Button(input_frame, text="选择输出目录", command=self._select_output_dir).grid(row=2, column=2, padx=5)

        # Provider / options ------------------------------------------------
        opt_frame = ttk.LabelFrame(top, text="流水线选项", padding=10)
        opt_frame.pack(fill="x", pady=5)

        ttk.Label(opt_frame, text="STT Provider：").grid(row=0, column=0, sticky="w")
        self.stt_provider_var = tk.StringVar(value=STT_PROVIDERS[0])
        ttk.Combobox(opt_frame, textvariable=self.stt_provider_var, values=STT_PROVIDERS, state="readonly", width=20).grid(row=0, column=1, sticky="w", padx=5)

        self.stt_model_label_var = tk.StringVar(value=STT_MODEL_LABELS[self.stt_provider_var.get()])
        ttk.Label(opt_frame, textvariable=self.stt_model_label_var, foreground="gray").grid(row=0, column=2, sticky="w", padx=(15, 0))

        ttk.Label(opt_frame, text="DashScope API Key：").grid(row=1, column=0, sticky="w")
        self.stt_key_var = tk.StringVar(value=os.environ.get("DASHSCOPE_API_KEY", ""))
        ttk.Entry(opt_frame, textvariable=self.stt_key_var, width=28, show="*").grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(opt_frame, text="LLM Provider：").grid(row=2, column=0, sticky="w", pady=(5, 0))
        self.llm_provider_var = tk.StringVar(value=LLM_PROVIDERS[0])
        ttk.Combobox(opt_frame, textvariable=self.llm_provider_var, values=LLM_PROVIDERS, state="readonly", width=20).grid(row=2, column=1, sticky="w", padx=5, pady=(5, 0))

        self.llm_model_label_var = tk.StringVar(value=LLM_MODEL_LABELS[self.llm_provider_var.get()])
        ttk.Label(opt_frame, textvariable=self.llm_model_label_var, foreground="gray").grid(row=2, column=2, sticky="w", padx=(15, 0), pady=(5, 0))

        ttk.Label(opt_frame, text="DeepSeek API Key：").grid(row=3, column=0, sticky="w", pady=(5, 0))
        self.llm_key_var = tk.StringVar(value=os.environ.get("DEEPSEEK_API_KEY", ""))
        ttk.Entry(opt_frame, textvariable=self.llm_key_var, width=28, show="*").grid(row=3, column=1, sticky="w", padx=5, pady=(5, 0))

        # Update model label when provider changes.
        self.stt_provider_var.trace_add("write", lambda *_: self._on_stt_provider_change())
        self.llm_provider_var.trace_add("write", lambda *_: self._on_llm_provider_change())

        check_frame = ttk.Frame(opt_frame)
        check_frame.grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))

        self.make_mp3_var = tk.BooleanVar(value=True)
        self.make_transcript_var = tk.BooleanVar(value=True)
        self.make_manuscript_var = tk.BooleanVar(value=True)
        self.make_docx_var = tk.BooleanVar(value=True)
        self.force_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(check_frame, text="生成 MP3", variable=self.make_mp3_var).pack(side="left", padx=5)
        ttk.Checkbutton(check_frame, text="生成原始转写", variable=self.make_transcript_var).pack(side="left", padx=5)
        ttk.Checkbutton(check_frame, text="生成 AI 文稿", variable=self.make_manuscript_var).pack(side="left", padx=5)
        ttk.Checkbutton(check_frame, text="生成 Word", variable=self.make_docx_var).pack(side="left", padx=5)
        ttk.Checkbutton(check_frame, text="强制重新处理（忽略已有结果）", variable=self.force_var).pack(side="left", padx=5)

        # Controls -----------------------------------------------------------
        ctrl_frame = ttk.Frame(top)
        ctrl_frame.pack(fill="x", pady=8)
        self.start_btn = ttk.Button(ctrl_frame, text="开始处理", command=self._start)
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = ttk.Button(ctrl_frame, text="停止/取消", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=5)

        self.current_task_var = tk.StringVar(value="就绪")
        ttk.Label(ctrl_frame, textvariable=self.current_task_var).pack(side="left", padx=15)

        self.progress = ttk.Progressbar(top, mode="determinate")
        self.progress.pack(fill="x", pady=5)

        # Job table -----------------------------------------------------------
        table_frame = ttk.LabelFrame(self.root, text="批量任务", padding=10)
        table_frame.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("filename", "status", "progress")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for col, label, width in (("filename", "文件名", 400), ("status", "状态", 150), ("progress", "进度", 300)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width)
        self.tree.pack(fill="both", expand=True)

        # Error log -----------------------------------------------------------
        err_frame = ttk.LabelFrame(self.root, text="错误信息", padding=5)
        err_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.error_text = tk.Text(err_frame, height=5, state="disabled")
        self.error_text.pack(fill="x")

    # ------------------------------------------------------------------
    # Input selection callbacks
    # ------------------------------------------------------------------
    def _select_single(self):
        path = filedialog.askopenfilename(filetypes=[("MP4 视频文件", "*.mp4")])
        if path:
            self.input_var.set(path)
            self._mode = "single"
            # Auto-set output to the same folder as the selected MP4.
            self.output_var.set(os.path.dirname(path))

    def _select_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.input_var.set(path)
            self._mode = "folder"
            # Auto-set output to the same folder as the selected input folder.
            self.output_var.set(path)

    def _select_output_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.output_var.set(path)

    def _on_stt_provider_change(self):
        name = self.stt_provider_var.get()
        self.stt_model_label_var.set(STT_MODEL_LABELS.get(name, ""))

    def _on_llm_provider_change(self):
        name = self.llm_provider_var.get()
        self.llm_model_label_var.set(LLM_MODEL_LABELS.get(name, ""))

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------
    def _current_options(self) -> JobOptions:
        return JobOptions(
            make_mp3=self.make_mp3_var.get(),
            make_transcript=self.make_transcript_var.get(),
            make_manuscript=self.make_manuscript_var.get(),
            make_docx=self.make_docx_var.get(),
            force_reprocess=self.force_var.get(),
            stt_provider=self.stt_provider_var.get(),
            llm_provider=self.llm_provider_var.get(),
        )

    def _start(self):
        input_path = self.input_var.get().strip()
        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("错误", "请先选择单个 MP4 文件或包含 MP4 的文件夹")
            return

        output_root = self.output_var.get().strip() or self.output_root
        os.makedirs(output_root, exist_ok=True)
        self.output_root = output_root
        self.store = JobStore(output_root)

        options = self._current_options()
        if os.path.isdir(input_path):
            jobs = build_jobs_for_folder(input_path, output_root, options)
            if not jobs:
                messagebox.showwarning("提示", "该文件夹内没有 .mp4 文件")
                return
        else:
            jobs = [Job(input_path=input_path, output_root=output_root, options=options)]

        self.tree.delete(*self.tree.get_children())
        self.job_rows.clear()
        for job in jobs:
            item = self.tree.insert("", "end", values=(os.path.basename(job.input_path), job.status.value, ""))
            self.job_rows[job.id] = item

        self._clear_errors()
        self.progress["value"] = 0
        self.progress["maximum"] = len(jobs)
        self.cancel_event.clear()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        try:
            stt_provider = get_stt_provider(
                options.stt_provider,
                api_key=self.stt_key_var.get().strip() or None,
            )
        except Exception as e:
            messagebox.showerror("错误", f"STT Provider 初始化失败: {e}")
            self._reset_controls()
            return

        deepseek_service = DeepSeekService(api_key=self.llm_key_var.get().strip() or None)
        ffmpeg_service = FFmpegService()
        pipeline = Pipeline(self.store, ffmpeg_service, stt_provider, deepseek_service)

        self.worker_thread = threading.Thread(
            target=self._run_jobs, args=(pipeline, jobs), daemon=True
        )
        self.worker_thread.start()

    def _stop(self):
        self.cancel_event.set()
        self.current_task_var.set("正在停止…")

    def _reset_controls(self):
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # Worker thread body (NEVER touch Tk widgets directly from here)
    # ------------------------------------------------------------------
    def _run_jobs(self, pipeline: Pipeline, jobs: list[Job]):
        done = 0
        for job in jobs:
            if self.cancel_event.is_set():
                self.msg_queue.put(("job_update", job, "已取消剩余任务"))
                break
            try:
                pipeline.run_job(
                    job,
                    on_progress=lambda j, msg: self.msg_queue.put(("job_update", j, msg)),
                    cancel_check=self.cancel_event.is_set,
                )
            except JobCancelled:
                self.msg_queue.put(("job_update", job, "已取消"))
                break
            done += 1
            self.msg_queue.put(("overall_progress", done, len(jobs)))
        self.msg_queue.put(("finished", None, None))

    # ------------------------------------------------------------------
    # Queue draining on the Tk main thread
    # ------------------------------------------------------------------
    def _poll_queue(self):
        try:
            while True:
                kind, a, b = self.msg_queue.get_nowait()
                if kind == "job_update":
                    job, msg = a, b
                    self._update_job_row(job)
                    self.current_task_var.set(msg)
                    if job.status == JobStatus.FAILED:
                        self._log_error(f"{job.base_name}: {job.error}")
                elif kind == "overall_progress":
                    self.progress["value"] = a
                elif kind == "finished":
                    self._reset_controls()
                    self.current_task_var.set("全部完成")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _update_job_row(self, job: Job):
        item = self.job_rows.get(job.id)
        if item is None:
            return
        progress_text = job.error if job.status == JobStatus.FAILED and job.error else job.status.value
        self.tree.item(item, values=(os.path.basename(job.input_path), job.status.value, progress_text))

    def _log_error(self, text: str):
        self.error_text.config(state="normal")
        self.error_text.insert("end", text + "\n")
        self.error_text.see("end")
        self.error_text.config(state="disabled")

    def _clear_errors(self):
        self.error_text.config(state="normal")
        self.error_text.delete("1.0", "end")
        self.error_text.config(state="disabled")


def run():
    root = tk.Tk()
    MainWindow(root)
    root.mainloop()
