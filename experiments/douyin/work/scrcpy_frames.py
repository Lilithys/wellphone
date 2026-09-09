"""Decode this session's scrcpy video to a native-resolution, atomic PNG."""
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path


class FrameStream:
    def __init__(self, output):
        self.output = Path(output)
        self.pipe_path = self.output / "virtual-video.pipe"
        self.frame_path = self.output / "latest_frame.png"
        self.log_path = self.output / "decoder.log"
        self.process = None
        self.log = None

    def start(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("需要本地 ffmpeg 解码副屏视频；未创建虚拟显示。")
        os.mkfifo(self.pipe_path, 0o600)
        self.log = self.log_path.open("xb")
        environment = os.environ.copy()
        environment.pop("PHONE_AGENT_API_KEY", None)
        self.process = subprocess.Popen(
            [ffmpeg, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
             "-analyzeduration", "0", "-probesize", "32", "-threads", "1",
             "-flags", "low_delay", "-f", "matroska", "-i", str(self.pipe_path),
             "-map", "0:v:0", "-an", "-fps_mode", "passthrough",
             # HONOR advertises YCgCo/log316, unsupported by this swscale build.
             # Explicit SDR metadata override for this device-specific UI prototype.
             "-vf", "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=limited,format=rgb24",
             "-c:v", "png",
             "-threads", "1", "-compression_level", "1", "-f", "image2", "-update", "1",
             "-atomic_writing", "1", str(self.frame_path)],
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self.log,
        )

    def assert_live(self):
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError(f"副屏解码进程已退出，禁止使用旧帧。日志：{self.log_path}")

    def wait_for_frame(self, timeout=15, not_before_ns=0, settle_seconds=0.5):
        """Wait for a new write, then a quiet interval (not a universal UI-idle proof)."""
        deadline = time.monotonic() + timeout
        last_version, stable_since = None, time.monotonic()
        while time.monotonic() < deadline:
            self.assert_live()
            try:
                info = self.frame_path.stat()
                if info.st_size > 0 and info.st_mtime_ns >= not_before_ns:
                    version = (info.st_mtime_ns, info.st_size)
                    if version != last_version:
                        last_version, stable_since = version, time.monotonic()
                    elif time.monotonic() - stable_since >= settle_seconds:
                        return
            except FileNotFoundError:
                pass
            time.sleep(0.1)
        raise RuntimeError(f"没有收到更新后稳定的副屏原生帧，停止使用旧截图。日志：{self.log_path}")

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.log is not None:
            self.log.close()
        if self.pipe_path.exists() and stat.S_ISFIFO(self.pipe_path.lstat().st_mode):
            self.pipe_path.unlink()
