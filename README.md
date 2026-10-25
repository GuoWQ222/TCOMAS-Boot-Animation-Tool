# LG700 开机动画工具

将本地图片或视频保存到 **TCOMAS / 钛坦 LG700 水冷屏幕**，在 Windows 启动前显示图片或循环播放动画。

这是独立的社区工具。完成写入后，屏幕可自行播放，无需原厂软件常驻。项目不升级或替换屏幕的可执行固件。

![LG700 开机动画工具界面预览](docs/assets/app-screenshot.png)

## 下载和使用

1. 从 [Releases](https://github.com/GuoWQ222/TCOMAS-Boot-Animation-Tool/releases) 下载 Windows x64 便携包并解压。
2. 视频和 GIF 转换需要 **FFmpeg**。从 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html#build-windows) 获取 Windows 版本，将 `ffmpeg.exe` 放在 `LG700Tool.exe` 同一目录，也可使用 PATH 中的 FFmpeg。公开包不捆绑 FFmpeg。
3. 连接 LG700，运行 `LG700Tool.exe`，等待设备识别。Release 已打包 Python 和所需库，不需要另行安装 Python。
4. 点击“写入自定义视频”选择自己的视频或图片。程序自动转换、检查容量、备份现有素材、写入、完整读回比较，并设置离线播放模式。
5. 让操作完成。随后真正切断屏幕供电再开机，观察进入 Windows 前的显示效果。

图片为单帧常驻，视频为多帧循环；视频首帧还会自动保存为静态回退图。写入后可关闭本工具。原厂软件若继续传屏，会接管屏幕显示。

## 功能

- 识别水冷型号、固件 REV 和硬件中已保存的动画大小。
- 读取 T'OWEI 中当前选中的图片或视频；此入口需要该软件留下的配置和可访问的本地素材。**只使用本地文件时不需要安装原厂软件。**
- 本地动画预览；拖动转换画质滑块后计算真实的硬件动画包大小，超限明确报错。
- 写入前自动保存现有开机动画和待机图，写入后全量读回并逐字节比较。
- 首次原始备份单独保留；可恢复原始动画和待机图。
- 修复已经保存动画的离线播放模式，无需重新转换或重写动画。
- 预览与写入共享相同转码结果；目标内容相同时，完整核验后跳过重复擦写。
- 备份、写入、校验整个会话暂停原厂传屏，结束或异常时恢复。
- 保留 **8,192 字节**读写分块；读取响应严格校验、有限只读重试、USB 重连后重新完整读回。擦写和提交命令不自动重发。

## 容量及兼容性

| 项目 | 范围 |
| --- | --- |
| 实测设备 | LG700，USB VID `1A2C` / PID `151C`，REV `0067` |
| 其他允许版本 | REV `0075` 的存储结构和播放模式经公开固件静态核对，未做同等硬件实测 |
| 分辨率 | 640 × 480 |
| 视频转换 | 30 FPS，非渐进 JPEG 帧序列 |
| 开机分区 | 11 MiB；扣除记录头，素材上限 **11,534,316 字节** |
| 待机分区 | 1 MiB；素材上限 1,048,556 字节 |
| 帧数 | 最多 4,095 帧，单帧不超过 200,000 字节 |
| 本机发布验证 | 240 帧动画完整读回一致；断电开机前播放经用户验证；卸载原厂软件后仍播放 |

容量按**转换后的硬件数据**计算，不能只看原 MP4 大小。工具不会为了通过容量检查而偷偷裁剪、降帧率或改变所选画质。本地预览为 30 FPS，屏幕的实际解码速度和节奏由固件决定。其他型号或未经验证的 REV 会限制写入。

## 备份与日志

路径按当前 Windows 用户生成，不依赖用户名或安装目录：

```text
%LOCALAPPDATA%\TCOMAS-LG700-Tool\Backups\<设备标识>\
  Original\                    首次原始备份，后续写入不会覆盖
  <时间戳>_before_write\       每次写入前的独立快照
%LOCALAPPDATA%\TCOMAS-LG700-Tool\Logs\LG700Tool.log
```

备份包含 `boot.bin`、`standby.bin`、`manifest.json` 和 SHA-256。日志记录操作阶段、偏移、长度和异常堆栈，不保存视频帧。

如果设备在第一次备份前已被其他工具修改，`Original` 只能保存当时的内容，无法凭空恢复出厂素材。可自行选择此前保存的原始备份目录；本项目不分发厂商动画或固件。

## 故障处理

写前备份失败会停止，尚未发送任何擦写命令；已经保存在屏幕中的动画仍可播放。应查看报错阶段和日志，不要把备份读取失败当成动画被覆盖。

如果命令口持续返回旧响应或版本查询超时，先让操作停止，保存工作，正常关机后让**水冷屏幕完全熄灭至少 20 秒**。主机重启不一定切断 USB 或外接集线器供电。切勿在写入进行中断电。

单次读取 6.73 MiB 动画约需 109 秒，完整备份和最终校验各需一次；不要因进度较慢强制退出。

## 从源码运行

Windows x64，建议 Python 3.13，需提供 Tkinter：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\lg700_tool.py
```

只读设备诊断：

```powershell
.\.venv\Scripts\python.exe src\lg700_tool.py --inspect --output device-info.json
```

## 测试和构建

公开测试使用代码生成的纯色 JPEG，不需要设备，不读取任何用户数据，也不包含真实备份。发布前另在真实 LG700 上验证过完整流程；离线测试不能替代硬件测试。

```powershell
.\.venv\Scripts\python.exe scripts\run_tests.py
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\scripts\build.ps1 -Python .\.venv\Scripts\python.exe
```

生成的程序位于 `dist\LG700Tool.exe`。项目的七个运行时 Python 文件与当前已验证 Release 对应，公开测试只移除了私人备份依赖。协议和已知边界见 [协议说明](docs/protocol.md)，版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## 贡献与仓库权限

本仓库公开，原仓库的直接 push 和合并由维护者 `@GuoWQ222` 控制，不授予其他用户写权限。欢迎 [提交 Issue](https://github.com/GuoWQ222/TCOMAS-Boot-Animation-Tool/issues) 或 fork 后提出 PR；PR 通过维护者审核后才会合入。公开仓库允许任何人查看、下载和 fork。

提交问题时请说明型号、REV、操作阶段，以及经过隐去个人路径的日志；不要公开设备备份或自己的视频。参见 [贡献说明](CONTRIBUTING.md)。

## 许可

本项目代码采用 [MIT License](LICENSE)。Python、Pillow、hidapi、Tcl/Tk 和 PyInstaller 保留各自许可，见 [第三方说明](THIRD_PARTY_NOTICES.md)。TCOMAS 商标、原厂软件、固件及动画不属于本项目的 MIT 授权内容。
