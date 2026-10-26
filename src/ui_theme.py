"""Self-drawn pastel surfaces and a shared theme; no external image assets."""
import math
from functools import lru_cache
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk

PAGE = '#f3f3fb'
CARD = '#ffffff'
INK = '#292d45'
MUTED = '#7a7e96'
PRIMARY = '#7b7ce9'
PREVIEW = '#151b24'


def theme_font(root, size, weight='normal', family='Microsoft YaHei UI'):
    root = root.winfo_toplevel()
    if not hasattr(root, '_pastel_fonts'):
        root._pastel_fonts = {}
    key = family, size, weight
    if key not in root._pastel_fonts:
        root._pastel_fonts[key] = tkfont.Font(root=root, family=family,
            size=round(size * getattr(root, '_ui_scale', 1)), weight=weight)
    return root._pastel_fonts[key]


class ResponsiveLayout:
    """One resize controller per window, shared by the main and reminder pages."""
    def __init__(self, root):
        self.root = root
        self.pending = None
        self.base = (1120, 860)
        self.callback = None
        self.last_scale = None
        root.bind('<Configure>', self.schedule, add='+')
        root.bind('<Destroy>', self.destroy, add='+')

    @classmethod
    def activate(cls, root, base, callback):
        if not hasattr(root, '_responsive_layout'):
            root._responsive_layout = cls(root)
        layout = root._responsive_layout
        layout.base, layout.callback, layout.last_scale = base, callback, None
        layout.schedule()
        return layout

    def schedule(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self.pending is not None:
            self.root.after_cancel(self.pending)
        self.pending = self.root.after(65, self.apply)

    def destroy(self, event):
        if event.widget is self.root and self.pending is not None:
            self.root.after_cancel(self.pending)
            self.pending = None

    def apply(self):
        self.pending = None
        width, height = self.root.winfo_width(), self.root.winfo_height()
        scale = max(.9, min(width / self.base[0], height / self.base[1]))
        scale = round(scale, 2)
        if scale == self.last_scale:
            return
        self.last_scale = self.root._ui_scale = scale
        for (_, size, _), font in getattr(self.root, '_pastel_fonts', {}).items():
            font.configure(size=max(8, round(size * scale)))
        style = ttk.Style(self.root)
        style.configure('TButton', padding=(round(15 * scale), round(8 * scale)))
        style.configure('Small.TButton', padding=(round(11 * scale), round(5 * scale)))
        style.configure('Primary.TButton', padding=(round(18 * scale), round(10 * scale)))
        style.configure('TCombobox', padding=(round(10 * scale), round(6 * scale)), arrowsize=round(11 * scale))
        style.configure('Crop.TCheckbutton', padding=(0, round(3 * scale), 0, round(3 * scale)))
        resize_control_images(self.root, scale)
        def visit(widget):
            if hasattr(widget, 'apply_scale'):
                widget.apply_scale(scale)
            for child in widget.winfo_children():
                visit(child)
        visit(self.root)
        if self.callback:
            self.callback(scale)


def scalable_image(root, image):
    root = root.winfo_toplevel()
    photo = ImageTk.PhotoImage(image, master=root)
    if not hasattr(root, '_scalable_images'):
        root._scalable_images = []
    root._scalable_images.append((photo, image))
    return photo


def resize_control_images(root, scale):
    """ttk caches element geometry: use size-specific elements to avoid clipping."""
    pixels = max(1, round(22 * scale))
    if not hasattr(root, '_scaled_element_cache'):
        root._scaled_element_cache = {}
    originals = {id(photo): image for photo, image in getattr(root, '_scalable_images', [])}
    style = ttk.Style(root)
    for kind, photos in (('Thumb', getattr(root, '_pastel_scale_images', [])[:3]),
                         ('Crop', getattr(root, '_crop_indicator_images', []))):
        if not photos:
            continue
        key = kind, pixels
        element = f'Pastel{kind}{pixels}.' + ('slider' if kind == 'Thumb' else 'indicator')
        trough = f'Pastel{kind}{pixels}.trough'
        if key not in root._scaled_element_cache:
            images = [ImageTk.PhotoImage(originals[id(photo)].resize((pixels, pixels), Image.Resampling.LANCZOS),
                                         master=root) for photo in photos]
            if kind == 'Thumb':
                style.element_create(element, 'image', images[0], ('disabled', images[2]),
                                     ('active', images[1]), sticky='')
                track_height = max(3, round(6 * scale))
                track = Image.new('RGBA', (max(10, pixels * 2), track_height))
                ImageDraw.Draw(track).rounded_rectangle((0, 0, track.width - 1, track.height - 1),
                                                        radius=track_height / 2, fill='#ececf9')
                images.append(ImageTk.PhotoImage(track, master=root))
                style.element_create(trough, 'image', images[-1],
                                     border=max(1, track_height // 2), padding=0, sticky='we')
            else:
                style.element_create(element, 'image', images[0], ('disabled selected', images[3]),
                                     ('disabled', images[2]), ('selected', images[1]), sticky='w')
            root._scaled_element_cache[key] = images
        if kind == 'Thumb':
            style.layout('Horizontal.TScale', [(trough, {'sticky': 'we',
                'children': [(element, {'side': 'left', 'sticky': ''})]})])
        else:
            style.layout('Crop.TCheckbutton', [('Checkbutton.padding', {'sticky': 'nswe', 'children': [
                (element, {'side': 'left', 'sticky': 'w'}),
                ('Checkbutton.focus', {'side': 'left', 'sticky': 'nswe', 'children': [
                    ('Checkbutton.label', {'sticky': 'nswe'})]})]})])


def background_of(widget):
    try:
        return widget.cget('background')
    except tk.TclError:
        return CARD


def configure_theme(root):
    root.configure(background=PAGE)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=theme_font(root, 10), foreground=INK)
    style.configure('TFrame', background=CARD)
    style.configure('TLabel', background=CARD, foreground=INK)
    style.configure('CardTitle.TLabel', font=theme_font(root, 12, 'bold'))
    style.configure('Subtle.TLabel', foreground=MUTED)
    style.configure('Page.TLabel', background=PAGE, foreground=MUTED, font=theme_font(root, 9))
    # Native ttk buttons retain keyboard navigation and focus. Nine-slice image
    # borders provide real rounded corners at any label width or Windows scale.
    if not getattr(root, '_pastel_button_images', None):
        root._pastel_button_images = []
        for name, colors in (
                ('Soft', ('#f0f0fc', '#e7e7fc', '#dddff8', '#f7f7fb')),
                ('Primary', (PRIMARY, '#8986ed', '#6668d6', '#d6d7ef'))):
            images = []
            for color in colors:
                im = Image.new('RGBA', (240, 120))
                ImageDraw.Draw(im).rounded_rectangle((1, 1, 239, 119), radius=35, fill=color)
                images.append(ImageTk.PhotoImage(im.resize((80, 40), Image.Resampling.LANCZOS), master=root))
            root._pastel_button_images.extend(images)
            element = name + '.rounded'
            style.element_create(element, 'image', images[0], ('disabled', images[3]),
                                 ('pressed', images[2]), ('active', images[1]),
                                 border=13, padding=0, sticky='nsew')
            layout = [(element, {'sticky': 'nsew', 'children': [
                ('Button.padding', {'sticky': 'nsew', 'children': [
                    ('Button.focus', {'sticky': 'nsew', 'children': [
                        ('Button.label', {'sticky': 'nsew'})]})]})]})]
            style.layout('TButton' if name == 'Soft' else 'Primary.TButton', layout)
    style.configure('TButton', padding=(15, 8), foreground='#626780', background=CARD,
                    borderwidth=0, focusthickness=1, focuscolor='#a8a6db', relief='flat')
    style.map('TButton', foreground=[('disabled', '#b5b7ca')])
    style.configure('Small.TButton', padding=(11, 5), font=theme_font(root, 9))
    style.configure('Primary.TButton', foreground='#ffffff', padding=(18, 10),
                    font=theme_font(root, 10, 'bold'), focuscolor='#ffffff')
    style.map('Primary.TButton', foreground=[('disabled', '#f7f7ff'), ('!disabled', '#ffffff')])
    style.configure('Notice.Primary.TButton', background=PAGE)
    style.configure('TCombobox', padding=(10, 6), fieldbackground='#f5f5fc',
                    background='#f5f5fc', bordercolor='#e8e9f6', borderwidth=1,
                    arrowcolor='#8586a9', arrowsize=11)
    style.map('TCombobox', fieldbackground=[('readonly', '#f5f5fc'), ('disabled', '#f7f7fb')],
              foreground=[('disabled', '#b0b3c7'), ('readonly', INK)],
              selectbackground=[('readonly', '#f5f5fc')], selectforeground=[('readonly', INK)])
    if not getattr(root, '_pastel_scale_images', None):
        root._pastel_scale_images = []
        for color in (PRIMARY, '#918cf3', '#d6d6e8'):
            im = Image.new('RGBA', (72, 72))
            draw = ImageDraw.Draw(im)
            draw.ellipse((4, 4, 68, 68), fill=color)
            draw.ellipse((24, 24, 48, 48), fill='white')
            root._pastel_scale_images.append(scalable_image(root, im.resize((22, 22), Image.Resampling.LANCZOS)))
        normal, active, disabled = root._pastel_scale_images
        style.element_create('Pastel.slider', 'image', normal, ('disabled', disabled),
                             ('active', active), sticky='')
        track = Image.new('RGBA', (120, 18))
        ImageDraw.Draw(track).rounded_rectangle((0, 0, 119, 17), radius=8, fill='#ececf9')
        track_photo = ImageTk.PhotoImage(track.resize((40, 6), Image.Resampling.LANCZOS), master=root)
        root._pastel_scale_images.append(track_photo)
        style.element_create('Pastel.trough', 'image', track_photo, border=3, padding=0, sticky='we')
        style.layout('Horizontal.TScale', [('Pastel.trough', {'sticky': 'we',
            'children': [('Pastel.slider', {'side': 'left', 'sticky': ''})]})])
    style.configure('Horizontal.TScale', background=CARD, troughcolor='#ececf9',
                    bordercolor=CARD, lightcolor=CARD, darkcolor=CARD,
                    troughrelief='flat', troughborderwidth=0, borderwidth=0)
    style.configure('TCheckbutton', background=CARD, foreground=INK)
    style.map('TCheckbutton', background=[('active', CARD)], foreground=[('disabled', '#b2b5c7')])
    return style


@lru_cache(maxsize=1)
def _mesh_base():
    """A blue/lilac/pink mesh, calculated at low resolution then smoothed."""
    width, height = 240, 96
    image = Image.new('RGB', (width, height))
    pixels = image.load()
    spots = ((.08, .08, .75, .95, (194, 225, 255)),
             (.62, -.16, .65, .85, (215, 204, 255)),
             (.95, .95, .72, .95, (255, 217, 235)),
             (.08, 1.25, .65, .75, (211, 247, 239)))
    for y in range(height):
        for x in range(width):
            rgb = [246., 246., 255.]
            for cx, cy, sx, sy, color in spots:
                a = .66 * math.exp(-2.6 * (((x / width - cx) / sx) ** 2 + ((y / height - cy) / sy) ** 2))
                rgb = [old * (1 - a) + target * a for old, target in zip(rgb, color)]
            pixels[x, y] = tuple(round(c) for c in rgb)
    return image


def mesh_image(size):
    return _mesh_base().resize(size, Image.Resampling.LANCZOS)


class MeshHeader(tk.Canvas):
    def __init__(self, parent, title, subtitle, *, eyebrow='TCOMAS  /  屏幕创作', version='', height=96):
        super().__init__(parent, height=height, background=PAGE, highlightthickness=0)
        self.title, self.subtitle, self.eyebrow, self.version = title, subtitle, eyebrow, version
        self._size = None
        self.scale = 1
        self.base_height = height
        self.bind('<Configure>', self.paint)

    def apply_scale(self, scale):
        self.scale = scale
        self._size = None
        self.configure(height=round(self.base_height * scale))
        self.paint()

    def paint(self, event=None):
        size = max(1, self.winfo_width()), max(1, self.winfo_height())
        if size == self._size:
            return
        self._size = size
        image = mesh_image(size).convert('RGBA')
        mask = Image.new('L', size)
        s = self.scale
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius=round(20 * s), fill=255)
        image.putalpha(mask)
        self.photo = ImageTk.PhotoImage(image, master=self)
        self.delete('all')
        self.create_image(0, 0, anchor='nw', image=self.photo)
        self.create_text(24 * s, 17 * s, anchor='w', text=self.eyebrow, fill='#6e719a',
                         font=theme_font(self, 9, 'bold'))
        self.create_text(23 * s, 46 * s, anchor='w', text=self.title, fill='#343650',
                         font=theme_font(self, 22, 'bold'))
        self.create_text(24 * s, 77 * s, anchor='w', text=self.subtitle, fill='#737791',
                         font=theme_font(self, 9))
        if self.version:
            self.create_text(size[0] - 24 * s, 22 * s, anchor='e', text='v' + self.version,
                             fill='#8182a3', font=theme_font(self, 10, family='Segoe UI'))
        if size[0] > 850 * s:
            for x1, y1, x2, y2, color in ((170, 25, 107, 88, '#ffffff'), (143, 34, 86, 91, '#c9c5ee'), (116, 17, 53, 80, '#ffffff')):
                self.create_oval(size[0] - x1 * s, y1 * s, size[0] - x2 * s, y2 * s,
                                 outline=color, width=max(1, round(2 * s)))


class RoundedCard(tk.Canvas):
    """Rounded, softly edged card with an ordinary frame for native controls."""
    def __init__(self, parent, padding=14):
        super().__init__(parent, highlightthickness=0, borderwidth=0,
                         background=background_of(parent), height=80)
        self.padding = self.base_padding = padding
        self.scale = 1
        self.body = tk.Frame(self, background=CARD)
        self.window = self.create_window(padding, padding, anchor='nw', window=self.body)
        self._desired = None
        self._size = None
        self.bind('<Configure>', self.resize)
        self.body.bind('<Configure>', self.content_changed)

    def apply_scale(self, scale):
        self.scale = scale
        self.padding = round(self.base_padding * scale)
        self.coords(self.window, self.padding, self.padding)
        self._size = None
        self.content_changed(None)
        self.resize(None)

    def content_changed(self, event):
        desired = self.body.winfo_reqheight() + self.padding * 2
        if desired != self._desired:
            self._desired = desired
            self.configure(height=desired)

    def resize(self, event):
        width, height = max(1, self.winfo_width()), max(1, self.winfo_height())
        self.itemconfigure(self.window, width=max(1, width - self.padding * 2),
                           height=max(1, height - self.padding * 2))
        size = (width, height)
        if self._size == size:
            return
        self._size = size
        image = Image.new('RGBA', (width * 2, height * 2))
        draw = ImageDraw.Draw(image)
        radius = round(33 * self.scale)
        draw.rounded_rectangle((3, 5, width * 2 - 3, height * 2 - 1), radius=radius, fill='#e9e8f5')
        draw.rounded_rectangle((2, 2, width * 2 - 3, height * 2 - 5), radius=radius, fill=CARD)
        image = image.resize(size, Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image, master=self)
        self.delete('card')
        self.create_image(0, 0, anchor='nw', image=self.photo, tags='card')
        self.tag_lower('card')


class SoftProgress(tk.Canvas):
    """Rounded progress track with a blue-to-lilac fill; keeps value API."""
    def __init__(self, parent, maximum=100):
        super().__init__(parent, height=9, highlightthickness=0, background=background_of(parent))
        self.maximum, self.value = maximum, 0
        self.scale = 1
        self.bind('<Configure>', lambda event: self.paint())

    def apply_scale(self, scale):
        self.scale = scale
        self.configure(height=max(5, round(9 * scale)))
        self.paint()

    def __setitem__(self, name, value):
        if name == 'value':
            self.value = max(0, min(self.maximum, float(value)))
            self.paint()
        else:
            super().__setitem__(name, value)

    def __getitem__(self, name):
        return self.value if name == 'value' else super().__getitem__(name)

    def paint(self):
        width, height = self.winfo_width(), max(5, round(9 * self.scale))
        if width <= 1:
            return
        image = Image.new('RGBA', (width * 3, height * 3))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, width * 3 - 1, height * 3 - 1), radius=height * 1.5, fill='#e6e7f5')
        fill = round(width * 3 * self.value / self.maximum)
        if fill:
            strip = Image.new('RGBA', (max(1, fill), height * 3))
            pixels = ImageDraw.Draw(strip)
            for x in range(fill):
                t = x / max(1, width * 3 - 1)
                color = tuple(round(a * (1 - t) + b * t) for a, b in zip((112, 172, 246), (167, 126, 238)))
                pixels.line((x, 0, x, height * 3), fill=color)
            mask = Image.new('L', strip.size)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, fill - 1, height * 3 - 1), radius=height * 1.5, fill=255)
            image.paste(strip, (0, 0), mask)
        self.photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)
        self.delete('all')
        self.create_image(0, 0, anchor='nw', image=self.photo)
