# 视频 AI 文稿流水线工具

将 MP4 → FFmpeg 提取 MP3 → 阿里云百炼 STT 语音转写 → DeepSeek LLM 文稿整理 → Markdown → DOCX 串成一条可断点续跑的本地流水线。

## 核心流水线

```
MP4
 ↓ FFmpeg
MP3
 ↓ Alibaba DashScope (qwen-audio-3.1-asr-flash-filetrans)
transcript.md (原始逐字转写稿)
 ↓ DeepSeek (deepseek-v4-flash)
manuscript.md (AI 整理后的文稿)
 ↓ python-docx
manuscript.docx (Word 文档)
```

## 为什么用两家 API、两个 Key

| 服务 | 提供方 | 模型 | 用途 |
|---|---|---|---|
| STT 语音转写 | 阿里云百炼 DashScope | `qwen-audio-3.1-asr-flash-filetrans` | 把 MP3 音频转成逐字文字 |
| LLM 文稿整理 | DeepSeek | `deepseek-v4-flash` | 把原始转写稿整理成结构清晰的文稿 |

两家 API 分工明确，互不混用：
- **DashScope Key** 只负责语音转写，不会调用 LLM
- **DeepSeek Key** 只负责文稿整理，不会调用 STT
- 两个 Key 分别从各自平台申请，分别配置

---

## 1. 项目目录结构

```
mp4_to_mp3/
  app.py                          # 入口
  requirements.txt
  .gitignore
  app.spec                        # PyInstaller 打包配置
  # ffmpeg.exe 需自行下载放置在项目根目录（见下方"安装"说明）
  .env.local.example              # 环境变量示例（复制为 .env.local 使用）
  prompts/
    manuscript.md                 # 分块整理 prompt（可自行修改）
    synthesis.md                  # 最终汇总 prompt（可自行修改）
  core/
    job.py                        # Job / JobStatus / JobOptions 数据模型
    state.py                      # JSON 任务状态持久化（JobStore）
    pipeline.py                   # 串联各阶段、断点续跑、单任务失败隔离
    chunking.py                   # 长文本分块（独立可测试）
    logging_config.py             # 统一日志配置
  services/
    ffmpeg_service.py             # 唯一调用 ffmpeg 子进程的地方
    stt_service.py                # STT Provider 抽象基类 + OpenAI 兼容实现
    alibaba_stt_service.py        # 阿里云百炼 DashScope STT 实现（默认）
    deepseek_service.py           # DeepSeek LLM 调用 + 重试 + 分块整理
    document_service.py           # 保存 Markdown / Markdown→DOCX
  ui/
    main_window.py                # 纯 Tkinter 界面，只做展示和调度
  tests/                          # pytest 单元/集成测试（全部 mock 外部 API）
  logs/                           # 运行日志（app.log），不入库
  output/                         # 产出目录，不入库
```

---

## 2. 安装依赖

### 2.1 下载 FFmpeg（必需）

项目不捆绑 `ffmpeg.exe`（文件太大，不适合 GitHub），请自行下载：

