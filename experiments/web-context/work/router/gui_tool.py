"""Reuse the frozen AutoGLM smoke task with stricter primary-IME guards."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from .device import MainGuard, DeviceError
from test_virtual_keyboard import require_primary_keyboard


def execute_about(adb, original_ime, max_steps=8):
    import run_autoglm_focus as driver
    root = Path(__file__).resolve().parents[1]
    project = root.parent
    manifest = json.loads((project / "baseline/source-sha256.json").read_text())
    if any(not (project / name).is_file() or hashlib.sha256((project / name).read_bytes()).hexdigest() != digest
           for name, digest in manifest.items()):
        raise DeviceError("冻结 GUI 源码已变化；先复核基线，不自动继续。")
    server, client = root / "focus_experiment/scrcpy-server-focus", root / "frame_stream/scrcpy-live"
    for path, expected in [(server, driver.SERVER_SHA256), (client, driver.CLIENT_SHA256)]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise DeviceError("GUI 二进制哈希不匹配。")
    guard = MainGuard(adb, original_ime, {"com.android.settings"})
    original_class, sessions = driver.AutoGLMSession, []

    class Session(original_class):
        def __init__(self, args):
            super().__init__(args)
            sessions.append(self)

        def assert_task_scope(self):
            current = super().assert_task_scope()
            guard.require()
            return current

    args = SimpleNamespace(serial=adb.serial, server_path=str(server), client_path=str(client),
                           require_focus_flags=True, trace_focus=True, live_mkv=True, ime_policy="local",
                           no_system_decorations=False, output_prefix="router-gui", window_title="Wellphone-Router-GUI",
                           preflight=False, max_steps=max_steps)
    driver.AutoGLMSession = Session
    try:
        code = driver.execute(args)
    finally:
        driver.AutoGLMSession = original_class
    if not sessions:
        raise DeviceError("GUI 执行器没有建立会话。")
    session = sessions[0]
    evidence = session.report
    effect = evidence.get("task_verified") is True
    errors = []
    if not evidence.get("focus_trace") or evidence.get("trace_incomplete"):
        errors.append("GUI 焦点采样不完整")
    for sample in evidence.get("focus_trace", []):
        try:
            require_primary_keyboard(sample)
        except RuntimeError:
            errors.append("GUI 期间主屏焦点或键盘异常")
            break
    try:
        guard.require()
    except DeviceError as exc:
        errors.append(str(exc))
    isolated = not errors and evidence.get("human_observation") == "正常"
    passed = (code == 0 and effect and isolated and evidence.get("virtual_display_removed") is True
              and not evidence.get("cleanup_error"))
    return {"passed": bool(passed), "effect_verified": effect, "isolation_verified": isolated,
            "errors": errors, "evidence_path": str(session.output / "result.json"),
            "virtual_display_removed": evidence.get("virtual_display_removed", False)}
