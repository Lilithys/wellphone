# AutoGLM × 同机焦点隔离联调

状态（2026-09-08）：底层 A–F 连续打字已通过一轮；新入口的真实手机预检通过，39 项离线测试通过。**真实 AutoGLM 模型闭环 + 连续打字尚待用户正式测试。**

`手机副屏 → scrcpy 低延迟视频 → ffmpeg 原生 PNG → AutoGLM → 动作限制/会话检查 → adb input -d 副屏ID`

副屏由独立 scrcpy 4.1 实验服务端创建，要求 `OWN_FOCUS + STEAL_TOP_FOCUS_DISABLED`。主屏仍由用户操作。此次仅测试“进入系统设置的关于手机页面”，不输入文字、不自动发送消息、不切换默认输入法。

## 运行（在本目录）

```sh
# 连接并解锁手机，允许 USB 调试；退出旧的 scrcpy 测试窗口。
# 检查设置 → 关于手机 → 返回的实时截图，不调用模型、不需要 key：
python3 run_autoglm_focus.py --preflight

# 预检通过后，正式联调：
python3 run_autoglm_focus.py
```

脚本自动使用 `/Users/yishanma/Desktop/wellphone/scrcpyvenv`，依赖 PATH 中的 adb、scrcpy 4.1、ffmpeg，以及项目自带的两份校验过的实验二进制。主屏继续使用之前的短信草稿和输入法，不发送消息，也不要在主屏打开系统设置。正式联调电脑按回车后有 5 秒准备时间，然后持续在手机打字；不要手动操作或关闭 scrcpy 预览窗口。

环境变量：`PHONE_AGENT_API_KEY`（可选，缺失时在 Terminal 隐藏输入，不保存）；`WELLPHONE_PROJECT_DIR`（可选，覆盖项目路径）。密钥不放在命令行、不传给 scrcpy、不发到聊天里。旧入口 `zsh run_autoglm_e2e.sh` 现在也转入新流程，不再等待 `READY_FOR_KEY` 管道。

模型固定为 `autoglm-phone`，端点固定为 `https://open.bigmodel.cn/api/paas/v4`；仅发送副屏系统设置画面（关于手机页面含设备标识）。默认最多 8 步，HTTP 超时 45 秒、禁用自动重试。新入口直接从副屏视频取帧，不依赖 Mac 窗口截图或屏幕录制权限，不会退回到主屏截图。

## 判断结果

- `--preflight`：确定性脚本进入设置、关于手机并返回；只证明当前会话和实时截图路径可用，不代表模型任务完成。
- 正式联调：记录逐步动作，并用实际 Activity 校验是否到达本机解析出的“关于手机”页面；不会只相信模型的 finish。
- `task_verified=true` 证明目标页面到达，**不等于主屏完全不受影响**。还需核对焦点采样和用户最后填写的 `human_observation`，观察是否白屏、键盘收起或丢字。
- 记录在 `../outputs/autoglm-*/result.json`，另有副屏预检/最终 PNG、scrcpy/decoder 日志。视频经临时 FIFO 解码，只保留最新 PNG，不保存完整录像。设置页含设备信息，分享或提交截图前检查并遮盖；不记录主屏输入正文或密钥。
- 关闭窗口、断开手机、焦点标记未确认或虚拟显示消失时停止；Ctrl+C/SIGTERM 清理本次拥有的 scrcpy 会话，不杀其他 scrcpy 进程。

边界：动作限制禁止 Type/Home/其他应用 Launch 等，但允许的 Tap 仍依赖模型识别，不能宣称对任意页面具有“绝不误改设置”的语义保证。文字输入、权限弹窗、多应用并发和同一应用在双屏同时使用未通过验收。

本次实机证据：`../outputs/autoglm-20260908-105456-8ixvnfhg/`，三张 1080×2400 PNG 经视觉核对对应设置/关于手机/返回；35 次顶层焦点采样均为 display 0，副屏正常销毁。此次主屏是桌面、键盘未显示，**不能用这次预检替代连续打字验收**。旧预检目录中有缩略图和延迟帧实验，不能仅凭其中的 `preflight_passed` 判定图像合格。

低延迟客户端补丁、复现构建与局限见 [frame_stream/README.md](frame_stream/README.md)。动作后等待新帧及 0.5 秒无新写入，超时停止；这是当前静态设置页的同步启发式，不是任意动画界面的严格新鲜度证明。

离线回归：`/Users/yishanma/Desktop/wellphone/scrcpyvenv/bin/python -m unittest discover -s . -p '*_unit.py' -v`。