1. 从 [FFmpeg 官网](https://ffmpeg.org/download.html#build-windows) 下载 Windows 构建
2. 解压后把 `ffmpeg.exe` 放到**项目根目录**（和 `app.py` 同级）

### 2.2 安装 Python 依赖

```bash
cd mp4_to_mp3
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

运行时依赖只有三个：
- `requests` — 调用 STT / DeepSeek HTTP API
- `python-docx` — 生成 Word 文档
- `python-dotenv` — 加载 `.env.local`（可选）

`tkinter` 是 Python 标准库自带，无需额外安装。`pyinstaller` 仅打包时需要。

---

## 3. 配置两个 API Key

### 推荐方式：.env.local 文件

项目根目录建一个 `.env.local` 文件（已加入 `.gitignore`，不会被提交）：

```bash
cp .env.local.example .env.local
```

编辑 `.env.local`：

```
# 阿里云百炼 STT Key（语音转写用）
DASHSCOPE_API_KEY=your_dashscope_key_here

# DeepSeek LLM Key（文稿整理用）
DEEPSEEK_API_KEY=your_deepseek_key_here
```

**获取 Key：**
- DashScope Key：[阿里云百炼控制台](https://help.aliyun.com/zh/model-studio/get-api-key)
- DeepSeek Key：[DeepSeek 开放平台](https://platform.deepseek.com/api_keys)

### 环境变量优先级

1. **系统环境变量** 优先级最高（如果已设置，`.env.local` 不会覆盖）
2. **UI 输入框** 中输入的 Key 只保存在内存，不会写入磁盘或日志
3. **`.env.local`** 文件作为默认值

### 模型名称

| 服务 | 模型 | 环境变量覆盖 |
|---|---|---|
| DashScope STT | `qwen-audio-3.1-asr-flash-filetrans` | `DASHSCOPE_STT_MODEL` |
| DeepSeek LLM | `deepseek-v4-flash` | `DEEPSEEK_MODEL` |

默认值已内置，无需修改即可使用。

---

## 4. 运行

```bash
python app.py
```

界面操作：

1. **输入**：选择单个 MP4 文件，或选择一个包含多个 MP4 的文件夹（批量）
2. **输出目录**：默认 `output/`，每个视频一个子文件夹
3. **STT 设置**：Provider 默认 `alibaba_dashscope`，模型 `qwen-audio-3.1-asr-flash-filetrans`，填入 DashScope Key
4. **LLM 设置**：Provider 默认 `deepseek`，模型 `deepseek-v4-flash`，填入 DeepSeek Key
5. **任务选项**：勾选需要生成的产物（MP3 / 原始转写 / AI 文稿 / Word）
6. 点击「开始处理」，可随时「停止/取消」
7. 下方表格显示每个文件的状态；出错的文件在「错误信息」区显示简要原因，完整技术细节在 `logs/app.log`

### 输出结构

```
output/
  视频名称/
    视频名称.mp3
    视频名称.transcript.md    # 原始逐字转写稿
    视频名称.manuscript.md    # AI 整理后的 Markdown 文稿
    视频名称.manuscript.docx  # Word 版本
```

---

## 5. 断点续跑

程序自动根据已有产物决定是否跳过已完成的阶段：

| 已有产物 | 下次运行时跳过 |
|---|---|
| `xxx.mp3` 存在且非空 | FFmpeg 音频转换 |
| `xxx.transcript.md` 存在且非空 | DashScope STT 语音转写 |
| `xxx.manuscript.md` 存在且非空 | DeepSeek LLM 文稿整理 |
| `xxx.manuscript.docx` 存在且非空 | DOCX 导出 |

任务状态持久化在 `output/.pipeline_state.json`，程序重启后自动读取。

### 强制重新处理

勾选「强制重新处理」选项，会忽略已有产物，从头开始执行所有阶段。

---

## 6. 批量处理

- 选择包含多个 MP4 的文件夹即可批量处理
- **单任务顺序执行**（有意设计），避免 API 限流和上传过多文件
- 某一个文件失败，只会标记该文件失败，不会影响其他文件继续处理
- 批量结果示例：
  ```
  001.mp4 ✅ done
  002.mp4 ✅ done
  003.mp4 ❌ failed (STT 超时)
  004.mp4 🔄 generating
  005.mp4 ⏳ pending
  ```

---

## 7. 长文本处理

长视频转写稿可能几万字，不能一次性发给 DeepSeek。

程序自动判断：
- **短文本**（默认 ≤ 6000 字）：直接调用一次整理 + 一次汇总
- **长文本**：自动按段落/句子分块 → 每块单独整理 → 最后调用一次 LLM 汇总成完整文稿

分块逻辑在 `core/chunking.py`，独立可测试。

---

## 8. 重试机制

| 错误类型 | 是否重试 |
|---|---|
| 网络连接错误 / 超时 | ✅ 最多 3 次，指数退避 |
| 服务端 5xx 错误 | ✅ 最多 3 次，指数退避 |
| 限流 429 | ✅ 最多 3 次，退避 |
| 鉴权失败 401/403 | ❌ 不重试（Key 错误） |
| 参数错误 400 | ❌ 不重试（请求格式错误） |
| 本地文件不存在 / 权限错误 | ❌ 不重试 |

---

## 9. 日志

日志文件：`logs/app.log`（滚动日志，单文件 5MB，保留 3 份）

记录内容：
- 任务开始/结束/失败
- FFmpeg 命令、返回码、stderr
- STT 任务 ID、状态、错误
- DeepSeek 请求、重试、超时
- DOCX 生成结果

**绝对不会打印完整 API Key**，日志中只显示前 4 位 + 后 4 位的脱敏形式。

---

## 10. 测试

### 自动化测试（mock 外部 API）

```bash
pip install pytest
python -m pytest tests/ -v
```

当前 **38 个测试全部通过**，全部使用 mock 隔离外部依赖，无需真实 API Key 或网络连接。

覆盖范围：
- FFmpeg 正常转换 / 无音轨 / 输出已存在 / 特殊文件名 / 返回非零
- DashScope STT 完整流程 / 鉴权失败 / 上传失败 / 任务失败 / 超时 / 限流
- OpenAI 兼容 STT 正常 / 网络失败 / 4xx 不重试
- DeepSeek 正常 / 网络失败 / 短文本单次处理
- 批量单任务失败不影响其他任务
- 已有产物自动跳过各阶段
- 状态持久化与重启后恢复
- 长文本分块 / 短文本不分块
- Markdown → DOCX 渲染
- `.env.local` 加载与环境变量优先级

### 人工验证（需要真实 API）

发布前建议手动验证：

1. 用一个真实中文 MP3 + 真实 `DASHSCOPE_API_KEY`，模型 `qwen-audio-3.1-asr-flash-filetrans`，得到真实中文 transcript
2. 用真实 transcript + 真实 `DEEPSEEK_API_KEY`，模型 `deepseek-v4-flash`，生成真实 manuscript.md 和 manuscript.docx

---

## 11. 打包 Windows EXE

**前置条件：** 确保 `ffmpeg.exe` 已放在项目根目录（和 `app.py` 同级）。

```bash
pip install pyinstaller
pyinstaller app.spec
```

产物在 `dist/video_ai_pipeline.exe`。

`app.spec` 已配置：
- 自动打包项目根目录的 `ffmpeg.exe`
- 自动打包 `prompts/` 目录
- 不包含任何大型 AI 模型权重（STT 走云端 API）

---

## 12. 当前限制

1. **DashScope STT 需要公网可访问的音频 URL**：程序会自动把本地 MP3 上传到 DashScope 临时 OSS（有效期 48 小时），无需用户手动配置 OSS。
2. **断点续跑不校验模型版本**：如果更换了 STT 或 LLM 模型，旧产物不会自动失效，需要勾选「强制重新处理」。
3. **DOCX 渲染是轻量 Markdown 子集**：支持标题 `#`/`##`/`###`、`**加粗**`、普通段落，不支持列表、表格、图片等复杂 Markdown 语法。
4. **批处理单线程顺序执行**：有意设计，避免同时打多个 API 请求导致限流。未来需要并发可在 pipeline 之上加线程池。
5. **UI 重启后不自动加载历史任务**：JobStore 能正确读回状态（有测试覆盖），但需要用户重新选择同一输入才会触发跳过逻辑。
6. **FasterWhisper 本地 STT 未实现**：只是占位类，抛 `NotImplementedError`。
