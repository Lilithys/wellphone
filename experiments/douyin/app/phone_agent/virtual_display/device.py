"""Device operations scoped to a scrcpy-created Android virtual display.

Input is sent with ``input -d DISPLAY_ID``. The focus-test runner reads native
PNG frames decoded from its scrcpy video stream; legacy callers may still use
macOS window capture. Neither path falls back to the phone's primary display.
This backend never changes the system IME.
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import tempfile
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from phone_agent.adb.screenshot import Screenshot
from phone_agent.config.apps import APP_PACKAGES, get_app_name
from phone_agent.config.timing import TIMING_CONFIG


_display_id: int | None = None
_window_title = "Wellphone-Agent"
_window_pid: int | None = None
_screen_size = (1080, 2400)
_window_id: int | None = None
_device_id: str | None = None
_frame_path: Path | None = None
_frame_producer_pid: int | None = None


def configure(
    display_id: int,
    window_title: str = "Wellphone-Agent",
    screen_size: tuple[int, int] = (1080, 2400),
    window_pid: int | None = None,
    device_id: str | None = None,
    frame_path: str | None = None,
    frame_producer_pid: int | None = None,
) -> None:
    """Configure the display and matching scrcpy window used by this backend."""
    global _display_id, _window_title, _window_pid, _screen_size, _window_id, _device_id
    global _frame_path, _frame_producer_pid
    if display_id <= 0:
        raise ValueError("Virtual display id must be greater than 0")
    if screen_size[0] <= 0 or screen_size[1] <= 0:
        raise ValueError("Virtual display size must be positive")
    if window_pid is not None and window_pid <= 0:
        raise ValueError("scrcpy window pid must be positive")
    if bool(frame_path) != (frame_producer_pid is not None):
        raise ValueError("Native frame path and producer pid must be provided together")
    if frame_producer_pid is not None and frame_producer_pid <= 0:
        raise ValueError("Frame producer pid must be positive")
    _display_id = display_id
    _window_title = window_title
    _window_pid = window_pid
    _screen_size = screen_size
    _window_id = None
    _device_id = device_id
    _frame_path = Path(frame_path).resolve() if frame_path else None
    _frame_producer_pid = frame_producer_pid


def _require_display_id() -> int:
    if _display_id is None:
        raise RuntimeError("Virtual display backend has not been configured")
    if _display_id <= 0:
        raise RuntimeError("Refusing to control the user's main display")
    return _display_id


def _adb_prefix(device_id: str | None) -> list[str]:
    if _device_id is not None:
        if device_id is not None and device_id != _device_id:
            raise RuntimeError("Device does not match this virtual-display session")
        device_id = _device_id
    return ["adb", "-s", device_id] if device_id else ["adb"]


def _adb_shell(device_id: str | None, *args: object) -> subprocess.CompletedProcess:
    return subprocess.run(
        _adb_prefix(device_id) + ["shell", shlex.join(map(str, args))],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=15,
    )


def _assert_display_exists(device_id: str | None) -> None:
    display_id = _require_display_id()
    if _window_pid is not None:
        try:
            os.kill(_window_pid, 0)
        except OSError as exc:
            raise RuntimeError("This scrcpy process is no longer available") from exc
    result = _adb_shell(device_id, "dumpsys", "display")
    if not any(f'displayId {display_id},' in line and '"scrcpy"' in line
               and 'type VIRTUAL' in line for line in result.stdout.splitlines()):
        raise RuntimeError(f"scrcpy virtual display {display_id} no longer exists")


def _find_window_id() -> int:
    global _window_id
    if _window_id is not None:
        return _window_id

    source = Path(__file__).with_name("window_id.c")
    helper = Path(tempfile.gettempdir()) / "wellphone-window-id"
    if not helper.exists() or helper.stat().st_mtime < source.stat().st_mtime:
        subprocess.run(
            [
                "clang",
                str(source),
                "-framework",
                "ApplicationServices",
                "-framework",
                "CoreFoundation",
                "-o",
                str(helper),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    result = subprocess.run(
        [str(helper), _window_title, str(_window_pid or 0)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        selector = (
            f"scrcpy process {_window_pid}"
            if _window_pid is not None
            else f'visible scrcpy window titled "{_window_title}"'
        )
        raise RuntimeError(
            f"Cannot find {selector}. Keep the window open and grant Terminal "
            "Screen & System Audio Recording permission in macOS settings."
        )
    _window_id = int(result.stdout.strip())
    return _window_id


def get_screenshot(device_id: str | None = None, timeout: int = 10) -> Screenshot:
    """Read native video PNG, or legacy window capture; never capture display 0."""
    _assert_display_exists(device_id)
    if _frame_path is not None:
        if _frame_producer_pid is None:
            raise RuntimeError("Native frame source requires a live producer")
        try:
            os.kill(_frame_producer_pid, 0)
            data = _frame_path.read_bytes()
        except OSError as exc:
            raise RuntimeError("Native virtual-display frame is unavailable; no screenshot fallback") from exc
        with Image.open(BytesIO(data)) as native:
            if native.size != _screen_size or native.format != "PNG":
                raise RuntimeError(f"Native frame has unexpected format or size: {native.format} {native.size}")
            native.verify()
        return Screenshot(base64_data=base64.b64encode(data).decode("utf-8"),
                          width=_screen_size[0], height=_screen_size[1], is_sensitive=False)
    window_id = _find_window_id()
    descriptor, filename = tempfile.mkstemp(prefix="wellphone-window-", suffix=".png")
    os.close(descriptor)
    temp_path = Path(filename)

    try:
        result = subprocess.run(
            ["screencapture", "-x", "-o", "-l", str(window_id), str(temp_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0 or not temp_path.exists() or temp_path.stat().st_size == 0:
            raise RuntimeError(
                "macOS could not capture the scrcpy window. Grant Terminal "
                "Screen & System Audio Recording permission, then restart Terminal."
            )
        image = Image.open(temp_path).convert("RGB")
        target_width, target_height = _screen_size
        if image.width < target_width // 2:
            raise RuntimeError(
                f"Window capture is only {image.width}x{image.height}; refusing to upscale a thumbnail. "
                "Use the native scrcpy video-frame runner instead."
            )

        # A decorated macOS window adds only a title bar once its shadow is
        # disabled. Infer the video height from the fixed Android aspect ratio.
        video_height = round(image.width * target_height / target_width)
        if video_height > image.height:
            raise RuntimeError(
                f"Window image {image.size} is smaller than expected video area"
            )
        top = image.height - video_height
        image = image.crop((0, top, image.width, image.height))
        image = image.resize(_screen_size, Image.Resampling.LANCZOS)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return Screenshot(
            base64_data=base64.b64encode(buffer.getvalue()).decode("utf-8"),
            width=target_width,
            height=target_height,
            is_sensitive=False,
        )
    finally:
        temp_path.unlink(missing_ok=True)


def get_current_app(device_id: str | None = None) -> str:
    """Return the resumed package in this display's activity section."""
    display_id = _require_display_id()
    _assert_display_exists(device_id)
    result = _adb_shell(device_id, "dumpsys", "activity", "activities")
    marker = f"Display #{display_id} (activities from top to bottom):"
    if marker not in result.stdout:
        return "System Home"
    section = result.stdout.split(marker, 1)[1]
    section = re.split(r"\nDisplay #\d+ \(activities from top to bottom\):", section, 1)[0]
    match = re.search(r"topResumedActivity=.*?\s([\w.]+)/[\w.$]+", section)
    if not match:
        return "System Home"
    package = match.group(1)
    return get_app_name(package) or package


