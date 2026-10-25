# FFmpeg 来源与重建

Windows 便携 ZIP 中的 `ffmpeg.exe` 使用以下未修改的上游源码独立构建，通过外部进程供本工具调用，不依赖原厂软件或其 DLL。

| 组件 | 版本 | 上游源码 |
| --- | --- | --- |
| FFmpeg | 8.1.1 | <https://ffmpeg.org/releases/ffmpeg-8.1.1.tar.xz> |
| zlib | 1.3.1 | <https://zlib.net/fossils/zlib-1.3.1.tar.gz> |

本构建未启用 GPL、nonfree 或 version3 组件，FFmpeg 使用 LGPL-2.1-or-later，zlib 使用 zlib 许可。许可证和构建配置随便携包提供。项目代码的 MIT 许可不覆盖第三方组件。

## 下载对应源码

同一 [v1.0.0 Release](https://github.com/GuoWQ222/TCOMAS-Boot-Animation-Tool/releases/tag/v1.0.0) 提供：

`TCOMAS-Boot-Animation-Tool-v1.0.0-third-party-sources.zip`

其中包含准确的上游源码压缩包、源文件 SHA-256、构建脚本、构建补丁及说明。`SHA256SUMS.txt` 同时列出公开下载资产的校验值。上游源码压缩包保持原样；构建时仅对 FFmpeg 的 Makefile 应用响应文件生成补丁，避免 Windows 命令行长度限制，不修改媒体处理实现。zlib 未做修改。

## 重建

发布构建环境为 Windows x64、MinGW-w64 GCC 13.2.0、GNU Make 4.4.1、Bash、NASM。可使用提供这些工具的 MinGW-w64 环境；确保 `gcc`、`g++`、`ar`、`nm`、`ranlib`、`strip`、`gmake`、`patch` 和 `nasm` 在 PATH 中。

1. 解压源码包中的 `ffmpeg-8.1.1.tar.xz` 和 `zlib-1.3.1.tar.gz`，使两个目录并列。
2. 在其父目录运行随源码包或本仓库提供的脚本：

```bash
PATCH_BIN=/usr/bin/patch JOBS=8 bash /path/to/scripts/build_ffmpeg_windows.sh
```

脚本先构建静态 zlib，再构建 FFmpeg。输出为 `ffmpeg-8.1.1/ffmpeg.exe`。配置参数完整保存在脚本及便携包内的 `FFmpeg-build-info.json`；FFmpeg 也可以通过 `-version`、`-buildconf`、`-L` 自行显示这些信息。

此构建保留本地视频解码、FPS/缩放/填充滤镜、JPEG 编码和图片序列输出，不启用网络输入，也不包含 ffplay 或 ffprobe。视频的分辨率、帧率及画质仍由本工具原有参数决定。
