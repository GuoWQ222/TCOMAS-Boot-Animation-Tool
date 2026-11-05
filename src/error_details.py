"""Display an operation failure and its underlying causes."""
import re


def describe_error(error):
    messages, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        message = str(error).strip() or type(error).__name__
        if not any(message in detail for detail in messages):
            messages.append(message)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return '\n底层原因：'.join(messages)


def _brief_reason(error):
    text = str(error).strip()
    if isinstance(error, PermissionError) or re.search(r'permission denied|access is denied', text, re.I):
        return '访问权限不足'
    if getattr(error, 'errno', None) == 28 or re.search(r'disk full|no space left', text, re.I):
        return '磁盘空间不足'
    if '尚未重新连接' in text:
        return '屏幕尚未重新连接'
    if getattr(error, 'winerror', None) == 1167 or '设备没有连接' in text:
        return '设备连接已断开'
    if isinstance(error, TimeoutError) or re.search(r'超时|timed out', text, re.I):
        return '等待设备响应超时'
    if isinstance(error, FileNotFoundError) and (text.startswith('[') or not re.search(r'[\u4e00-\u9fff]', text)):
        return '找不到所需文件'
    if text.lower() == 'read error':
        return 'USB 读取失败'
    if 'HID' in text and '发送长度' in text:
        return 'USB 数据发送不完整'
    if re.search(r'响应.*(长度|顺序|包数|总等待)|其他命令响应|旧包', text):
        return 'USB 响应异常'
    if '素材元数据异常' in text or '存储布局不符' in text:
        return '屏幕中的素材信息异常'
    if 'SHA-256 校验失败' in text:
        return '备份文件校验失败'
    if re.search(r'读回.*(不一致|回退)', text):
        return '写后读回校验未通过'
    if '无法查询原厂软件进程' in text:
        return '无法查询官方软件进程'
    if re.search(r'JPEG.*(解码|decod)|broken data stream', text, re.I):
        return 'JPEG 图像解码失败'
    if 'invalid preview container' in text:
        return '预览素材格式无效'
    if text.startswith('视频转换失败'):
        return '视频转换失败，请检查素材是否可正常播放'
    if not re.search(r'[\u4e00-\u9fff]', text):
        return '操作失败，请查看日志'
    # Keep the readable reason; byte counts, offsets, hashes and system output
    # follow the first colon in the detailed exceptions retained in the log.
    text = re.split(r'[\n：:]|[；;](?=.*(?:0x|offset|selector|NTSTATUS))', text, maxsplit=1)[0]
    return text if len(text) <= 90 else text[:89] + '…'


def concise_error(error):
    """Show a short reason, adding at most one useful underlying cause."""
    messages, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        reason = _brief_reason(error)
        if reason != '操作失败，请查看日志' and not any(reason in item for item in messages):
            messages.append(reason)
        if len(messages) == 2:
            break
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return '；'.join(messages) if messages else '操作失败，请查看日志'
