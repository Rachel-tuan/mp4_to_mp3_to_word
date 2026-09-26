# MP4 转 MP3 转换工具 - customtkinter 版
import customtkinter as ctk
from tkinter import filedialog, messagebox
import subprocess
import os
import ctypes
import sys


if sys.platform == "win32":
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

# Windows DPI 适配
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

ctk.set_appearance_mode("System")  # 系统浅色或深色模式
ctk.set_default_color_theme("blue")  # 主题色：蓝色

# 获取ffmpeg.exe路径，兼容打包和调试
def get_ffmpeg_path():
    if getattr(sys, 'frozen', False):
        # 正确获取 PyInstaller 解压资源的路径
        return os.path.join(sys._MEIPASS, "ffmpeg.exe")
    else:
        # 脚本调试时使用脚本所在路径
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")

def select_single_video():
    file_path = filedialog.askopenfilename(filetypes=[("MP4 视频文件", "*.mp4")])
    if file_path:
        single_video_var.set(file_path)
        base = os.path.splitext(file_path)[0]
        single_output_var.set(base + ".mp3")

def select_single_output():
    file_path = filedialog.asksaveasfilename(defaultextension=".mp3", filetypes=[("MP3 音频文件", "*.mp3")])
    if file_path:
        single_output_var.set(file_path)

