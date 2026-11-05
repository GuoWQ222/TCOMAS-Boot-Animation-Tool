"""User messages stay brief while the original diagnostics remain available."""
import unittest

from error_details import concise_error, describe_error


class ConciseErrorTests(unittest.TestCase):
    def test_technical_parameters_are_removed_but_reasons_stay_distinct(self):
        cases = (
            ('命令 0x0D 响应包顺序错误：2/3；应为 0x10D，收到 0x20D', 'USB 响应异常'),
            ('设备拒绝开始写入：请求 selector=1, size=8192；返回 selector=0, size=-1', '设备拒绝开始写入'),
            ('写后读回内容不一致：selector=1，SHA-256 分别为 abc 和 def', '写后读回校验未通过'),
            ('备份文件长度与清单不一致：boot.bin，清单 8192 字节，文件 4096 字节', '备份文件长度与清单不一致'),
            ('恢复官方软件运行失败：NTSTATUS=0xC0000022', '恢复官方软件运行失败'),
            ('视频转换失败：' + 'FFmpeg diagnostic\n' * 100, '视频转换失败，请检查素材是否可正常播放'),
        )
        for detail, expected in cases:
            with self.subTest(detail=detail[:40]):
                error = OSError(detail)
                self.assertEqual(concise_error(error), expected)
                self.assertEqual(describe_error(error), detail.strip())

    def test_useful_system_cause_has_no_path_or_exception_chain(self):
        error = RuntimeError('完整备份当前素材失败')
        error.__cause__ = PermissionError(13, 'Access is denied', 'private/backup/boot.bin')
        self.assertEqual(concise_error(error), '完整备份当前素材失败；访问权限不足')
        self.assertIn('private/backup/boot.bin', describe_error(error))

    def test_existing_actionable_guidance_and_suppression_are_preserved(self):
        message = '此备份中的原始开机动画为空，没有可恢复的动画；请写入本地图片或视频'
        self.assertEqual(concise_error(ValueError(message)), message)
        error = RuntimeError('恢复失败')
        error.__context__ = TimeoutError('command 0x0B at offset 8192 timed out')
        error.__suppress_context__ = True
        self.assertEqual(concise_error(error), '恢复失败')


if __name__ == '__main__':
    unittest.main()
