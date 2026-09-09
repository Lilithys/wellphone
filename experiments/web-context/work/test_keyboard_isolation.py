#!/usr/bin/env python3
"""Interactive, local-only keyboard isolation test. Python 3.9+, no packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path


def activity_on_display(dump: str, display_id: int) -> str:
    sections = re.split(r"(?m)^Display #(\d+) \(activities from top to bottom\):", dump)
    for index in range(1, len(sections), 2):
        if int(sections[index]) == display_id:
            match = re.search(r"topResumedActivity=.*?\s([\w.]+/[\w.$]+)", sections[index + 1])
            return match.group(1) if match else "unknown"
    return "unknown"


def focus_state(ime: str, window: str) -> dict:
    """Whitelist diagnostics; never persist editor text, surrounding text or window titles."""
    match = re.search(r"(?m)^\s*mTopFocusedDisplayId=(-?\d+)\s*$", window)
    return {
        "top_focused_display_id": int(match.group(1)) if match else None,
        "ime_state": [line.strip() for line in ime.splitlines()
                      if re.match(r"\s*(mCurFocusedWindow=|mCurTokenDisplayId=|"
                                  r"mImeWindowVis=|mInputShown=|mShowRequested=)", line)],
    }


class KeyboardTest:
    def __init__(self, args):
        self.args = args
        self.serial = args.serial
        self.process = None
        self.reader = None
        self.display_id = None
        self.display_ready = threading.Event()
        self.video_ready = threading.Event()
        self.focus_flags_verified = threading.Event()
        self.scrcpy_log = []
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_root = Path(__file__).resolve().parent.parent / "outputs"
        output_root.mkdir(exist_ok=True)
        prefix = getattr(args, "output_prefix", "keyboard")
        self.output = Path(tempfile.mkdtemp(prefix=f"{prefix}-{stamp}-", dir=output_root))
        self.report = {"started_at": datetime.now().isoformat(), "ime_policy": args.ime_policy,
                       "system_decorations": not args.no_system_decorations,
                       "require_focus_flags": args.require_focus_flags,
                       "trace_focus": args.trace_focus, "stages": []}

    def save(self):
        (self.output / "result.json").write_text(
            json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    def shell(self, *args):
        result = subprocess.run(
            ["adb", "-s", self.serial, "shell", shlex.join(map(str, args))],
            capture_output=True, text=True, check=True, timeout=15)
        return result.stdout

    def guard(self):
        if self.display_id is None or self.display_id <= 0:
            raise RuntimeError("没有可操作的非主屏显示，已停止。")
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("本次 scrcpy 已退出，已停止动作。")
        if self.args.require_focus_flags and not self.focus_flags_verified.is_set():
            raise RuntimeError("实验版尚未确认焦点标记生效，停止发送动作。")
        dump = self.shell("dumpsys", "display")
        if not any(f'displayId {self.display_id},' in line and '"scrcpy"' in line
                   and 'type VIRTUAL' in line for line in dump.splitlines()):
            raise RuntimeError("本次 scrcpy 虚拟显示不存在，已停止动作。")

    def read_focus(self):
        ime = self.shell("dumpsys", "input_method")
        window = self.shell("sh", "-c",
                            "dumpsys window | grep -E '^[[:space:]]*mTopFocusedDisplayId='")
        return {"time": datetime.now().isoformat(), **focus_state(ime, window)}

    def snapshot(self):
        """Only record app component and IME state, never messages or images."""
        try:
            dump = self.shell("dumpsys", "activity", "activities")
            return {
                **self.read_focus(),
                "main_activity": activity_on_display(dump, 0),
                "virtual_activity": activity_on_display(dump, self.display_id)
                if self.display_id else "not_created",
            }
        except Exception as exc:
            return {"snapshot_error": str(exc)}

    def prepare(self):
        for tool in ("adb", "scrcpy"):
            if shutil.which(tool) is None:
                raise RuntimeError(f"没有找到 {tool}，请使用之前能运行测试的 Terminal。")
        if self.args.server_path:
            self.args.server_path = str(Path(self.args.server_path).expanduser().resolve(strict=True))
            self.report["server_path"] = self.args.server_path
            self.report["server_sha256"] = hashlib.sha256(Path(self.args.server_path).read_bytes()).hexdigest()
        if self.args.require_focus_flags:
            if not self.args.server_path:
                raise RuntimeError("实验测试必须显式指定 --server-path。")
            version = subprocess.run([getattr(self.args, "client_path", "scrcpy"), "--version"], capture_output=True,
                                     text=True, check=True, timeout=10).stdout
            if not re.search(r"(?m)^scrcpy 4\.1(?:\s|$)", version):
                raise RuntimeError("实验服务端仅匹配 scrcpy 4.1，请勿混用客户端版本。")
        devices = subprocess.run(["adb", "devices"], capture_output=True,
                                 text=True, check=True, timeout=15).stdout
        serials = re.findall(r"(?m)^(\S+)\s+device\s*$", devices)
        if not self.serial:
            if len(serials) != 1:
                raise RuntimeError("请连接一台已授权的手机；多设备时传 --serial 指定序列号。")
            self.serial = serials[0]
        if self.serial not in serials:
            raise RuntimeError("指定手机没有连接或尚未允许 USB 调试。")
        self.report["device"] = self.serial
        dump = self.shell("dumpsys", "display")
        if any('"scrcpy"' in line and 'type VIRTUAL' in line for line in dump.splitlines()):
            raise RuntimeError("检测到已有 scrcpy 虚拟显示。请先关闭旧的测试窗口再运行。")
        resolved = self.shell("cmd", "package", "resolve-activity", "--brief",
                              "-a", "android.intent.action.MAIN", "-c",
                              "android.intent.category.LAUNCHER", "com.android.settings")
        candidates = re.findall(r"(?m)^com\.android\.settings/[\w.$]+$", resolved)
        if len(candidates) != 1:
            raise RuntimeError("无法确定系统设置的启动页面，未启动任何应用。")
        self.settings_component = candidates[0]

    def start_display(self):
        command = [getattr(self.args, "client_path", "scrcpy"), "--serial", self.serial, "--new-display=1080x2400/420",
                   f"--display-ime-policy={self.args.ime_policy}", "--no-audio",
                   "--no-clipboard-autosync",
                   "--window-title=" + getattr(self.args, "window_title", "Wellphone-Keyboard-Test")]
        if self.args.no_system_decorations:
            command.append("--no-vd-system-decorations")
        if getattr(self.args, "record_path", None):
            command.extend(["--record=" + str(self.args.record_path), "--record-format=mkv"])
        if getattr(self.args, "probe_only", False):
            # Keep the video producer alive without a Mac window or a saved recording.
            # Only this empty new display is encoded; its stream is discarded in /dev/null.
            command.extend(["--no-window", "--no-control", "--time-limit=10",
                            "--record=/dev/null", "--record-format=mkv"])
        environment = os.environ.copy()
        environment.pop("PHONE_AGENT_API_KEY", None)  # scrcpy does not need model credentials
        if getattr(self.args, "live_mkv", False):
            environment["WELLPHONE_LIVE_MKV"] = "1"
        if self.args.server_path:
            environment["SCRCPY_SERVER_PATH"] = self.args.server_path
        self.process = subprocess.Popen(command, env=environment, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True)

        def read_log():
            for line in self.process.stdout:
                self.scrcpy_log.append(line.rstrip())
                if re.search(r"Wellphone focus flags VERIFIED: display=\d+ actual=0x[0-9a-f]+", line):
                    self.focus_flags_verified.set()
                    self.report["focus_flags_verified_log"] = line.strip()
                match = re.search(r"New display:.*\(id=(\d+)\)", line)
                if match:
                    self.display_id = int(match.group(1))
                    self.display_ready.set()
                if "Texture:" in line:
                    self.video_ready.set()

        self.reader = threading.Thread(target=read_log, daemon=True)
        self.reader.start()
        deadline = time.monotonic() + 20
        while not self.display_ready.wait(0.1):
            if self.process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("虚拟显示启动失败：\n" + "\n".join(self.scrcpy_log[-12:]))
        self.guard()
        # With decorations disabled an empty display may produce no video yet.
        if not self.args.no_system_decorations:
            self.video_ready.wait(3)
        self.report["display_id"] = self.display_id
        print(f"虚拟显示已创建：display {self.display_id}", flush=True)
        if self.args.require_focus_flags:
            print("焦点标记已被系统接受；这不等于键盘隔离已经通过，仍需观察。", flush=True)

    def launch_settings(self):
        self.guard()
        main = activity_on_display(self.shell("dumpsys", "activity", "activities"), 0)
        if main.startswith("com.android.settings/"):
            raise RuntimeError("主屏正在使用设置。请把主屏换成备忘录后重新测试。")
        result = self.shell("am", "start", "--display", self.display_id,
                            "-n", self.settings_component,
                            "-a", "android.intent.action.MAIN",
                            "-c", "android.intent.category.LAUNCHER")
        if "Error:" in result or "Exception" in result:
            raise RuntimeError("在虚拟显示启动设置失败：" + result)
        time.sleep(1)
        self.require_settings()

    def require_settings(self):
        self.guard()
        activity = activity_on_display(self.shell("dumpsys", "activity", "activities"),
                                       self.display_id)
        if not activity.startswith("com.android.settings/"):
            raise RuntimeError("虚拟显示没有处于设置页面，停止发送动作。")

    def scroll(self):
        for index in range(10):
            self.require_settings()
            start_y, end_y = (1800, 650) if index % 2 == 0 else (650, 1800)
            self.shell("input", "-d", self.display_id, "swipe", 540,
                       start_y, 540, end_y, 400)
            print(f"滑动 {index + 1}/10；请继续在手机上输入。", flush=True)
            time.sleep(2)

    def open_info(self):
        self.require_settings()
        result = self.shell("am", "start", "--display", self.display_id,
                            "-a", "android.settings.DEVICE_INFO_SETTINGS",
                            "-p", "com.android.settings")
        if "Error:" in result or "Exception" in result:
            raise RuntimeError("关于手机页面启动失败：" + result)

    def press_back(self):
        self.require_settings()
        self.shell("input", "-d", self.display_id, "keyevent", "KEYCODE_BACK")

    def stop_display(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            dump = self.shell("dumpsys", "display")
            if not re.search(rf"\bmDisplayId={self.display_id}\b", dump):
                self.report["virtual_display_removed"] = True
                return
            time.sleep(0.3)
        self.report["virtual_display_removed"] = False
        raise RuntimeError("scrcpy 已停止，但显示销毁尚未确认，请检查手机连接。")

    def stage(self, name, action):
        print(f"\n{name}", flush=True)
        print("先让手机键盘恢复正常。电脑按回车后有 5 秒准备时间，再持续在手机上打字。")
        input("按回车准备本阶段；Ctrl+C 结束测试：")
        entry = {"stage": name, "before": self.snapshot()}
        self.report["stages"].append(entry)
        self.save()
        for remaining in range(5, 0, -1):
            print(f"{remaining}…", flush=True)
            time.sleep(1)
        print("现在执行本阶段，请观察手机键盘。", flush=True)
        trace_stop = threading.Event()
        trace_thread = None
        if self.args.trace_focus:
            entry["focus_trace"] = []

            def record_focus():
                while not trace_stop.is_set():
                    try:
                        entry["focus_trace"].append(self.read_focus())
                    except Exception as exc:
                        entry["focus_trace"].append({"error": str(exc)})
                    trace_stop.wait(0.4)

            trace_thread = threading.Thread(target=record_focus, daemon=True)
            trace_thread.start()
        try:
            action()
            time.sleep(3)
        finally:
            trace_stop.set()
            if trace_thread is not None:
                trace_thread.join(timeout=3)
                if trace_thread.is_alive():
                    entry["focus_trace_incomplete"] = True
        entry["after"] = self.snapshot()
        print("观察结果：0=正常，1=键盘区域白屏，2=键盘收起，3=其他输入异常，4=没看清")
        while True:
            answer = input("输入编号并回车：").strip()
            if answer in {"0", "1", "2", "3", "4"}:
                break
        entry["observation"] = {"0": "正常", "1": "白屏", "2": "键盘收起",
                                "3": "其他输入异常", "4": "未观察清楚"}[answer]
        if answer in {"1", "2", "3"}:
            entry["recovery"] = input("如何恢复？输入 无需操作 / 点输入框 / 退出再进入 / 无法恢复 / 其他：").strip()
        self.save()

    def run(self):
        try:
            self.prepare()
            print("本次不使用 API key、不调用模型、不截取或上传画面。")
            if self.args.require_focus_flags:
                print("本次使用独立的禁止抢顶层焦点实验版，不替换原有 scrcpy。")
            if getattr(self.args, "probe_only", False):
                print("仅检查空显示的焦点标记；不启动桌面或设置，不注入任何输入。")
                self.report["probe_only"] = True
                self.report["probe_before"] = self.snapshot()
                self.start_display()
                self.report["probe_active"] = self.snapshot()
                self.stop_display()
                self.report["probe_after"] = self.snapshot()
            else:
                print("请在手机主屏打开备忘录并调出键盘；不要在 scrcpy 窗口操作。")
                print("A 阶段创建显示时，系统可能自动启动副屏桌面，尚不能把两者完全分开。")
                self.stage("A 创建虚拟显示（不主动启动设置）", self.start_display)
                self.stage("B 第一次在虚拟显示打开设置", self.launch_settings)
                self.stage("C 设置页面连续上下滑动约 30 秒", self.scroll)
                self.stage("D 在同一显示打开关于手机页面", self.open_info)
                self.stage("E 返回设置页面", self.press_back)
                self.stage("F 关闭本次虚拟显示", self.stop_display)
            self.report["completed"] = True
        except (EOFError, KeyboardInterrupt):
            print("\n测试已中断，正在清理本次虚拟显示。")
            self.report["interrupted"] = True
        except Exception as exc:
            self.report["error"] = str(exc)
            print(f"\n测试停止：{exc}")
        finally:
            try:
                self.stop_display()
            except Exception as exc:
                self.report["cleanup_error"] = str(exc)
                print(f"清理提示：{exc}")
            self.report["finished_at"] = datetime.now().isoformat()
            self.save()
            (self.output / "scrcpy.log").write_text("\n".join(self.scrcpy_log), encoding="utf-8")
            print("\n结果：")
            for entry in self.report["stages"]:
                print(f"  {entry['stage']}：{entry.get('observation', '未完成')}")
            print(f"记录已保存：{self.output / 'result.json'}")
        return 0 if self.report.get("completed") and not self.report.get("cleanup_error") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="分阶段测试 scrcpy 是否打断主屏键盘，无需 API key。")
    parser.add_argument("--serial", help="多设备连接时指定手机序列号")
    parser.add_argument("--ime-policy", choices=("local", "hide"), default="local")
    parser.add_argument("--no-system-decorations", action="store_true",
                        help="仅供后续对照测试：禁用副屏系统装饰")
    parser.add_argument("--server-path", help="仅为本次进程指定 scrcpy 服务端，不替换系统安装")
    parser.add_argument("--require-focus-flags", action="store_true",
                        help="实验版必须确认独立焦点及禁止抢焦点标记，否则停止")
    parser.add_argument("--trace-focus", action="store_true",
                        help="动作期间采样焦点和输入法状态；不能保证捕获所有瞬时变化")
    raise SystemExit(KeyboardTest(parser.parse_args()).run())
