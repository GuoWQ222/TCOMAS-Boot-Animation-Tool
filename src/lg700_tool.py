"""LG700 boot animation utility: GUI and a read-only diagnostic CLI."""
import argparse
import io
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import sys
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw
from lg700_core import LG700, capacity, unpack_boot, restore_file
from convert_media import convert
from vendor_pause import pause_vendor
from current_media import detect_current_media
from backup_store import data_home, new_snapshot, preserve_original, original_directory, device_home
from quality_preview import QualityPreview
from hover_help import question_icon


def configure_logging():
    directory = data_home() / 'Logs'
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / 'LG700Tool.log', maxBytes=2_000_000,
                                  backupCount=3, encoding='utf8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logger = logging.getLogger('lg700')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return directory / 'LG700Tool.log'

def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_%f")


def crop_checkbutton_images(root, style):
    images = []
    for selected, disabled in ((False, False), (True, False), (False, True), (True, True)):
        icon = Image.new('RGBA', (88, 88))
        draw = ImageDraw.Draw(icon)
        fill = ('#a5b5bc' if disabled else '#0089b9') if selected else '#ffffff'
        outline = '#b6bdc1' if disabled else ('#0089b9' if selected else '#87949b')
        draw.rounded_rectangle((8, 8, 80, 80), radius=13, fill=fill, outline=outline, width=5)
        if selected:
            draw.line(((25, 44), (39, 58), (64, 29)), fill='white', width=10, joint='curve')
        images.append(ImageTk.PhotoImage(icon.resize((22, 22), Image.Resampling.LANCZOS), master=root))
    style.element_create('Crop.indicator', 'image', images[0],
        ('disabled selected', images[3]), ('disabled', images[2]), ('selected', images[1]), sticky='w')
    style.layout('Crop.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Crop.indicator', {'side': 'left', 'sticky': 'w'}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'nswe', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    style.configure('Crop.TCheckbutton', font=('Microsoft YaHei UI', 11), padding=(0, 3, 0, 3))
    return images


class App:
    def __init__(self, root):
        self.root, self.events, self.busy = root, queue.Queue(), False
        self.asset, self.frames, self.preview_index, self.playing = None, [], 0, False
        self.current_media = None
        self.preview_source = None
        self.preview_generation = 0
        self.preview_after = None
        self.preview_ready = False
        self.preview_wanted_playing = False
        self.tick_after = None
        self.model_summary = ''
        self.operation_stage = '就绪'
        self.log_path = configure_logging()
        self.quality_worker = QualityPreview(self.events)
        root.title("LG700 开机动画工具 v1.0.1 · 通信修复测试版")
        root.geometry("1000x840")
        root.minsize(950, 810)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TButton", padding=(12, 8), font=("Microsoft YaHei UI", 10))
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        outer = ttk.Frame(root, padding=22)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="LG700 开机动画", style="Title.TLabel").pack(anchor="w")
        self.device_text = tk.StringVar(value="正在检测屏幕……")
        ttk.Label(outer, textvariable=self.device_text).pack(anchor="w", pady=(8, 15))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        self.buttons = []
        for text, action in [("识别水冷与当前动画", self.detect), ("备份屏幕素材", self.backup),
                             ("修复当前离线播放", self.repair_offline)]:
            button = ttk.Button(row, text=text, command=action)
            button.pack(side="left", padx=(0, 10))
            self.buttons.append(button)
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(18, 10))
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 22))
        self.canvas = tk.Canvas(left, width=480, height=360, bg="#151b24", highlightthickness=0)
        self.canvas.pack(anchor="w")
        self.canvas.create_text(240, 180, text="选择动画素材", fill="#b6c2d1",
                                font=("Microsoft YaHei UI", 16))
        self.file_text = tk.StringVar(value="未选择素材")
        ttk.Label(left, textvariable=self.file_text, wraplength=490).pack(anchor="w", pady=(8, 0))
        panel = ttk.Frame(body, width=280)
        panel.pack(side="right", fill="y")
        for text, action in [("当前动画写入硬件", self.write_current),
                             ("写入自定义视频", self.choose_media),
                             ("恢复钛坦原始动画", self.restore),
                             ("播放 / 暂停本地预览", self.toggle_preview)]:
            button = ttk.Button(panel, text=text, command=action)
            button.pack(fill="x", pady=(0, 10))
            self.buttons.append(button)
        quality_row = ttk.Frame(panel)
        quality_row.pack(fill='x', pady=(8, 4))
        self.quality_title = tk.StringVar(value='转换画质：75')
        self.quality_size = tk.StringVar(value='动画大小：未计算')
        ttk.Label(quality_row, textvariable=self.quality_title).pack(side='left')
        self.quality_size_label = ttk.Label(quality_row, textvariable=self.quality_size,
                                           foreground='#526172')
        self.quality_size_label.pack(side='right', padx=(8, 0))
        self.quality = tk.DoubleVar(value=75)
        self.last_quality_value = 75
        self.quality_scale = ttk.Scale(panel, from_=35, to=90, variable=self.quality,
                                       command=self.quality_changed)
        self.quality_scale.pack(fill="x")
        self.quality_hint = tk.StringVar(value='拖动后自动计算，尚未写入硬件')
        self.quality_hint_label = ttk.Label(panel, textvariable=self.quality_hint,
                                           foreground='#526172', wraplength=265)
        self.quality_hint_label.pack(anchor='w', pady=(4, 0))
        self.fill_screen = tk.BooleanVar(value=False)
        self.crop_indicator_images = crop_checkbutton_images(root, style)
        fill_row = ttk.Frame(panel)
        fill_row.pack(anchor='w', pady=(8, 0))
        self.fill_checkbutton = ttk.Checkbutton(fill_row, text='等比裁剪铺满屏幕', variable=self.fill_screen,
            command=self.fill_mode_changed, style='Crop.TCheckbutton')
        self.fill_checkbutton.pack(side='left')
        self.fill_help = question_icon(fill_row,
            '不勾选：按原比例完整显示，比例与屏幕不同时会补黑边。\n\n'
            '勾选：保持原比例，居中裁剪后铺满 640×480 屏幕，不拉伸画面。\n'
            '比 4:3 更宽的素材裁左右，更高的素材裁上下；正好 4:3 时不裁剪。\n\n'
            '可能裁掉边缘内容，不会自动识别或跟随人物。切换只更新本地预览和大小，点击写入后才保存到屏幕。')
        self.fill_help.pack(side='left', padx=(5, 0))
        ttk.Label(panel, text="支持 MP4、GIF、JPG、PNG 等。\n"
                  "硬件动画区：11 MiB\n可用素材：11,534,316 字节\n"
                  "按转码后的实际大小检查，超限报错。\n"
                  "写前自动备份，写后完整读回校验。\n"
                  "本地预览 30 FPS，硬件节奏由固件决定。",
                  wraplength=265, foreground="#526172").pack(anchor="w", pady=8)
        ttk.Label(outer, text="备份位置：%LOCALAPPDATA%\\TCOMAS-LG700-Tool\\Backups\\",
                  foreground="#526172").pack(anchor="w", pady=(0, 8))
        self.status = tk.StringVar(value="就绪")
        ttk.Label(outer, textvariable=self.status, wraplength=850).pack(anchor="w", pady=(0, 6))
        self.progress = ttk.Progressbar(outer, maximum=100)
        self.progress.pack(fill="x")
        self.log = tk.Text(outer, height=5, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="x", pady=(10, 0))
        root.after(100, self.poll)
        self.detect()

    def note(self, text, percent=None):
        if percent is None:
            logging.getLogger('lg700.app').info(text)
        self.events.put(("note", text, percent))

    def stage(self, name):
        self.operation_stage = name
        self.note(name)

    def selected_quality(self):
        return max(35, min(90, round(self.quality.get())))

    def update_hardware_size(self, size):
        self.device_text.set(f'{self.model_summary} · 当前屏幕动画 {size / 1048576:.2f} MiB（已保存）')

    def stop_preview_timer(self):
        if self.tick_after is not None:
            self.root.after_cancel(self.tick_after)
            self.tick_after = None

    def quality_changed(self, value=None):
        quality = self.selected_quality()
        if quality == self.last_quality_value:
            return
        self.last_quality_value = quality
        self.quality_title.set(f'转换画质：{quality}')
        self.request_quality_preview()

    def fill_mode_changed(self):
        if not self.busy:
            self.request_quality_preview(delay=0)

    def request_quality_preview(self, delay=350, play=False):
        self.preview_generation += 1
        self.preview_ready = False
        self.preview_wanted_playing = self.playing or self.preview_wanted_playing or play
        self.playing = False
        self.stop_preview_timer()
        if self.preview_after is not None:
            self.root.after_cancel(self.preview_after)
            self.preview_after = None
        if not self.preview_source:
            self.quality_size.set('动画大小：无素材')
            return
        self.quality_size.set('动画大小：计算中…')
        self.quality_size_label.configure(foreground='#526172')
        self.quality_hint.set('正在计算本地转换大小，不会自动写入')
        self.quality_hint_label.configure(foreground='#526172')
        self.preview_after = self.root.after(delay, self.start_quality_preview)

    def start_quality_preview(self):
        self.preview_after = None
        if self.busy or not self.preview_source:
            return
        self.quality_worker.request(self.preview_generation, self.preview_source,
                                    self.selected_quality(), self.fill_screen.get())

    def accept_quality_result(self, event):
        _, generation, source, quality, fill_screen, data, error = event
        if (self.busy or generation != self.preview_generation or
                source != self.preview_source or quality != self.selected_quality() or
                fill_screen != self.fill_screen.get()):
            return
        if error:
            self.quality_size.set('动画大小：计算失败')
            self.quality_size_label.configure(foreground='#b42318')
            self.quality_hint.set(error)
            self.quality_hint_label.configure(foreground='#b42318')
            return
        self.apply_preview_data(quality, data)

    def apply_preview_data(self, quality, data):
        self.stop_preview_timer()
        self.playing = False
        self.asset = data
        self.frames = unpack_boot(data, inspect_jpeg=False)
        self.preview_index = 0
        self.preview_ready = True
        self.quality_size.set(f'动画大小：{len(data) / 1048576:.2f} MiB')
        excess = len(data) - capacity(1)
        color = '#b42318' if excess > 0 else '#166534'
        self.quality_size_label.configure(foreground=color)
        self.quality_hint_label.configure(foreground=color)
        if excess > 0:
            self.quality_hint.set(f'超出容量 {excess / 1048576:.2f} MiB，请降低画质；不能写入')
        else:
            self.quality_hint.set(f'{len(self.frames)} 帧 · 上限 11.00 MiB · 本地转换结果')
        self.draw()
        if self.preview_wanted_playing:
            self.playing = True
            self.preview_wanted_playing = False
            self.tick()

    def run(self, action):
        if self.busy:
            return
        self.busy = True
        self.operation_stage = '准备操作（尚未发送写入）'
        self.preview_generation += 1
        self.preview_ready = False
        if self.preview_after is not None:
            self.root.after_cancel(self.preview_after)
            self.preview_after = None
        self.quality_scale.configure(state='disabled')
        self.fill_checkbutton.configure(state='disabled')
        for button in self.buttons:
            button.configure(state="disabled")
        def worker():
            try:
                action()
            except Exception as error:
                logging.getLogger('lg700.app').exception('Operation failed at %s', self.operation_stage)
                message = self.operation_stage + '\n' + str(error)
                if self.operation_stage in ('写前备份（尚未发送擦除或写入）', '核验原始备份（尚未发送擦除或写入）'):
                    message += '\n本次尚未发送擦除或写入命令，原动画未被本次操作覆盖。'
                self.events.put(("error", message + '\n日志：' + str(self.log_path)))
            finally:
                self.events.put(("done",))
        threading.Thread(target=worker, daemon=False).start()

    def poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "note":
                    self.status.set(event[1])
                    if event[2] is not None:
                        self.progress["value"] = event[2]
                    else:
                        self.log.configure(state="normal")
                        self.log.insert("end", event[1] + "\n")
                        self.log.see("end")
                        self.log.configure(state="disabled")
                elif event[0] == "device":
                    self.model_summary = f'{event[1]} · REV {event[2]:04X} · 开机区 11 MiB'
                    self.update_hardware_size(event[3])
                elif event[0] == 'hardware_size':
                    self.update_hardware_size(event[1])
                elif event[0] == 'quality_result':
                    self.accept_quality_result(event)
                elif event[0] == "reset_current":
                    self.current_media = None
                    self.preview_source = None
                    self.preview_generation += 1
                    self.preview_ready = False
                    self.frames, self.playing = [], False
                    self.canvas.delete("all")
                    self.canvas.create_text(240, 180, text="正在识别当前素材……", fill="#b6c2d1",
                                            font=("Microsoft YaHei UI", 16))
                    self.file_text.set("正在识别当前素材……")
                elif event[0] == "media_missing":
                    self.quality_size.set('动画大小：无素材')
                    self.file_text.set("未识别到软件当前动画，可使用本地视频或图片")
                    self.canvas.delete("all")
                    self.canvas.create_text(240, 180, text="请选择本地视频或图片", fill="#b6c2d1",
                                            font=("Microsoft YaHei UI", 16))
                elif event[0] == "current":
                    self.current_media = event[1]
                    self.preview_source = event[1]['path']
                    self.file_text.set("软件当前动画：" + event[1]["name"] +
                                       f" · 源文件 {event[1]['bytes'] / 1048576:.2f} MiB")
                    self.request_quality_preview(delay=0)
                elif event[0] == 'custom_source':
                    path = Path(event[1])
                    self.preview_source = str(path)
                    self.file_text.set(f'自定义素材：{path.name} · 源文件 {path.stat().st_size / 1048576:.2f} MiB')
                elif event[0] == 'prepared_asset':
                    self.apply_preview_data(event[1], event[2])
                elif event[0] == "error":
                    self.status.set("操作失败：" + event[1])
                    messagebox.showerror("LG700", event[1], parent=self.root)
                elif event[0] == "done":
                    self.busy = False
                    self.quality_scale.configure(state='normal')
                    self.fill_checkbutton.configure(state='normal')
                    for button in self.buttons:
                        button.configure(state="normal")
                    if self.preview_source and not self.preview_ready:
                        self.request_quality_preview(delay=0)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def detect(self):
        def job():
            self.events.put(("reset_current",))
            self.stage('识别设备')
            with pause_vendor(), LG700() as device:
                meta = device.metadata(1)
                model = device.info.get('product_string', 'TCOMAS LG700').strip()
                self.events.put(("device", model, device.revision, meta['size']))
                self.note("设备连接成功；备份目录：" + str(device_home(device)))
            try:
                media = detect_current_media()
                self.events.put(("current", media))
                self.note("已识别钛坦软件当前动画：" + media["name"])
            except (OSError, ValueError) as error:
                self.events.put(("media_missing",))
                self.note("设备已连接，当前软件动画未能识别：" + str(error) + "。仍可写入本地文件。")
        self.run(job)

    def backup(self):
        def job():
            self.stage('备份屏幕素材（只读）')
            with pause_vendor(), LG700() as device:
                directory = new_snapshot(device, "manual")
                device.backup(directory, lambda name, p: self.note(f"备份 {name}：{p:.0%}", p * 100))
            self.note("备份完成：" + str(directory))
        self.run(job)

    def enable_offline(self, device, directory):
        self.note('设置屏幕原生离线播放：图片常驻，视频循环……')
        result = device.set_offline_loop(True, directory)
        if result['device_reconnected']:
            self.note('模式切换后屏幕已重新连接；没有重复发送命令。')
        else:
            self.note('屏幕已应答离线播放模式命令。')
        self.note('模式接口没有读取功能；关机开机后的实际播放仍需观察确认。')

    def repair_offline(self):
        def job():
            self.stage('修复前备份（尚未修改播放模式）')
            with pause_vendor(), LG700() as device:
                directory = new_snapshot(device, 'before_playback_repair')
                device.backup(directory, lambda name, p: self.note(f'修复前备份 {name}：{p:.0%}', p * 100))
                preserve_original(device, directory)
                self.stage('设置离线播放模式')
                self.enable_offline(device, directory)
            self.note('离线播放设置已发送；原动画未重写，原厂传屏已恢复。')
        self.run(job)

    def draw(self):
        if not self.frames:
            return
        im = Image.open(io.BytesIO(self.frames[self.preview_index])).resize((480, 360))
        self.photo = ImageTk.PhotoImage(im)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def toggle_preview(self):
        if not self.preview_ready:
            self.request_quality_preview(delay=0, play=True)
            return
        self.playing = not self.playing
        self.preview_wanted_playing = False
        self.stop_preview_timer()
        if self.playing:
            self.tick()

    def tick(self):
        self.tick_after = None
        if self.playing and self.frames:
            self.draw()
            self.preview_index = (self.preview_index + 1) % len(self.frames)
            self.tick_after = self.root.after(33, self.tick)

    def choose_media(self):
        source = filedialog.askopenfilename(title="写入自定义视频或图片（转码后最多 11 MiB）",
            filetypes=[("视频和图片", "*.mp4 *.gif *.mov *.mkv *.webm *.avi *.jpg *.jpeg *.png *.bmp *.webp"), ("所有文件", "*.*")])
        if not source:
            return
        quality = self.selected_quality()
        fill_screen = bool(self.fill_screen.get())
        self.events.put(('custom_source', source))
        def job():
            dest = data_home() / "Generated" / (timestamp() + ".lg700boot")
            data = convert(source, dest, quality, self.note, fill_screen=fill_screen)
            self.events.put(('prepared_asset', quality, data))
            self.note(f"转换完成：{len(data):,} / {capacity(1):,} 字节，开始自动备份和写入")
            self.write_data(data)
        self.run(job)

    def write_current(self):
        quality = self.selected_quality()
        fill_screen = bool(self.fill_screen.get())
        def job():
            media = detect_current_media()  # re-read selection immediately before writing
            self.events.put(("current", media))
            dest = data_home() / "Generated" / (timestamp() + "_current.lg700boot")
            data = convert(media["path"], dest, quality, self.note, fill_screen=fill_screen)
            self.events.put(('prepared_asset', quality, data))
            self.note(f"当前动画转换完成：{len(data):,} / {capacity(1):,} 字节")
            self.write_data(data)
        self.run(job)

    def write_data(self, data):
        frame = unpack_boot(data)[0]
        self.stage('写前备份（尚未发送擦除或写入）')
        with pause_vendor(), LG700() as device:
            directory = new_snapshot(device, "before_write")
            device.backup(directory, lambda name, p: self.note(f"写前备份 {name}：{p:.0%}", p * 100))
            self.stage('核验原始备份（尚未发送擦除或写入）')
            original = preserve_original(device, directory)
            self.note("原始备份已保留：" + str(original))
            self.stage('素材写入及完整读回校验')
            assets = [(0, "待机图", frame), (1, "开机动画", data)]
            for selector, name, asset in assets:
                self.stage(f'{name}写入及完整读回校验')
                def update(p, name=name):
                    stage, fraction = ("读回校验", p - 1) if p > 1 else ("写入", p)
                    self.note(f"{name} {stage}：{fraction:.0%}", fraction * 100)
                digest = device.write_asset(selector, asset, directory, update)
                if device.last_verification_reconnected:
                    self.note(f'{name}校验期间 USB 重新连接，已重新完整读取；没有重发写入。')
                if device.last_write_skipped:
                    self.note(f'{name}与目标内容相同，完整读回一致，已跳过重复擦写：{digest[:16]}…')
                else:
                    self.note(f"{name}写入并读回一致：{digest[:16]}…")
            self.events.put(('hardware_size', len(data)))
            try:
                self.stage('设置离线播放模式（素材已写入并校验）')
                self.enable_offline(device, directory)
            except Exception as error:
                raise RuntimeError('素材已写入并校验通过，但离线播放模式设置失败：' + str(error)) from error
        self.note("素材写入校验通过，已设置离线播放。原厂传屏已恢复，请关机开机验证。")

    def restore(self):
        try:
            with pause_vendor(), LG700() as device:
                path = str(original_directory(device))
        except Exception:
            path = filedialog.askdirectory(title="选择包含 manifest.json 的钛坦原始备份目录",
                                           initialdir=str(data_home() / "Backups"))
        if not path:
            return
        try:
            original = {s: restore_file(path, s) for s in (0, 1)}
        except Exception as error:
            messagebox.showerror("恢复失败", str(error), parent=self.root)
            return
        def job():
            self.stage('恢复前备份')
            with pause_vendor(), LG700() as device:
                try:
                    directory = new_snapshot(device, "before_restore")
                    device.backup(directory, lambda name, p: self.note(f"恢复前备份 {name}：{p:.0%}", p * 100))
                except (OSError, ValueError) as error:
                    self.note("当前素材无法完整备份，将用现有原厂备份恢复：" + str(error))
                self.stage('恢复素材并完整读回校验')
                for selector in (0, 1):
                    def update(p, selector=selector):
                        fraction = p - 1 if p > 1 else p
                        self.note(f"恢复素材 {selector}：{fraction:.0%}", fraction * 100)
                    device.write_asset(selector, original[selector], path,
                                       update, recovery=True)
                self.events.put(('hardware_size', len(original[1])))
                device.set_offline_loop(False, path)
            self.note("原厂动画和待机图恢复完成，读回校验通过；已发送原厂默认播放模式 0。")
        self.run(job)

    def close(self):
        if self.busy:
            messagebox.showinfo("LG700", "操作尚未完成，请等待写入或备份结束。", parent=self.root)
        else:
            self.quality_worker.close()
            self.root.destroy()


def main():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Local.Tools.LG700BootTool")
    parser = argparse.ArgumentParser(description="LG700 native boot animation tool")
    parser.add_argument("--inspect", action="store_true", help="read-only device probe, writes JSON to stdout")
    parser.add_argument("--output", type=Path, help="save the read-only diagnostic JSON")
    args = parser.parse_args()
    if args.inspect:
        with LG700() as device:
            result = {"model": "TCOMAS LG700", "revision": f"{device.revision:04X}",
                      "boot": device.metadata(1), "standby": device.metadata(0)}
        encoded = json.dumps(result, indent=2)
        if args.output:
            args.output.write_text(encoded, encoding="utf8")
        else:
            print(encoded)
    else:
        App(tk.Tk()).root.mainloop()


if __name__ == "__main__":
    main()
