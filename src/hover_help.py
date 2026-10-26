"""A delayed, screen-bounded help bubble for a small question-mark icon."""
import tkinter as tk
from tkinter import ttk
from ui_theme import theme_font


class HoverHelp:
    def __init__(self, widget, text, delay_ms=500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.pending = None
        self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(self.delay_ms, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists():
            return
        popup = tk.Toplevel(self.widget)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes('-topmost', True)
        tk.Label(popup, text=self.text, justify='left', wraplength=340,
            font=theme_font(self.widget, 10), background='#fcfbff', foreground='#626780',
            relief='solid', borderwidth=1, padx=14, pady=12).pack()
        popup.update_idletasks()
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        x = max(0, min(x, popup.winfo_screenwidth() - popup.winfo_reqwidth() - 8))
        y = max(0, min(y, popup.winfo_screenheight() - popup.winfo_reqheight() - 8))
        popup.geometry(f'+{x}+{y}')
        popup.deiconify()
        self.window = popup

    def hide(self, event=None):
        if self.pending is not None:
            try:
                self.widget.after_cancel(self.pending)
            except tk.TclError:
                pass
            self.pending = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class QuestionIcon(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, width=22, height=22, highlightthickness=0,
            background=ttk.Style(parent).lookup('TFrame', 'background'), cursor='question_arrow')

    def apply_scale(self, scale):
        self.configure(width=round(22 * scale), height=round(22 * scale))
        self.delete('all')
        self.create_oval(2 * scale, 2 * scale, 20 * scale, 20 * scale, fill='#eeedfc', outline='#eeedfc')
        self.create_text(11 * scale, 11 * scale, text='?', fill='#8581d3',
                         font=theme_font(self, 10, 'bold', family='Segoe UI'))


def question_icon(parent, text):
    icon = QuestionIcon(parent)
    icon.apply_scale(getattr(icon.winfo_toplevel(), '_ui_scale', 1))
    icon.hover_help = HoverHelp(icon, text)
    return icon
