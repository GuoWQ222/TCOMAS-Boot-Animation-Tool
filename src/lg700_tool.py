"""TCOMAS LG700 / LG600 / LX800 assets: GUI and read-only diagnostics."""
import argparse
import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw
from lg700_core import LG700, capacity, unpack_boot, restore_file, sha256, is_empty_boot
from convert_media import convert
from vendor_pause import pause_vendor
from backup_store import data_home, new_snapshot, preserve_original, original_directory, device_home
from maintenance_backup import backup_before_update
from quality_preview import QualityPreview
from hover_help import question_icon
from device_profiles import PROFILES, LG700_PROFILE, resolve_profile, connection_identity
from preview_geometry import preview_frame
from startup_notice import StartupNotice
from ui_theme import (configure_theme, MeshHeader, RoundedCard, SoftProgress, ResponsiveLayout,
                      theme_font, scalable_image, PAGE, MUTED, PRIMARY, PREVIEW)
from ui_settings import needs_startup_notice, remember_notice_read

APP_VERSION = '1.2.9'


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
    if hasattr(root, '_crop_indicator_images'):
        return root._crop_indicator_images
    images = []
    for selected, disabled in ((False, False), (True, False), (False, True), (True, True)):
        icon = Image.new('RGBA', (88, 88))
        draw = ImageDraw.Draw(icon)
        fill = ('#c7c8df' if disabled else PRIMARY) if selected else '#ffffff'
        outline = '#d6d7e7' if disabled else (PRIMARY if selected else '#b6b9d2')
        draw.rounded_rectangle((8, 8, 80, 80), radius=13, fill=fill, outline=outline, width=5)
        if selected:
            draw.line(((25, 44), (39, 58), (64, 29)), fill='white', width=10, joint='curve')
        images.append(scalable_image(root, icon.resize((22, 22), Image.Resampling.LANCZOS)))
    style.element_create('Crop.indicator', 'image', images[0],
        ('disabled selected', images[3]), ('disabled', images[2]), ('selected', images[1]), sticky='w')
    style.layout('Crop.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
        ('Crop.indicator', {'side': 'left', 'sticky': 'w'}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'nswe', 'children': [
            ('Checkbutton.label', {'sticky': 'nswe'})]})]})])
    style.configure('Crop.TCheckbutton', font=theme_font(root, 10), padding=(0, 3, 0, 3))
    root._crop_indicator_images = images
    return images