def convert_single():
    video_path = single_video_var.get()
    output_path = single_output_var.get()
    if not os.path.isfile(video_path):
        messagebox.showerror("错误", "请选择有效的单个 MP4 视频文件")
        return
    try:
        status_var.set("单文件转换中，请稍候...")
        app.update()
        ffmpeg_path = get_ffmpeg_path()
        subprocess.run(
            [ffmpeg_path, "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
        status_var.set("单文件转换成功！")
        messagebox.showinfo("完成", f"已保存音频到：\n{output_path}")
    except Exception as e:
        status_var.set("单文件转换失败")
        messagebox.showerror("错误", f"发生错误：{str(e)}")

def select_batch_folder():
    folder = filedialog.askdirectory()
    if folder:
        batch_folder_var.set(folder)

def select_batch_output_folder():
    folder = filedialog.askdirectory()
    if folder:
        batch_output_var.set(folder)

def convert_batch():
    folder = batch_folder_var.get()
    output_folder = batch_output_var.get()
    if not os.path.isdir(folder):
        messagebox.showerror("错误", "请选择有效的 MP4 文件夹")
        return
    if not os.path.isdir(output_folder):
        messagebox.showerror("错误", "请选择有效的输出文件夹")
        return
    mp4_files = [f for f in os.listdir(folder) if f.lower().endswith(".mp4")]
    if not mp4_files:
        messagebox.showwarning("提示", "该文件夹内没有 .mp4 文件")
        return
    success_count = 0
    status_var.set(f"开始批量转换 {len(mp4_files)} 个文件...")
    app.update()
    for idx, filename in enumerate(mp4_files, 1):
        input_path = os.path.join(folder, filename)
        base_name = os.path.splitext(filename)[0]
        output_path = os.path.join(output_folder, base_name + ".mp3")
        status_var.set(f"批量转换中：{filename} ({idx}/{len(mp4_files)})")
        app.update()
        try:
            ffmpeg_path = get_ffmpeg_path()

            subprocess.run(
                [ffmpeg_path, "-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
            success_count += 1
        except Exception as e:
            print(f"转换失败：{filename} - {e}")
    status_var.set(f"批量转换完成，成功转换 {success_count} 个文件")
    messagebox.showinfo("完成", f"批量转换完成，共成功转换 {success_count} 个文件")

# 创建主窗口
app = ctk.CTk()
app.title("MP4 转 MP3 转换工具 - customtkinter 版")
app.geometry("800x450")
app.grid_columnconfigure((0,1), weight=1)
app.grid_rowconfigure(0, weight=1)

single_video_var = ctk.StringVar()
single_output_var = ctk.StringVar()
batch_folder_var = ctk.StringVar()
batch_output_var = ctk.StringVar()
status_var = ctk.StringVar()

# 左侧 Frame - 单文件转换
left_frame = ctk.CTkFrame(app, corner_radius=10)
left_frame.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

ctk.CTkLabel(left_frame, text="单个文件转换", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0,15))

ctk.CTkLabel(left_frame, text="选择 MP4 视频文件：").pack(anchor="w", padx=10)
ctk.CTkEntry(left_frame, textvariable=single_video_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(left_frame, text="选择视频文件", command=select_single_video, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkLabel(left_frame, text="保存为 MP3 文件：").pack(anchor="w", padx=10, pady=(15,0))
ctk.CTkEntry(left_frame, textvariable=single_output_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(left_frame, text="选择保存路径（可选）", command=select_single_output, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkButton(left_frame, text="开始转换单个文件", command=convert_single, fg_color="#4caf50", width=180).pack(pady=20)

# 右侧 Frame - 批量转换
right_frame = ctk.CTkFrame(app, corner_radius=10)
right_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

ctk.CTkLabel(right_frame, text="批量文件夹转换", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0,15))

ctk.CTkLabel(right_frame, text="选择包含 MP4 文件的文件夹：").pack(anchor="w", padx=10)
ctk.CTkEntry(right_frame, textvariable=batch_folder_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(right_frame, text="选择文件夹", command=select_batch_folder, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkLabel(right_frame, text="选择 MP3 输出文件夹：").pack(anchor="w", padx=10, pady=(15,0))
ctk.CTkEntry(right_frame, textvariable=batch_output_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(right_frame, text="选择输出文件夹", command=select_batch_output_folder, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkButton(right_frame, text="开始批量转换", command=convert_batch, fg_color="#2196f3", width=180).pack(pady=20)

# 状态栏
status_label = ctk.CTkLabel(app, textvariable=status_var, text_color="#0d47a1", font=ctk.CTkFont(size=12, slant="italic"))
status_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=30, pady=(0,15))

app.mainloop()
import customtkinter as ctk
from tkinter import filedialog, messagebox
import subprocess
import os
import ctypes

# Windows DPI 适配
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

ctk.set_appearance_mode("System")  # 系统浅色或深色模式
ctk.set_default_color_theme("blue")  # 主题色：蓝色

def select_single_video():
    file_path = filedialog.askopenfilename(filetypes=[("MP4 视频文件", "*.mp4")])
    if file_path:
        single_video_var.set(file_path)
        base = os.path.splitext(file_path)[0]
        single_output_var.set(base + ".mp3")

def select_single_output():
    file_path = filedialog.asksaveasfilename(defaultextension=".mp3", filetypes=[("MP3 音频文件", "*.mp3")])
    if file_path:
        single_output_var.set(file_path)

def convert_single():
    video_path = single_video_var.get()
    output_path = single_output_var.get()
    if not os.path.isfile(video_path):
        messagebox.showerror("错误", "请选择有效的单个 MP4 视频文件")
        return
    try:
        status_var.set("单文件转换中，请稍候...")
        app.update()
        ffmpeg_path = get_ffmpeg_path()

        subprocess.run(
            [ffmpeg_path, "-i", video_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
        status_var.set("单文件转换成功！")
        messagebox.showinfo("完成", f"已保存音频到：\n{output_path}")
    except Exception as e:
        status_var.set("单文件转换失败")
        messagebox.showerror("错误", f"发生错误：{str(e)}")

def select_batch_folder():
    folder = filedialog.askdirectory()
    if folder:
        batch_folder_var.set(folder)

def select_batch_output_folder():
    folder = filedialog.askdirectory()
    if folder:
        batch_output_var.set(folder)

def convert_batch():
    folder = batch_folder_var.get()
    output_folder = batch_output_var.get()
    if not os.path.isdir(folder):
        messagebox.showerror("错误", "请选择有效的 MP4 文件夹")
        return
    if not os.path.isdir(output_folder):
        messagebox.showerror("错误", "请选择有效的输出文件夹")
        return
    mp4_files = [f for f in os.listdir(folder) if f.lower().endswith(".mp4")]
    if not mp4_files:
        messagebox.showwarning("提示", "该文件夹内没有 .mp4 文件")
        return
    success_count = 0
    status_var.set(f"开始批量转换 {len(mp4_files)} 个文件...")
    app.update()
    for idx, filename in enumerate(mp4_files, 1):
        input_path = os.path.join(folder, filename)
        base_name = os.path.splitext(filename)[0]
        output_path = os.path.join(output_folder, base_name + ".mp3")
        status_var.set(f"批量转换中：{filename} ({idx}/{len(mp4_files)})")
        app.update()
        try:
            ffmpeg_path = get_ffmpeg_path()

            subprocess.run(
                [ffmpeg_path, "-i", input_path, "-vn", "-acodec", "libmp3lame", "-q:a", "2", output_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,creationflags=CREATE_NO_WINDOW)
            success_count += 1
        except Exception as e:
            print(f"转换失败：{filename} - {e}")
    status_var.set(f"批量转换完成，成功转换 {success_count} 个文件")
    messagebox.showinfo("完成", f"批量转换完成，共成功转换 {success_count} 个文件")

# 创建主窗口
app = ctk.CTk()
app.title("MP4 转 MP3 转换工具")
app.geometry("800x450")
app.grid_columnconfigure((0,1), weight=1)
app.grid_rowconfigure(0, weight=1)

single_video_var = ctk.StringVar()
single_output_var = ctk.StringVar()
batch_folder_var = ctk.StringVar()
batch_output_var = ctk.StringVar()
status_var = ctk.StringVar()

# 左侧 Frame - 单文件转换
left_frame = ctk.CTkFrame(app, corner_radius=10)
left_frame.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

ctk.CTkLabel(left_frame, text="单个文件转换", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0,15))

ctk.CTkLabel(left_frame, text="选择 MP4 视频文件：").pack(anchor="w", padx=10)
ctk.CTkEntry(left_frame, textvariable=single_video_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(left_frame, text="选择视频文件", command=select_single_video, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkLabel(left_frame, text="保存为 MP3 文件：").pack(anchor="w", padx=10, pady=(15,0))
ctk.CTkEntry(left_frame, textvariable=single_output_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(left_frame, text="选择保存路径（可选）", command=select_single_output, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkButton(left_frame, text="开始转换单个文件", command=convert_single, fg_color="#4caf50", width=180).pack(pady=20)

# 右侧 Frame - 批量转换
right_frame = ctk.CTkFrame(app, corner_radius=10)
right_frame.grid(row=0, column=1, padx=20, pady=20, sticky="nsew")

ctk.CTkLabel(right_frame, text="批量文件夹转换", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0,15))

ctk.CTkLabel(right_frame, text="选择包含 MP4 文件的文件夹：").pack(anchor="w", padx=10)
ctk.CTkEntry(right_frame, textvariable=batch_folder_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(right_frame, text="选择文件夹", command=select_batch_folder, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkLabel(right_frame, text="选择 MP3 输出文件夹：").pack(anchor="w", padx=10, pady=(15,0))
ctk.CTkEntry(right_frame, textvariable=batch_output_var, width=400).pack(padx=10, pady=5)
ctk.CTkButton(right_frame, text="选择输出文件夹", command=select_batch_output_folder, fg_color="#1976d2").pack(padx=10, pady=5)

ctk.CTkButton(right_frame, text="开始批量转换", command=convert_batch, fg_color="#2196f3", width=180).pack(pady=20)

# 状态栏
status_label = ctk.CTkLabel(app, textvariable=status_var, text_color="#0d47a1", font=ctk.CTkFont(size=12, slant="italic"))
status_label.grid(row=1, column=0, columnspan=2, sticky="w", padx=30, pady=(0,15))

app.mainloop()
