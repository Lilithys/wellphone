"""Experiment-only ADB error metadata; never persist commands or device output.

Default transport rejection rules are unchanged. Executor-test opt-in may
reobserve a successful AppOps get with stderr once, requiring a clean identical
mode readback. Never retry any write, input, navigation, timeout or other query.
"""
from datetime import datetime
import re
import shlex
import subprocess

from router.device import ADB, DeviceError


ERROR_OUTPUT = re.compile(
    r"(?mi)^\s*(?:\[ERROR\]|Error:|Error while|Exception|SecurityException|Permission Denial|Unknown command:)"
)
PACKAGE = "com.ss.android.ugc.aweme"


def command_category(args):
    """Only fixed labels reach logs; unknown arguments may contain private data."""
    exact = {
        ("am", "get-current-user"): "identity.current_user",
        ("pm", "list", "packages", "-U", PACKAGE): "identity.package_uid",
        ("getprop", "ro.build.fingerprint"): "identity.build",
        ("dumpsys", "activity", "activities"): "state.activities",
        ("dumpsys", "input_method"): "state.ime",
        ("dumpsys", "window"): "state.windows",
        ("dumpsys", "audio"): "audio.players",
        ("settings", "get", "secure", "default_input_method"): "state.default_ime",
    }
    if args in exact:
        return exact[args]
    if len(args) == 5 and args[:4] == ("pm", "list", "packages", "--uid") and args[4].isdigit():
        return "identity.uid_sharing"
    if (len(args) in (7, 8) and args[:2] == ("cmd", "appops")
            and args[3:5] == ("--user", "0") and args[6] in {"PLAY_AUDIO", "TAKE_AUDIO_FOCUS"}):
        if len(args) == 7 and args[2] == "get" and (args[5] == PACKAGE or args[5].isdigit()):
            return "audio.read." + ("package." if args[5] == PACKAGE else "uid.") + args[6]
        if (len(args) == 8 and args[2] == "set" and args[5] == PACKAGE
                and args[7] in {"allow", "ignore", "deny", "default"}):
            return "audio.write.package." + args[6]
    return "unclassified_command"


class DiagnosticADB(ADB):
    def __init__(self, serial=None, *, failures=None, recheck_audio_read=False):
        super().__init__(serial)
        self.failures = [] if failures is None else failures
        self.recheck_audio_read = recheck_audio_read

    def confirm_audio_read(self, args, result, event):
        if (not self.recheck_audio_read or not event["command_category"].startswith("audio.read.")
                or result.returncode or not result.stderr.strip()
                or ERROR_OUTPUT.search(result.stdout) or ERROR_OUTPUT.search(result.stderr)):
            return None
        from douyin_audio import parse_mode
        try:
            original_mode = parse_mode(result.stdout, args[6])
        except RuntimeError:
            return None
        event["read_recheck"] = {"attempts": 1, "status": "STARTED", "writes_retried": False}
        evidence = event["read_recheck"]
        try:
            fresh = subprocess.run(["adb", "-s", self.serial, "shell", shlex.join(args)],
                                   capture_output=True, text=True, timeout=15)
            evidence.update(returncode=fresh.returncode, stdout_chars=len(fresh.stdout), stderr_chars=len(fresh.stderr))
            if fresh.returncode or fresh.stderr.strip() or ERROR_OUTPUT.search(fresh.stdout):
                evidence["status"] = "REJECTED_SECOND_READ"
                return None
            if parse_mode(fresh.stdout, args[6]) != original_mode:
                evidence["status"] = "REJECTED_MODE_CHANGED"
                return None
            evidence["status"] = "CLEAN_IDENTICAL_MODE_CONFIRMED"
            print("音频只读查询已用第二次无错误、相同模式的回读确认；没有重放任何写入。", flush=True)
            return fresh.stdout
        except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
            evidence.update(status="RECHECK_FAILED", error_type=type(exc).__name__)
            return None

    def shell(self, *args):
        if not self.serial:
            raise DeviceError("没有固定的设备身份。")
        args = tuple(map(str, args))
        category = command_category(args)
        event = {"time": datetime.now().isoformat(), "command_category": category}
        try:
            result = subprocess.run(
                ["adb", "-s", self.serial, "shell", shlex.join(args)],
                capture_output=True, text=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            event["failure_kind"] = "TIMEOUT"
            self.failures.append(event)
            raise DeviceError(f"ADB 超时；写入结果可能不确定，不自动重试。 [{category}: TIMEOUT]") from None
        except OSError as exc:
            event.update(failure_kind="PROCESS_START_FAILED", errno=exc.errno)
            self.failures.append(event)
            raise DeviceError(f"ADB 进程无法启动；未重试。 [{category}: PROCESS_START_FAILED]") from None
        signals = []
        if result.returncode:
            signals.append("NONZERO_EXIT")
        if result.stderr.strip():
            signals.append("STDERR_PRESENT")
        if ERROR_OUTPUT.search(result.stdout):
            signals.append("ERROR_OUTPUT")
        if signals:
            # No raw output, command arguments, serial, exception messages, or hashes.
            event.update(failure_kind="COMMAND_REJECTED", failure_signals=signals,
                         returncode=result.returncode, stdout_chars=len(result.stdout),
                         stderr_chars=len(result.stderr))
            self.failures.append(event)
            confirmed = self.confirm_audio_read(args, result, event)
            if confirmed is not None:
                return confirmed
            raise DeviceError("ADB/Provider 命令报错；不自动提升权限或切换执行方式。 "
                              f"[{category}: {','.join(signals)}]")
        return result.stdout
