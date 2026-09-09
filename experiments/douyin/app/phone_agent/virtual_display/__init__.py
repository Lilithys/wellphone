"""Android virtual-display backend for running the agent off the main screen."""

from phone_agent.adb.connection import (
    ADBConnection,
    ConnectionType,
    DeviceInfo,
    list_devices,
    quick_connect,
)
from phone_agent.virtual_display.device import (
    back,
    clear_text,
    configure,
    detect_and_set_adb_keyboard,
    double_tap,
    get_current_app,
    get_screenshot,
    home,
    launch_app,
    long_press,
    restore_keyboard,
    swipe,
    tap,
    type_text,
)

__all__ = [
    "configure",
    "get_screenshot",
    "get_current_app",
    "tap",
    "double_tap",
    "long_press",
    "swipe",
    "back",
    "home",
    "launch_app",
    "type_text",
    "clear_text",
    "detect_and_set_adb_keyboard",
    "restore_keyboard",
    "ADBConnection",
    "DeviceInfo",
    "ConnectionType",
    "quick_connect",
    "list_devices",
]
