"""A read-first landing page with no device or process-control side effects."""
import re
import tkinter as tk
from tkinter import ttk, messagebox
from ui_theme import configure_theme, MeshHeader, RoundedCard, ResponsiveLayout, theme_font, PAGE, PRIMARY, MUTED


NOTICES = (
    ('关闭官方钛钽水冷驱动',
     '官方软件通过 USB 持续传送实时画面；\n'
     '本程序将图片或动画写入屏幕内置存储，由屏幕固件独立播放。\n'
     '两者同时使用时，官方实时传屏会接管显示，覆盖已保存动画的画面。'
     '因此，建议完全退出官方钛钽水冷软件（包括托盘），并关闭其开机自启动。'),
    ('写入期间保持供电和连接',
     '备份、写入和读回校验完成前，请勿断电、拔出 USB 或关闭本工具。\n'
     '如果操作报错，请退出本程序并关机断电（拔掉电源再插上），再开机重试。'),
    ('确认型号与验证范围',
     '支持 LG600、LG700、LX800\n'
     'LG700 经过实机验证'),
    ('留意素材容量并保留备份',
     '容量按转换后的动画包计算，超限时请降低画质或缩短视频。'
     '程序会在写入前备份、写入后完整读回校验；完成后再检查屏幕的离线播放效果。'),
)


def wrap_notice_body(text, font, width):
    """Wrap CJK text to the card width while keeping model names together."""
    lines, sizes = [], {}
    for paragraph in text.split('\n'):
        line, used = '', 0
        for token in re.findall(r'[A-Za-z0-9_./%×:+-]+|.', paragraph):
            if token not in sizes:
                sizes[token] = font.measure(token)
            if line and used + sizes[token] > width:
                lines.append(line.rstrip())
                line, used = '', 0
                token = token.lstrip()
            line += token
            used += sizes.get(token, 0)
        lines.append(line.rstrip())
    return '\n'.join(lines)


class StartupNotice:
    """Construct the main application only after the user clicks 已阅读."""
    def __init__(self, root, on_read, version, returning=False, on_close=None):
        self.root = root
        self.on_read = on_read
        self._entered = False
        root.title(f'钛钽开机动画工具 v{version} · 提醒事项')
        root.geometry('960x780')
        root.minsize(900, 740)
        root.protocol('WM_DELETE_WINDOW', on_close or root.destroy)
        configure_theme(root)
        self.frame = tk.Frame(root, background=PAGE, padx=22, pady=20)
        self.frame.pack(fill='both', expand=True)
        MeshHeader(self.frame, '开始之前，先了解这几件事',
            '钛钽开机动画工具  ·  LG700 / LG600 / LX800',
            eyebrow='TCOMAS  /  提醒事项', version=version).pack(fill='x', pady=(0, 15))
        footer = tk.Frame(self.frame, background=PAGE)
        footer.pack(side='bottom', fill='x', pady=(10, 0))
        self.footer_label = tk.Label(footer,
                 text=('可随时从主界面再次查看提醒事项。' if returning else
                       '首次使用请阅读以上说明。\n点击“已阅读”后，下次启动直接进入主界面。'),
                 background=PAGE, foreground=MUTED, justify='left', anchor='w', font=theme_font(root, 9),
                 wraplength=630)
        self.footer_label.pack(side='left', fill='x', expand=True, padx=(0, 18))
        self.read_button = ttk.Button(footer, text='返回主界面' if returning else '已阅读，进入主界面', command=self.accept,
                                      style='Notice.Primary.TButton')
        self.read_button.pack(side='right')
        self.body_labels = []
        content = tk.Frame(self.frame, background=PAGE)
        content.pack(fill='both', expand=True)
        content.columnconfigure(0, weight=1)
        for index, (title, text) in enumerate(NOTICES, 1):
            card = RoundedCard(content, padding=15)
            card.grid(row=index, column=0, sticky='nsew', pady=(0, 9))
            content.rowconfigure(index, weight=1)
            heading = ttk.Frame(card.body)
            heading.pack(fill='x')
            ttk.Label(heading, text=f'{index:02d}', foreground=PRIMARY,
                      font=theme_font(root, 14, 'bold', family='Segoe UI')).pack(side='left', padx=(0, 12))
            ttk.Label(heading, text=title, font=theme_font(root, 15, 'bold')).pack(side='left')
            body = ttk.Label(card.body, text=text, foreground='#62677f', font=theme_font(root, 13),
                             wraplength=820, justify='left')
            body.notice_text = text
            body.pack(fill='x', pady=(7, 0))
            self.body_labels.append(body)
        self.frame.bind('<Configure>', self.resize)
        ResponsiveLayout.activate(root, (960, 780), self.apply_ui_scale)

    def apply_ui_scale(self, scale):
        self.frame.configure(padx=round(22 * scale), pady=round(20 * scale))
        self.footer_label.configure(wraplength=max(240, self.frame.winfo_width() - round(285 * scale)))
        self.resize(None)

    def resize(self, event):
        scale = getattr(self.root, '_ui_scale', 1)
        width = max(240, self.frame.winfo_width() - round(84 * scale))
        for label in self.body_labels:
            label.configure(wraplength=0, text=wrap_notice_body(label.notice_text, theme_font(self.root, 13), width))

    def accept(self):
        if self._entered:
            return
        self._entered = True
        self.read_button.configure(state='disabled')
        self.frame.pack_forget()
        try:
            self.on_read()
        except Exception as error:
            self._entered = False
            self.frame.pack(fill='both', expand=True)
            self.read_button.configure(state='normal')
            messagebox.showerror('无法打开主界面', str(error), parent=self.root)
            return
        self.frame.destroy()