def tap(x: int, y: int, device_id: str | None = None, delay: float | None = None) -> None:
    _assert_display_exists(device_id)
    _adb_shell(device_id, "input", "-d", _require_display_id(), "tap", x, y)
    time.sleep(TIMING_CONFIG.device.default_tap_delay if delay is None else delay)


def double_tap(x: int, y: int, device_id: str | None = None, delay: float | None = None) -> None:
    tap(x, y, device_id, 0.1)
    tap(x, y, device_id, delay)


def long_press(
    x: int,
    y: int,
    duration_ms: int = 3000,
    device_id: str | None = None,
    delay: float | None = None,
) -> None:
    swipe(x, y, x, y, duration_ms, device_id, delay)


def swipe(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration_ms: int | None = None,
    device_id: str | None = None,
    delay: float | None = None,
) -> None:
    _assert_display_exists(device_id)
    duration_ms = 500 if duration_ms is None else duration_ms
    _adb_shell(
        device_id,
        "input",
        "-d",
        _require_display_id(),
        "swipe",
        start_x,
        start_y,
        end_x,
        end_y,
        duration_ms,
    )
    time.sleep(TIMING_CONFIG.device.default_swipe_delay if delay is None else delay)


def _keyevent(key: str, device_id: str | None, delay: float | None) -> None:
    _assert_display_exists(device_id)
    _adb_shell(device_id, "input", "-d", _require_display_id(), "keyevent", key)
    time.sleep(0 if delay is None else delay)


def back(device_id: str | None = None, delay: float | None = None) -> None:
    _keyevent(
        "KEYCODE_BACK",
        device_id,
        TIMING_CONFIG.device.default_back_delay if delay is None else delay,
    )


def home(device_id: str | None = None, delay: float | None = None) -> None:
    _keyevent(
        "KEYCODE_HOME",
        device_id,
        TIMING_CONFIG.device.default_home_delay if delay is None else delay,
    )


def _resolve_launcher_activity(package: str, device_id: str | None) -> str | None:
    result = _adb_shell(
        device_id,
        "cmd",
        "package",
        "resolve-activity",
        "--brief",
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
        package,
    )
    candidates = [line.strip() for line in result.stdout.splitlines() if "/" in line]
    return candidates[-1] if candidates else None


def launch_app(
    app_name: str,
    device_id: str | None = None,
    delay: float | None = None,
) -> bool:
    package = APP_PACKAGES.get(app_name)
    if package is None:
        return False
    _assert_display_exists(device_id)
    component = _resolve_launcher_activity(package, device_id)
    if component is None:
        return False
    result = _adb_shell(
        device_id,
        "am",
        "start",
        "--display",
        _require_display_id(),
        "-n",
        component,
    )
    if "Error:" in result.stdout or "Exception" in result.stdout:
        return False
    time.sleep(TIMING_CONFIG.device.default_launch_delay if delay is None else delay)
    return True


def type_text(text: str, device_id: str | None = None) -> None:
    """Fail closed until concurrent text input has its own acceptance test."""
    raise RuntimeError("Virtual-display text input is not yet validated and is disabled")


def clear_text(device_id: str | None = None) -> None:
    raise RuntimeError("Virtual-display text editing is not yet validated and is disabled")


def detect_and_set_adb_keyboard(device_id: str | None = None) -> str:
    """Do not touch the global IME; retained for ActionHandler compatibility."""
    return ""


def restore_keyboard(ime: str, device_id: str | None = None) -> None:
    """No-op because this backend never changes the global IME."""
    return None
