# 原生副屏取帧（macOS 本机原型）

问题：本机 Mac 窗口截图仅 92×300，放大后文字不可读；原版 scrcpy 的 MKV 录制 + 默认解码缓冲会留下过渡动画帧。现在从**同一手机副屏**取原生 1080×2400 视频，不截主屏、不再依赖窗口像素。

`scrcpy-live + 原有焦点隔离 server → 私有 FIFO (MKV) → ffmpeg → 原子更新 latest_frame.png`

客户端修改仅限 `app/src/recorder.[ch]`：环境变量 `WELLPHONE_LIVE_MKV=1` 且 MKV、只有视频时，不等待下一包来计算当前包时长；使用 `av_write_frame` 并逐包刷新 muxer 和 AVIO。普通录制路径保持原逻辑。解码端单线程、低延迟；未混用 FFmpeg 的两种写包 API。依据：[FFmpeg 写包 API](https://ffmpeg.org/doxygen/trunk/group__lavf__encoding.html)、[Matroska 选项](https://ffmpeg.org/ffmpeg-formats.html#matroska)。没有替换系统 Homebrew 的 scrcpy 或服务端。

## 复现

使用 [scrcpy v4.1 官方源码](https://codeload.github.com/Genymobile/scrcpy/tar.gz/refs/tags/v4.1)，压缩包 SHA256 `537b2ade623cb94b6edddfa5c61bf0b0af21484aa8365ea2531b686ea573249a`。在解压后的源码中应用本目录 `live-mkv.patch`。客户端不需要重新构建 Java 服务端。

```sh
# 在 work/frame_stream 下；源码及输出路径替换为你的新构建位置
patch -p1 -d /path/to/scrcpy-4.1 -i "$PWD/live-mkv.patch"
python3 build_client.py /path/to/scrcpy-4.1 /path/to/new-scrcpy-live
```

本机依赖：Apple clang、Homebrew `/opt/homebrew` 下的 SDL 3.4.16、FFmpeg libavformat/libavcodec 63.1.101、libavutil 61.1.101；USB/OTG、V4L2 未编入。`config.h` 为本机显式构建配置，跨平台应采用上游 Meson 流程。本次二进制 `scrcpy-live` SHA256：`f9f8c02f887d1218a51dd953fc7dccf70dd4cc6557d46c5bca7ce08bd72171f2`。重建后需审查并更新入口的哈希，不能跳过校验。

原项目 Apache-2.0 许可证在 `../focus_experiment/LICENSE.scrcpy`；本目录补丁也以 Apache-2.0 提供。二进制仍依赖本机 Homebrew 动态库，尚不是独立分发包。

## 验证与边界

2026-09-08 10:54 的实机预检：设置 → 关于手机 → 返回，三张实时原生 PNG 均视觉核对通过；记录 `../../outputs/autoglm-20260908-105456-8ixvnfhg/`。无模型调用。主屏为桌面，需另测连续打字。

HONOR 的编码流标记 YCgCo/log316，本机 swscale 无法转换，当前只对该机 UI 原型覆盖为 BT.709 SDR；不声称颜色科学准确或适用于任意设备。静态帧可长期不更新，不能用“距当前时间很久”简单判为坏帧；Tap/Swipe/Back 后要求新写入并等待 0.5 秒静默。进程死亡、帧缺失或动作后长期没有新稳定帧则停止，不退回主屏。持续动画页面可能超时，源端时间戳对齐尚未实现。
