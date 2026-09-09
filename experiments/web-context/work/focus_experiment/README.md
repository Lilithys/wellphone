# Wellphone 焦点隔离实验（scrcpy 4.1）

状态：HONOR Magic3 / Android 14 的 A–F 连续打字测试已通过一轮（2026-09-07 23:26 会话，用户确认全部正常；87 次动作期间采样均为顶层焦点 0、IME display 0、键盘显示）。尚未外推到其他应用、输入法或副屏文字输入。AutoGLM 联调见上级目录的 `AUTOGLM_FOCUS.md`。

## 运行

在本目录的上一级 `work` 目录运行，无需虚拟环境、API key 或模型：

```sh
python3 test_focus_isolation.py
```

继续使用之前测试的主屏应用和输入法，保持变量一致。每阶段电脑按回车后，有 5 秒准备时间，再持续在手机上打字。不要在电脑 scrcpy 窗口点击或打字。A–F 均正常才算候选通过；之后至少再重复一次。

本次保持 `local` 输入法策略及原有副屏桌面设置，界面/输入行为的改动是焦点标记；另外增加只读焦点采样。采样可能漏掉短暂变化，不能代替人眼观察，也不能证明没有丢字。结果保存到 `../../outputs/keyboard-*/result.json`，包含包名/Activity 和焦点状态，不含输入正文、截图或 API key。

仅验证空显示（不启动桌面和应用、不注入输入）：

```sh
python3 test_focus_isolation.py --probe-only
```

空显示模式没有 Mac 窗口，编码输出直接丢弃到 `/dev/null`，不保存录像。因为空显示可能没有产生视频帧，关闭时出现 `Recording stopped before headers` 不代表焦点检查失败；以返回码、`focus_flags_verified_log` 和 `virtual_display_removed` 为准。

## 改动与边界

`test_focus_isolation.py` → 仅本次子进程的 `SCRCPY_SERVER_PATH` → `scrcpy-server-focus` → 同一手机的虚拟显示。

- 从官方 scrcpy v4.1 构建，只修改 `NewDisplayCapture.java`，补丁见 `no-steal-focus.patch`。
- 从手机运行时反射读取 `OWN_FOCUS`、`STEAL_TOP_FOCUS_DISABLED`，不把 Android 大版本号当成能力证明。
- 创建时请求两个标记，并检查返回的 `Display.getFlags()`；缺少能力或标记未被接受时终止。标记接受不保证 OEM 输入法隔离正确。
- 不改系统全局焦点配置、不 root、不替换 Homebrew 安装、不改 AutoGLM、不切换手机默认输入法。
- 原测试保留：`python3 test_keyboard_isolation.py --ime-policy local`。如果你手动设置过 `SCRCPY_SERVER_PATH`，运行原测试前应先取消该变量。
- 实验版仅匹配 scrcpy 4.1；回到原命令就使用原安装，不需要回滚系统。

## 构建来源

源码：[Genymobile/scrcpy v4.1](https://github.com/Genymobile/scrcpy/tree/v4.1)，许可见 `LICENSE.scrcpy`。构建工具下载自 Eclipse Temurin 和 Google Android SDK，校验值见 `build-manifest.json`，没有安装到全局目录。

复现：下载/解压官方 v4.1 源码，在源码根目录执行 `patch -p1 < /absolute/path/no-steal-focus.patch`。准备 JDK 17、Android SDK Platform 36、Build Tools 36.0.0；将 JDK 的 `bin` 加入 `PATH`，设置 `ANDROID_HOME` 为该 SDK。用 `mktemp -d` 创建独立构建目录，设置 `BUILD_DIR` 指向它，执行 `bash server/build_without_gradle.sh`。输出为 `$BUILD_DIR/scrcpy-server`。该上游脚本会清理构建目录下的中间产物，所以不要把 `BUILD_DIR` 指向已有项目目录。

先前空显示探测：display 29，实际 flags `0x1f88`；前/中/后三次采样均为顶层焦点 0、IME display 0、`mInputShown=true`。之后 A–F 测试使用 display 30、flags `0x1fc8`，结果见 `../../outputs/keyboard-20260907-232655-ocvppn0i/result.json`。两次均已确认虚拟显示销毁。
