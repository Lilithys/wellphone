"""Share the phone lock with router/Web; retain old experiment journals/lock."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path

from router.device import device_lease


def shared_state_root(root):
    root = Path(root).resolve()
    sibling = root.parent / "wellphone-router"
    shared = Path(os.environ.get("WELLPHONE_STATE_ROOT", str(sibling if sibling.is_dir() else root))).expanduser().resolve()
    if not shared.is_dir():
        raise RuntimeError("共享状态目录不存在；不创建新目录绕过已有设备锁。")
    return shared


@contextmanager
def experiment_lease(root, serial):
    """Nonblocking locks, held through audio recovery. No journal migration."""
    local = Path(root).resolve()
    shared = shared_state_root(local)
    with ExitStack() as stack:
        stack.enter_context(device_lease(shared, serial))
        # Keep compatibility with older Douyin/audio processes using local lock.
        if shared != local:
            stack.enter_context(device_lease(local, serial))
        yield shared
