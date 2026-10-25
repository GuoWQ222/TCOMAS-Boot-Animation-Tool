# Third-party notices

LG700Tool v1.0.0 includes the following components in the Windows executable:

| Component | Version | License / reference |
| --- | --- | --- |
| CPython | 3.13.13 | PSF License and bundled notices, `licenses/third-party/Python-LICENSE.txt` |
| Pillow | 12.2.0 | HPND and bundled notices, `licenses/third-party/Pillow/LICENSE` |
| hidapi Python package / HIDAPI | 0.15.0 | BSD license option, `licenses/third-party/hidapi/` |
| PyInstaller bootloader | 6.22.3 | GPL with bootloader exception, `licenses/third-party/pyinstaller/COPYING.txt` |
| Tcl/Tk | 8.6 | Python runtime notices and `licenses/third-party/tk8.6-license.terms` |

The application source is licensed separately under MIT. The third-party license notices remain applicable to their respective components.

The Windows portable ZIP also contains **FFmpeg 8.1.1**, invoked as a separate executable. This build disables GPL, nonfree and version3 components, and uses FFmpeg under LGPL-2.1-or-later. Its statically linked zlib 1.3.1 uses the zlib license. The executable's full license output and upstream license texts are included under `licenses/third-party/FFmpeg/` and `licenses/third-party/zlib/`.

Unmodified FFmpeg and zlib source archives, the exact build script, source checksums, and rebuilding instructions are provided in the same Release as `TCOMAS-Boot-Animation-Tool-v1.0.0-third-party-sources.zip`. See `docs/ffmpeg-sources.md`. The project's MIT license does not replace these component licenses.

No vendor installer, firmware, factory animation, device backup, user video, or proprietary native vendor library is distributed by this project.