class App:
    def __init__(self, root, profile=None):
        self.root, self.events, self.busy = root, queue.Queue(), False
        self.asset, self.frames, self.preview_index, self.playing = None, [], 0, False
        self.current_media = None
        self.preview_kind = None
        self.preview_source = None
        self.preview_generation = 0
        self.preview_after = None
        self.preview_ready = False
        self.preview_wanted_playing = False
        self.tick_after = None
        self.model_summary = ''
        self.profile = resolve_profile(profile) if profile is not None else None
        self.device_identity = None
        self.preview_size = (480, 360)
        self.operation_stage = '就绪'
        self.log_path = configure_logging()
        self.quality_worker = QualityPreview(self.events)
        root.title(f"钛钽开机动画工具 v{APP_VERSION} · LG700 / LG600 / LX800")
        root.geometry("1120x860")
        root.minsize(1080, 840)
        root.protocol("WM_DELETE_WINDOW", self.close)
        style = configure_theme(root)
        outer = tk.Frame(root, background=PAGE, padx=18, pady=16)
        self.main_frame = outer
        outer.pack(fill="both", expand=True)
        MeshHeader(outer, '把喜欢的画面，留在屏幕里',
            '钛钽开机动画工具  ·  LG700 / LG600 / LX800', version=APP_VERSION).pack(fill='x', pady=(0, 10))
        device_card = RoundedCard(outer, padding=12)
        device_card.pack(fill='x', pady=(0, 10))
        target_row = ttk.Frame(device_card.body)
        target_row.pack(fill='x')
        ttk.Label(target_row, text='目标设备', style='CardTitle.TLabel').pack(side='left', padx=(0, 14))
        self.model_choice = tk.StringVar(value=self.profile.name if self.profile else '自动识别')
        self.model_selector = ttk.Combobox(target_row, textvariable=self.model_choice,
            values=('自动识别', *(p.name for p in PROFILES)), state='readonly', width=12)
        self.model_selector.pack(side='left')
        self.model_selector.bind('<<ComboboxSelected>>', self.model_changed)
        row = ttk.Frame(target_row)
        row.pack(side='right')
        self.buttons = []
        for text, action in [("识别水冷与当前动画", self.detect), ("备份屏幕素材", self.backup),
                             ("修复当前离线播放", self.repair_offline)]:
            button = ttk.Button(row, text=text, command=action, style='Small.TButton')
            button.pack(side="left", padx=(7, 0))
            self.buttons.append(button)
        self.notice_button = ttk.Button(row, text='提醒事项', command=self.show_notice, style='Small.TButton')
        self.notice_button.pack(side='left', padx=(7, 0))
        self.device_text = tk.StringVar(value="正在检测屏幕……")
        self.device_label = ttk.Label(device_card.body, textvariable=self.device_text,
                                      foreground='#696b9c', wraplength=1020)
        self.device_label.pack(anchor='w', pady=(5, 0))
        body = tk.Frame(outer, background=PAGE)
        self.content_body = body
        body.pack(fill="both", expand=True, pady=(0, 10))
        body.columnconfigure(0, weight=5, minsize=520)
        body.columnconfigure(1, weight=4, minsize=420)
        body.rowconfigure(0, weight=1)
        preview_card = RoundedCard(body)
        preview_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        left = preview_card.body
        preview_heading = ttk.Frame(left)
        preview_heading.pack(fill='x', pady=(0, 10))
        ttk.Label(preview_heading, text='画面预览', style='CardTitle.TLabel').pack(side='left')
        self.preview_caption = tk.StringVar(value='本地预览 · 30 FPS')
        ttk.Label(preview_heading, textvariable=self.preview_caption, style='Subtle.TLabel').pack(side='right')
        preview_area = tk.Frame(left, background=PREVIEW, width=480, height=360)
        self.preview_area = preview_area
        preview_area.pack_propagate(False)
        preview_area.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(preview_area, width=480, height=360, bg=PREVIEW, highlightthickness=0)
        self.canvas.pack(expand=True)
        preview_area.bind('<Configure>', self.resize_preview)
        self.canvas.create_text(240, 180, text="选择动画素材", fill="#b6c2d1",
                                font=theme_font(root, 16))
        self.file_text = tk.StringVar(value="未选择素材")
        self.file_label = ttk.Label(left, textvariable=self.file_text, wraplength=495, style='Subtle.TLabel')
        self.file_label.pack(anchor="w", pady=(10, 0))
        controls_card = RoundedCard(body)
        controls_card.grid(row=0, column=1, sticky='nsew')
        panel = controls_card.body
        ttk.Label(panel, text='素材与写入', style='CardTitle.TLabel').pack(anchor='w', pady=(0, 10))
        for text, action, button_style in [("写入自定义视频", self.choose_media, 'Primary.TButton'),
                             ("恢复钛钽原始动画", self.restore, 'TButton'),
                             ("播放 / 暂停本地预览", self.toggle_preview, 'TButton')]:
            button = ttk.Button(panel, text=text, command=action, style=button_style)
            button.pack(fill="x", pady=(0, 7))
            self.buttons.append(button)
        quality_row = ttk.Frame(panel)
        quality_row.pack(fill='x', pady=(7, 4))
        self.quality_title = tk.StringVar(value='转换画质：75')
        self.quality_size = tk.StringVar(value='动画大小：未计算')
        ttk.Label(quality_row, textvariable=self.quality_title).pack(side='left')
        self.quality_size_label = ttk.Label(quality_row, textvariable=self.quality_size,
                                           foreground=MUTED)
        self.quality_size_label.pack(side='right', padx=(8, 0))
        self.quality = tk.DoubleVar(value=75)
        self.last_quality_value = 75
        self.quality_scale = ttk.Scale(panel, from_=35, to=90, variable=self.quality,
                                       command=self.quality_changed)
        self.quality_scale.pack(fill="x")
        self.quality_hint = tk.StringVar(value='拖动后自动计算，尚未写入硬件')
        self.quality_hint_label = ttk.Label(panel, textvariable=self.quality_hint,
                                           foreground=MUTED, wraplength=395)
        self.quality_hint_label.pack(anchor='w', pady=(4, 0))
        self.fill_screen = tk.BooleanVar(value=True)
        self.crop_indicator_images = crop_checkbutton_images(root, style)
        fill_row = ttk.Frame(panel)
        fill_row.pack(anchor='w', pady=(8, 0))
        self.fill_checkbutton = ttk.Checkbutton(fill_row, text='等比裁剪铺满屏幕', variable=self.fill_screen,
            command=self.fill_mode_changed, style='Crop.TCheckbutton')
        self.fill_checkbutton.pack(side='left')
        self.fill_help = question_icon(fill_row, self.crop_help_text(LG700_PROFILE))
        self.fill_help.pack(side='left', padx=(5, 0))
        self.format_label = ttk.Label(panel, text="MP4 / GIF / JPG / PNG  ·  CPU 转码\n"
                  "动画容量 11 MiB（11,534,316 字节）\n"
                  "转换后检查容量，写前备份，写后完整校验。\n"
                  "硬件播放节奏由固件决定。",
                  wraplength=395, foreground=MUTED, font=theme_font(root, 9))
        self.format_label.pack(anchor="w", pady=(9, 0))
        status_card = RoundedCard(outer, padding=12)
        status_card.pack(fill='x')
        status_row = ttk.Frame(status_card.body)
        status_row.pack(fill='x', pady=(0, 8))
        self.status = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(status_row, textvariable=self.status, foreground='#62688b', wraplength=940)
        self.status_label.pack(side='left')
        self.progress_text = tk.StringVar(value='0%')
        ttk.Label(status_row, textvariable=self.progress_text, foreground=PRIMARY,
                  font=theme_font(root, 10, 'bold', family='Segoe UI')).pack(side='right')
        self.progress = SoftProgress(status_card.body, maximum=100)
        self.progress.pack(fill="x")
        self.log = tk.Text(status_card.body, height=2, state="disabled", font=theme_font(root, 9),
            background='#f8f8fd', foreground='#82869e', relief='flat', borderwidth=0,
            highlightthickness=0, padx=8, pady=5, selectbackground='#dddafa', wrap='word')
        self.log.pack(fill="x", pady=(8, 0))
        self.backup_label = ttk.Label(outer, text="备份位置：%LOCALAPPDATA%\\TCOMAS-LG700-Tool\\Backups\\",
                                      style='Page.TLabel')
        self.backup_label.pack(anchor='w', pady=(7, 0))
        ResponsiveLayout.activate(root, (1120, 860), self.apply_ui_scale)
        root.after(100, self.poll)
        self.detect()

    def apply_ui_scale(self, scale):
        self.main_frame.configure(padx=round(18 * scale), pady=round(16 * scale))
        self.content_body.columnconfigure(0, minsize=round(520 * scale))
        self.content_body.columnconfigure(1, minsize=round(420 * scale))
        self.preview_area.configure(width=round(480 * scale), height=round(360 * scale))
        self.file_label.configure(wraplength=round(495 * scale))
        self.quality_hint_label.configure(wraplength=round(395 * scale))
        self.format_label.configure(wraplength=round(395 * scale))
        self.status_label.configure(wraplength=round(940 * scale))
        self.device_label.configure(wraplength=round(1020 * scale))
        self.resize_preview()

    def resize_preview(self, event=None):
        width, height = self.preview_area.winfo_width(), self.preview_area.winfo_height()
        if min(width, height) <= 1:
            return
        aspect = self.target_profile().size[0] / self.target_profile().size[1]
        new_width = min(width, round(height * aspect))
        size = max(1, new_width), max(1, round(new_width / aspect))
        if size == self.preview_size:
            return
        self.preview_size = size
        self.canvas.configure(width=size[0], height=size[1])
        if self.frames:
            self.draw()
        else:
            for item in self.canvas.find_all():
                if self.canvas.type(item) == 'text':
                    self.canvas.coords(item, size[0] / 2, size[1] / 2)

    def show_notice(self):
        if self.busy:
            return
        geometry, minimum = self.root.geometry(), self.root.minsize()
        was_playing = self.playing
        self.playing = False
        self.stop_preview_timer()
        self.main_frame.pack_forget()
        def return_to_main():
            self.root.title(f'钛钽开机动画工具 v{APP_VERSION} · LG700 / LG600 / LX800')
            self.root.minsize(*minimum)
            self.root.geometry(geometry)
            self.root.protocol('WM_DELETE_WINDOW', self.close)
            self.main_frame.pack(fill='both', expand=True)
            ResponsiveLayout.activate(self.root, (1120, 860), self.apply_ui_scale)
            if was_playing and self.frames and self.preview_ready:
                self.playing = True
                self.tick()
        StartupNotice(self.root, on_read=return_to_main, version=APP_VERSION,
                      returning=True, on_close=self.close)

    def note(self, text, percent=None):
        if percent is None:
            logging.getLogger('lg700.app').info(text)
        self.events.put(("note", text, percent))

    def stage(self, name):
        self.operation_stage = name
        self.note(name)

    def selected_quality(self):
        return max(35, min(90, round(self.quality.get())))

    def target_profile(self):
        return self.profile or LG700_PROFILE

    @staticmethod
    def crop_help_text(profile):
        panel = ('LX800 为圆形可视区域，480×480 是编码画布。圆外四角不可见；'
                 '无论是否勾选，重要内容请放在圆内，预览已模拟圆形遮罩。\n\n'
                 if profile.visible_shape == 'circle' else '')
        return (panel + '不勾选：按原比例放入编码画布，比例不同时会补黑边。\n\n'
                f'勾选：保持原比例，居中裁剪后铺满 {profile.dimensions} 屏幕，不拉伸画面。\n'
                '比屏幕比例更宽的素材裁左右，更高的素材裁上下。\n\n'
                '可能裁掉边缘内容，不会自动识别或跟随人物。切换只更新本地预览和大小，点击写入后才保存到屏幕。')

    def apply_profile_view(self, profile):
        self.profile = profile
        self.resize_preview()
        shape = '圆形可视区域 · ' if profile.visible_shape == 'circle' else ''
        if self.device_identity is None:
            self.device_text.set(f'{profile.name} · {profile.dimensions} · {shape}{profile.validation_note}')
        self.fill_help.hover_help.text = self.crop_help_text(profile)

    def model_changed(self, event=None):
        if self.busy:
            return
        self.device_identity = None
        value = self.model_choice.get()
        self.profile = None if value == '自动识别' else resolve_profile(value)
        if self.profile is not None:
            self.apply_profile_view(self.profile)
        else:
            self.apply_profile_view(LG700_PROFILE)
            self.profile = None
            self.device_text.set('自动识别设备；无设备时可选择型号预览素材')
        self.detect()

    def update_hardware_size(self, size):
        self.device_text.set(f'{self.model_summary} · 已保存 {size / 1048576:.2f} MiB · {self.target_profile().validation_note}')

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

    @contextlib.contextmanager
    def vendor_session(self):
        # Only suspend the sender for the device transaction. The context
        # restores it on success or failure; no persistent process ownership.
        with pause_vendor():
            yield

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
            if self.preview_kind == 'hardware_first_frame':
                return
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
                                    self.selected_quality(), self.fill_screen.get(), self.target_profile())

    def accept_quality_result(self, event):
        _, generation, source, quality, fill_screen, data, error, *model = event
        if (self.busy or generation != self.preview_generation or
                source != self.preview_source or quality != self.selected_quality() or
                fill_screen != self.fill_screen.get() or
                (model and model[0] != self.target_profile().key)):
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
        self.preview_kind = 'local_asset'
        self.preview_caption.set('本地预览 · 30 FPS')
        self.frames = unpack_boot(data, inspect_jpeg=False, profile=self.target_profile())
        self.preview_index = 0
        self.preview_ready = True
        self.quality_size.set(f'动画大小：{len(data) / 1048576:.2f} MiB')
        excess = len(data) - capacity(1, self.target_profile())
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

    def apply_hardware_first_frame(self, snapshot):
        self.stop_preview_timer()
        self.update_hardware_size(snapshot['total_size'])
        self.playing = False
        self.preview_wanted_playing = False
        self.preview_kind = 'hardware_first_frame'
        self.preview_caption.set('硬件首帧 · 静态预览')
        self.preview_source = self.current_media = self.asset = None
        self.frames = [snapshot['jpeg']]
        self.preview_index = 0
        self.preview_ready = True
        self.quality_size.set(f"硬件动画：{snapshot['total_size'] / 1048576:.2f} MiB")
        self.quality_size_label.configure(foreground='#166534')
        self.quality_hint.set(f"共 {snapshot['frame_count']} 帧 · 只读取首帧，未转码")
        self.quality_hint_label.configure(foreground=MUTED)
        self.file_text.set(f"硬件已保存动画的首帧 · JPEG {snapshot['frame_size'] / 1024:.1f} KiB · 非实时画面")
        self.draw()

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
        self.notice_button.configure(state='disabled')
        self.model_selector.configure(state='disabled')
        for button in self.buttons:
            button.configure(state="disabled")
        def worker():
            try:
                action()
            except Exception as error:
                logging.getLogger('lg700.app').exception('Operation failed at %s', self.operation_stage)
                message = self.operation_stage + '\n' + str(error)
                if self.operation_stage in ('写前备份（尚未发送擦除或写入）', '核验原始备份（尚未发送擦除或写入）',
                                            '暂停离线循环（备份已核验，尚未擦写素材）'):
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
                        self.progress_text.set(f'{max(0, min(100, event[2])):.0f}%')
                    else:
                        self.log.configure(state="normal")
                        self.log.insert("end", event[1] + "\n")
                        self.log.see("end")
                        self.log.configure(state="disabled")
                elif event[0] == "device":
                    self.apply_profile_view(event[4])
                    self.device_identity = event[5]
                    self.model_summary = f'{event[4].name} · REV {event[2]:04X} · {event[4].dimensions} · 开机区 11 MiB'
                    self.update_hardware_size(event[3])
                elif event[0] == 'hardware_size':
                    self.update_hardware_size(event[1])
                elif event[0] == 'quality_result':
                    self.accept_quality_result(event)
                elif event[0] == "reset_current":
                    self.stop_preview_timer()
                    self.device_identity = None
                    self.current_media = None
                    self.preview_source = None
                    self.preview_kind = None
                    self.preview_caption.set('硬件首帧 · 读取中')
                    self.asset = None
                    self.preview_generation += 1
                    self.preview_ready = False
                    self.frames, self.playing = [], False
                    self.canvas.delete("all")
                    self.canvas.create_text(self.preview_size[0] / 2, self.preview_size[1] / 2, text="正在识别当前素材……", fill="#b6c2d1",
                                            font=theme_font(self.root, 16))
                    self.file_text.set("正在识别当前素材……")
                elif event[0] == 'boot_empty':
                    self.device_text.set(self.model_summary + ' · 尚未保存开机动画')
                    self.quality_size.set('硬件动画：未保存')
                    self.quality_hint.set('可选择本地图片或视频首次写入')
                    self.preview_caption.set('硬件动画 · 未保存')
                    self.file_text.set('设备已连接，开机动画为空；可选择本地图片或视频写入')
                    self.canvas.delete('all')
                    self.canvas.create_text(self.preview_size[0] / 2, self.preview_size[1] / 2,
                        text='尚未保存开机动画', fill='#b6c2d1', font=theme_font(self.root, 16))
                elif event[0] == "media_missing":
                    self.quality_size.set('首帧预览：不可用')
                    self.quality_hint.set('未用官方素材替代；仍可选择本地文件写入')
                    self.preview_caption.set('硬件首帧 · 未取得')
                    self.file_text.set("已识别设备，未能读取可靠的硬件首帧；请稍后重新识别")
                    self.canvas.delete("all")
                    self.canvas.create_text(self.preview_size[0] / 2, self.preview_size[1] / 2, text="未取得硬件首帧", fill="#b6c2d1",
                                            font=theme_font(self.root, 16))
                elif event[0] == 'hardware_first_frame':
                    self.apply_hardware_first_frame(event[1])
                elif event[0] == 'custom_source':
                    path = Path(event[1])
                    self.preview_source = str(path)
                    self.preview_kind = 'local_asset'
                    self.preview_caption.set('本地预览 · 30 FPS')
                    self.file_text.set(f'自定义素材：{path.name} · 源文件 {path.stat().st_size / 1048576:.2f} MiB')
                elif event[0] == 'prepared_asset':
                    self.apply_preview_data(event[1], event[2])
                elif event[0] == "error":
                    self.status.set("操作失败：" + event[1])
                    messagebox.showerror("钛钽开机动画工具", event[1], parent=self.root)
                elif event[0] == "done":
                    self.busy = False
                    self.quality_scale.configure(state='normal')
                    self.fill_checkbutton.configure(state='normal')
                    self.notice_button.configure(state='normal')
                    self.model_selector.configure(state='readonly')
                    for button in self.buttons:
                        button.configure(state="normal")
                    if self.preview_source and not self.preview_ready:
                        self.request_quality_preview(delay=0)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def detect(self):
        requested = None if self.model_choice.get() == '自动识别' else resolve_profile(self.model_choice.get())
        if requested is not None:
            self.apply_profile_view(requested)
        self.device_text.set('正在检测屏幕……')
        def job():
            self.events.put(("reset_current",))
            self.stage('识别设备')
            with LG700(profile=requested) as device:
                meta = device.metadata(1)
                profile = device.profile
                model = device.info.get('product_string', profile.name).strip()
                self.events.put(("device", model, device.revision, meta['size'], profile,
                                 connection_identity(device.info)))
                self.note("设备连接成功；备份目录：" + str(device_home(device)))
                self.note(profile.validation_note)
                if is_empty_boot(meta, profile):
                    self.events.put(('boot_empty',))
                    self.note('屏幕尚未保存开机动画；可备份现有待机图，或选择本地图片、视频首次写入。', 100)
                    return
                self.stage('读取硬件动画首帧（只读，不切换模式）')
                try:
                    snapshot = device.read_boot_first_frame()
                    self.events.put(('hardware_first_frame', snapshot))
                    self.note(f"硬件首帧读取完成：共 {snapshot['frame_count']} 帧，"
                              f"仅传输 {snapshot['read_bytes'] / 1024:.1f} KiB，"
                              f"用时 {snapshot['seconds']:.2f} 秒；没有读取整段动画。")
                    self.note('硬件首帧已读取（只读，未转码）', 100)
                except (OSError, ValueError) as error:
                    self.events.put(('media_missing',))
                    self.note('设备已连接，首帧预览未取得：' + str(error) + '；没有使用官方软件配置替代。')
        self.run(job)

    def backup(self):
        profile, identity = self.target_profile(), self.device_identity
        def job():
            self.stage('备份屏幕素材（只读）')
            with self.vendor_session(), LG700(profile=profile, expected_identity=identity) as device:
                directory = new_snapshot(device, "manual")
                device.backup(directory, lambda name, p: self.note(f"备份 {name}：{p:.0%}", p * 100))
            self.note("备份完成：" + str(directory))
        self.run(job)

    def enable_offline(self, device, directory):
        self.note('设置屏幕原生离线播放：图片常驻，视频循环……')
        result = device.set_offline_loop(True, directory)
        if result['device_reconnected']:
            self.note('发送后版本查询曾失败，已重新打开命令口；没有重复发送模式命令。')
        else:
            self.note('离线播放命令已发送，后续版本查询正常；此命令不返回应答。')
        self.note('模式接口没有读取功能；关机开机后的实际播放仍需观察确认。')

    def repair_offline(self):
        profile, identity = self.target_profile(), self.device_identity
        def job():
            self.stage('修复前备份（有已核验备份时先暂停播放）')
            with self.vendor_session(), LG700(profile=profile, expected_identity=identity) as device:
                if is_empty_boot(device.metadata(1), profile):
                    raise ValueError('屏幕尚未保存开机动画，请先写入本地图片或视频，再设置离线播放')
                directory = new_snapshot(device, 'before_playback_repair')
                manifest, paused = backup_before_update(device, directory,
                    lambda name, p: self.note(f'修复前备份 {name}：{p:.0%}', p * 100))
                try:
                    preserve_original(device, directory)
                except Exception:
                    if paused and not manifest['assets']['boot'].get('empty'):
                        device.set_offline_loop(True, directory)
                    raise
                self.stage('设置离线播放模式')
                self.enable_offline(device, directory)
            self.note('离线播放设置已发送；原动画未重写，设备会话结束后恢复临时暂停的官方软件。')
        self.run(job)

    def draw(self):
        if not self.frames:
            return
        im = preview_frame(self.frames[self.preview_index], self.target_profile(), self.preview_size)
        self.photo = ImageTk.PhotoImage(im)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def toggle_preview(self):
        if self.preview_kind == 'hardware_first_frame':
            self.note('当前为硬件首帧静态预览，不会读取完整动画或捕获实时传屏。')
            return
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
        profile, identity = self.target_profile(), self.device_identity
        self.events.put(('custom_source', source))
        def job():
            dest = data_home() / "Generated" / (timestamp() + ".lg700boot")
            data = convert(source, dest, quality, self.note, fill_screen=fill_screen, profile=profile)
            self.events.put(('prepared_asset', quality, data))
            self.note(f"{profile.name} 转换完成：{len(data):,} / {capacity(1, profile):,} 字节，开始自动备份和写入")
            self.write_data(data, profile=profile, expected_identity=identity)
        self.run(job)

    def write_data(self, data, *, profile=None, expected_identity=None):
        profile = resolve_profile(profile) if profile is not None else self.target_profile()
        identity = self.device_identity if expected_identity is None else expected_identity
        frame = unpack_boot(data, profile=profile)[0]
        self.stage('写前备份（尚未发送擦除或写入）')
        with self.vendor_session(), LG700(profile=profile, expected_identity=identity) as device:
            directory = new_snapshot(device, "before_write")
            manifest, paused = backup_before_update(device, directory,
                lambda name, p: self.note(f"写前备份 {name}：{p:.0%}", p * 100))
            self.stage('核验原始备份（尚未发送擦除或写入）')
            try:
                original = preserve_original(device, directory)
            except Exception:
                if paused and not manifest['assets']['boot'].get('empty'):
                    device.set_offline_loop(True, directory)
                raise
            self.note("原始备份已保留：" + str(original))
            self.stage('暂停离线循环（备份已核验，尚未擦写素材）')
            if not paused:
                device.set_offline_loop(False, directory)
            self.note('已发送维护模式 0，写入和读回期间保持；校验成功后再恢复离线循环。')
            self.stage('素材写入及完整读回校验')
            # The same first frame must exist in both playback paths.  The
            # complete package belongs to mode1/selector 1; selector 0 is the
            # mode0 standby image used before the native loop takes over.
            # Write selector 1 first, then selector 0 last so the final record
            # committed by this operation is the requested mode0 first frame.
            assets = [(1, "mode1 开机动画", data), (0, "mode0 待机首帧", frame)]
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
            # REV 0067 can expose the previous standby record when mode 1 is
            # sent immediately after a successful selector-0 commit.  The
            # write/read pair above proves the bytes, but not that the flash
            # record has finished becoming the active record.  Let the
            # device finish its commit, then verify selector 0 once more
            # before changing the playback mode.
            self.stage('等待 mode0 待机记录提交完成')
            time.sleep(3)
            settled, _ = device.read_asset(0, lambda p: self.note(
                f'mode0 待机首帧提交后复核：{p:.0%}', p * 100))
            if settled != frame:
                raise OSError('mode0 待机首帧在提交等待后已回退；未发送 mode1 命令')
            self.note(f'mode0 待机首帧提交后复核一致：{sha256(settled)[:16]}…')
            try:
                self.stage('设置离线播放模式（素材已写入并校验）')
                self.enable_offline(device, directory)
            except Exception as error:
                raise RuntimeError('素材已写入并校验通过，但离线播放模式设置失败：' + str(error)) from error
            # Once mode 1 starts, the firmware reads the boot package while
            # it is playing. A concurrent host read can return a valid-size
            # container with one or more torn JPEG frames. All writes and
            # full readbacks above are already complete, so do not read the
            # boot region again after starting playback. The mode command's
            # communication probe remains read-only and does not resend it.
        self.note("自定义素材写入及 mode0 提交前读回均通过，已切换 mode1 离线循环；播放期间不再读取开机区，避免固件读写竞争。")

    def restore(self):
        profile, identity = self.target_profile(), self.device_identity
        try:
            with self.vendor_session(), LG700(profile=profile, expected_identity=identity) as device:
                path = str(original_directory(device))
        except Exception:
            path = filedialog.askdirectory(title="选择包含 manifest.json 的钛钽原始备份目录",
                                           initialdir=str(data_home() / "Backups"))
        if not path:
            return
        try:
            original = {s: restore_file(path, s, profile=profile) for s in (0, 1)}
        except Exception as error:
            messagebox.showerror("恢复失败", str(error), parent=self.root)
            return
        def job():
            self.stage('恢复前备份')
            with self.vendor_session(), LG700(profile=profile, expected_identity=identity) as device:
                self.stage('恢复前暂停离线循环（恢复备份已核验）')
                device.set_offline_loop(False, path)
                try:
                    directory = new_snapshot(device, "before_restore")
                    device.backup(directory, lambda name, p: self.note(f"恢复前备份 {name}：{p:.0%}", p * 100))
                except (OSError, ValueError) as error:
                    self.note("当前素材无法完整备份，将用现有原厂备份恢复：" + str(error))
                self.stage('恢复素材并完整读回校验')
                for selector in (1, 0):
                    def update(p, selector=selector):
                        fraction = p - 1 if p > 1 else p
                        self.note(f"恢复素材 {selector}：{fraction:.0%}", fraction * 100)
                    device.write_asset(selector, original[selector], path,
                                       update, recovery=True)
                self.events.put(('hardware_size', len(original[1])))
                self.stage('等待恢复的 mode0 待机记录提交完成')
                time.sleep(3)
                settled, _ = device.read_asset(0)
                if settled != original[0]:
                    raise OSError('恢复的待机首帧在提交等待后已回退；未发送 mode1 命令')
                # Restore mode 1 so the backed-up factory boot animation is
                # what the screen plays after power-on. Mode 0 hands control
                # back to the vendor's online sender and can show its cached
                # image instead of the restored boot package.
                device.set_offline_loop(True, path)
            self.note("原始动画和待机图恢复完成，读回校验通过；已自动切换 mode1 离线循环。请关机开机验证。")
        self.run(job)

    def close(self):
        if self.busy:
            messagebox.showinfo("钛钽开机动画工具", "操作尚未完成，请等待写入或备份结束。", parent=self.root)
        else:
            self.quality_worker.close()
            self.root.destroy()


