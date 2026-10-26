"""Real Tk visual/interaction checks with all hardware and vendor I/O isolated."""
import io
import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageGrab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
import lg700_tool
from startup_notice import StartupNotice
from device_profiles import PROFILES
from convert_media import build_asset
from ui_theme import mesh_image

OUT = ROOT / 'build/ui-validation'
OUT.mkdir(parents=True, exist_ok=True)

def settle(root, seconds=.2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        root.update()
        time.sleep(.01)

def capture(root, name):
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(OUT / name)

def visible_inside(widget, root):
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    details = f'{widget}: {x},{y} {widget.winfo_width()}x{widget.winfo_height()}, root {root.winfo_rootx()},{root.winfo_rooty()} {root.winfo_width()}x{root.winfo_height()}'
    assert x >= root.winfo_rootx() and y >= root.winfo_rooty(), str(widget)
    assert x + widget.winfo_width() <= root.winfo_rootx() + root.winfo_width(), details
    assert y + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height(), details

results = {'version': lg700_tool.APP_VERSION, 'no_USB_access': True,
           'real_official_software_touched': False, 'main_device_detection_mocked': True}
with tempfile.TemporaryDirectory() as temp:
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = lambda kind, value, tb: (_ for _ in ()).throw(value)
    opened = []
    with patch.object(lg700_tool.App, 'detect') as detect, \
         patch.object(lg700_tool.App, 'choose_media') as choose, \
         patch.object(lg700_tool.App, 'backup') as backup, \
         patch.object(lg700_tool.App, 'repair_offline') as repair, \
         patch.object(lg700_tool.App, 'restore') as restore, \
         patch.object(lg700_tool, 'data_home', return_value=Path(temp)), \
         patch.object(lg700_tool, 'LG700', side_effect=AssertionError('NO DEVICE ACCESS')) as hid, \
         patch.object(lg700_tool, 'pause_vendor', side_effect=AssertionError('No real process suspension')) as pause:
        try:
            page = StartupNotice(root, lambda: opened.append(lg700_tool.App(root)), lg700_tool.APP_VERSION)
            root.geometry('960x780+0+0')
            root.attributes('-topmost', True)
            root.deiconify()
            settle(root)
            detect.assert_not_called(); pause.assert_not_called(); hid.assert_not_called()
            for size in ('900x740', '960x780'):
                root.geometry(size + '+0+0'); settle(root)
                for label in page.body_labels:
                    visible_inside(label, root)
                    assert label.winfo_y() + label.winfo_height() <= label.master.winfo_height()
                visible_inside(page.read_button, root)
            capture(root, 'startup-notice.png')
            page.read_button.invoke(); settle(root)
            app = opened[0]
            assert len(opened) == 1 and not page.frame.winfo_exists()
            assert not hasattr(app, 'stop_official') and not hasattr(app, 'vendor_control')
            assert not hasattr(app, 'write_current')
            page.accept(); assert len(opened) == 1
            results['startup_notice'] = {'read_first': True, 'no_process_control_before_read': True,
                                        'opens_main_once': True, 'minimum_window_text_fits': True}
            sample = mesh_image((720, 540))
            draw = ImageDraw.Draw(sample)
            draw.rounded_rectangle((70, 72, 650, 467), radius=55, outline='white', width=4)
            font = ImageFont.truetype('C:/Windows/Fonts/segoeuib.ttf', 68)
            small = ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf', 22)
            draw.text((360, 240), 'MAKE IT YOURS', font=font, anchor='mm', fill='#555588')
            draw.text((360, 310), 'Your favorite moments, on display.', font=small, anchor='mm', fill='#7878a2')
            source = Path(temp) / 'synthetic.png'
            sample.save(source)
            results['models'] = []
            for profile in PROFILES:
                app.apply_profile_view(profile)
                app.model_choice.set(profile.name)
                app.device_text.set(f'{profile.name} · 离线界面验证 · 未连接 USB')
                app.file_text.set('合成预览素材 · 仅用于界面验证')
                app.apply_preview_data(75, build_asset(source, profile=profile, fill_screen=True))
                app.status.set('正在写入动画 · 界面演示，无设备操作')
                app.progress['value'] = 47
                app.progress_text.set('47%')
                for size in ('1080x840', '1120x860'):
                    root.geometry(size + '+0+0'); settle(root)
                    capture(root, 'current-layout.png')
                    for widget in [*app.buttons, app.notice_button, app.fill_checkbutton,
                                   app.quality_hint_label, app.progress, app.log, app.canvas, app.backup_label]:
                        visible_inside(widget, root)
                    assert app.log.winfo_rooty() + app.log.winfo_height() <= app.log.master.winfo_rooty() + app.log.master.winfo_height()
                    capture(root, profile.key + ('-minimum.png' if size.startswith('1080') else '.png'))
                results['models'].append({'model': profile.key, 'shape': profile.visible_shape,
                                          'minimum_layout_fits': True, 'preview_dimensions': app.preview_size})
            # Real button command routing and native disabled states, with mocked actions.
            for button in app.buttons:
                button.configure(state='disabled'); button.invoke()
            choose.assert_not_called(); backup.assert_not_called(); repair.assert_not_called(); restore.assert_not_called()
            capture(root, 'disabled-controls.png')
            for button in app.buttons:
                button.configure(state='normal')
            for button in app.buttons[:5]: button.invoke()
            assert detect.call_count == 2
            choose.assert_called_once(); backup.assert_called_once(); repair.assert_called_once(); restore.assert_called_once()
            for value in (0, 1, 50, 100):
                app.note('进度展示（无设备操作）', value); settle(root, .15)
                assert app.progress['value'] == value and app.progress_text.get() == f'{value}%'
            app.quality_scale.set(64); settle(root)
            assert app.selected_quality() == 64
            app.fill_help.hover_help.show(); settle(root)
            assert app.fill_help.hover_help.window is not None
            app.fill_help.hover_help.hide()
            # Reminder navigation reuses the existing app and preserves media.
            frames, quality = app.frames, app.selected_quality()
            app.notice_button.invoke(); settle(root)
            assert not app.main_frame.winfo_ismapped()
            notice_frames = [w for w in root.winfo_children() if isinstance(w, tk.Frame) and w is not app.main_frame]
            assert len(notice_frames) == 1
            def buttons(widget):
                for child in widget.winfo_children():
                    if isinstance(child, __import__('tkinter').ttk.Button): yield child
                    yield from buttons(child)
            next(buttons(notice_frames[0])).invoke(); settle(root)
            assert app.main_frame.winfo_ismapped() and app.frames is frames and app.selected_quality() == quality
            assert detect.call_count == 2
            # Larger windows scale actual controls and raster preview, not just cards.
            root.geometry('1120x860+0+0'); settle(root, .35)
            base_preview = app.preview_size
            base_button_height = app.buttons[3].winfo_height()
            root.geometry('1440x1100+0+0'); settle(root, .6)
            assert root._ui_scale > 1.2
            assert app.preview_size[0] > base_preview[0]
            assert app.buttons[3].winfo_height() > base_button_height
            for widget in [*app.buttons, app.notice_button, app.progress, app.log]: visible_inside(widget, root)
            capture(root, 'large-window.png')
            root.state('zoomed'); settle(root, .7)
            maximized = [root.winfo_width(), root.winfo_height()]
            for widget in [*app.buttons, app.notice_button, app.progress, app.log, app.canvas, app.backup_label]: visible_inside(widget, root)
            thumb_x = [x for x in range(app.quality_scale.winfo_width())
                       if app.quality_scale.identify(x, app.quality_scale.winfo_height() // 2).endswith('.slider')]
            assert thumb_x, 'Scale thumb must be identifiable'
            fraction = (min(thumb_x) + max(thumb_x)) / (2 * app.quality_scale.winfo_width())
            assert .45 < fraction < .65, f'Incorrect resized thumb position: {fraction}'
            capture(root, 'maximized.png')
            root.state('normal'); root.geometry('1120x860+0+0'); settle(root, .6)
            assert .99 <= root._ui_scale <= 1.01
            results['responsive'] = {'larger_controls': True, 'larger_preview': True, 'reminder_round_trip_keeps_media': True,
                                     'maximized': maximized, 'restores_normal_size': True}
            results['controls'] = {'button_routing': True, 'disabled_buttons_do_not_invoke': True,
                                    'progress_0_1_50_100': True, 'quality_slider': True,
                                    'official_stop_feature_removed': True, 'help_popup': True}
            hid.assert_not_called(); pause.assert_not_called()
        finally:
            for app in opened:
                app.quality_worker.close()
            for task in root.tk.call('after', 'info'): root.after_cancel(task)
            root.destroy()
            logging.shutdown()
    logging.shutdown()
(OUT / 'result.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
print(json.dumps(results, ensure_ascii=False))
