"""Offline parsing only; no credentials, phone calls or automatic sending."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("delivery_task", ROOT / "delivery_task.py")
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


class NaturalTaskTests(unittest.TestCase):
    def test_quoted_name_and_message(self):
        task = module.parse_task('在抖音给“示例联系人”发送“hello 1”')
        self.assertEqual((task.recipient, task.message), ("示例联系人", "hello 1"))

    def test_plain_single_instruction(self):
        task = module.parse_task("打开抖音给示例联系人发1")
        self.assertEqual((task.recipient, task.message), ("示例联系人", "1"))

    def test_name_containing_action_word_can_be_quoted(self):
        task = module.parse_task('抖音给“阿发”发送“OK”')
        self.assertEqual(task.recipient, "阿发")

    def test_unsupported_or_ambiguous_input_rejected(self):
        for text in ('', '给他发一下', '微信给示例联系人发1',
                     '抖音给“示例联系人”发送“你好”', '抖音给“示例联系人”发送“%s”',
                     '抖音给“示例联系人”发送“1”; shell',
                     '抖音给“示例联系人”发送“1”然后删除聊天',
                     '抖音给“示例联系人”发送“1\n2”'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                module.parse_task(text)


if __name__ == "__main__":
    unittest.main()