def main():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Local.Tools.LG700BootTool")
    parser = argparse.ArgumentParser(description="TCOMAS LG700 / LG600 / LX800 boot animation tool")
    parser.add_argument('--model', choices=('auto', *(p.key for p in PROFILES)), default='auto',
                        help='select a model; auto requires exactly one supported display')
    parser.add_argument("--inspect", action="store_true", help="read-only device probe, writes JSON to stdout")
    parser.add_argument("--output", type=Path, help="save the read-only diagnostic JSON")
    args = parser.parse_args()
    profile = None if args.model == 'auto' else resolve_profile(args.model)
    if args.inspect:
        status = 0
        try:
            with LG700(profile=profile) as device:
                result = {"model": "TCOMAS " + device.profile.name,
                          "vid": f'{device.profile.vid:04X}', "pid": f'{device.profile.pid:04X}',
                          "size": list(device.profile.size),
                          "visible_shape": device.profile.visible_shape,
                          "validation": device.profile.validation_note,
                          "write_revision_supported": device.revision in device.profile.supported_revisions,
                          "revision": f"{device.revision:04X}",
                          "boot": device.metadata(1), "standby": device.metadata(0)}
        except (OSError, RuntimeError, ValueError) as error:
            # A busy device is a normal diagnostic result. Do not open the
            # frozen executable's fatal-exception dialog over another writer.
            result = {'error': str(error), 'model_requested': args.model, 'read_only': True}
            status = 2
        encoded = json.dumps(result, indent=2, ensure_ascii=False)
        if args.output:
            args.output.write_text(encoded, encoding="utf8")
        elif sys.stdout is not None:
            print(encoded)
        return status
    else:
        root = tk.Tk()
        if needs_startup_notice():
            def start_after_read():
                remember_notice_read()
                App(root, profile=profile)
            StartupNotice(root, on_read=start_after_read, version=APP_VERSION)
        else:
            App(root, profile=profile)
        root.mainloop()


if __name__ == "__main__":
    raise SystemExit(main())
