# 非展示屏：预检与一次完整监督式流程已通过

2026-09-10 的默认非展示屏完整流程在同一台 HONOR Magic3 / Android 14 上通过：副屏创建及实际标记回读、抖音导航、联系人定位、编辑框焦点回读、固定 ASCII `1` 输入、发送前核对和单次发送均完成；主屏持续打字观察为正常，未观察到额外声音，副屏关闭且音频原值回读恢复。记录位于上级 `outputs/douyin-flow-20260910-105157-x5pfdk_y/`（私有、被 Git 忽略）。

这仍是一次监督式验收，不等于长期稳定性、任意 App 兼容性、对方收件回执或低帧率参数已验收。低帧率版本的 `actual_source_fps_verified` 仍需单独验证。

## 依据与假设

14:11 display66 的初始帧已缺少外层导航，无模型/点击；Activity仍为抖音，窗口匹配失败原因是 `FOCUSED_WINDOW_MISMATCH`。这只能说明焦点窗口未满足原来的匹配条件，不能证明它就是Presentation窗口。

scrcpy v4.1 的 [NewDisplayCapture.java](https://github.com/Genymobile/scrcpy/blob/v4.1/server/src/main/java/com/genymobile/scrcpy/video/NewDisplayCapture.java) 默认设置 `VIRTUAL_DISPLAY_FLAG_PRESENTATION`。[Android 官方说明](https://developer.android.com/reference/android/hardware/display/DisplayManager#VIRTUAL_DISPLAY_FLAG_PRESENTATION)：这会把显示列入展示屏类别，应用可能自动向其投放内容；不设此标记通常不自动投放，但应用仍可以向显示投放。因此“另一个纯视频展示窗口盖住应用UI”只是待检验假设，不是已确认根因。

## 运行与回退

display67/68/69 三次独立无模型预检通过，见[验收范围与记录](startup-acceptance.md)。二进制未改变；只扩大到已有的人工首页确认＋一次消息导航流程，不开放通用操作。

当前接力测试：

```sh
python3 /path/to/wellphone-douyin-experiment/run.py douyin --startup-only --confirm-home-first --non-presentation --serial YOUR_DEVICE_SERIAL
```

运行时须同意副屏截图上传给AutoGLM。人工确认正常首页输入`1`；只有红圈为外层底部“消息”入口才输入`messages`，到消息列表后再核验一次。最多一次消息点击，缺少导航不接模型，不进会话、不输入、不发送；所有清理和音频恢复沿用原流程。

仍可独立运行无模型预检：

```sh
python3 /path/to/wellphone-douyin-experiment/run.py douyin --preflight --non-presentation --serial YOUR_DEVICE_SERIAL
```

显式同意后才创建显示。仅去掉本次新显示的PRESENTATION标记；保留PUBLIC、OWN_CONTENT_ONLY、原焦点标记、IME策略、分辨率、系统装饰、音频事务及全部隔离检查。不强停/重置/清空抖音，不切输入法，不改主屏；`--preflight`不调用模型或执行GUI输入。服务端实际回读焦点标记和PRESENTATION缺席；Python检查回读日志与本次display匹配，缺失即禁止启动App，模型入口也再次检查证据。音频原值仍需恢复。

原窗口诊断自动记录启动与用户回答后的副屏证据；完整顶部/底部导航才答“是”，没有答“否”。先做启动对照，仍观察主屏键盘和背景音乐；即使通过一次，也不能泛化成长期稳定或其他App已兼容。

不加 `--non-presentation` 就使用原服务端；新开关只允许 `--preflight` 或同时带有 `--startup-only --confirm-home-first`，缺少人工首页门槛、混用模式、数字发送等组合在接触设备前拒绝。新二进制独立保存，93个冻结文件不变，未晋升完整baseline。异常不自动切回原服务端、不自动重试。

## 构建复现

校验 `build-manifest.json` 里的官方v4.1源码归档SHA-256，在新建临时目录解压。先应用原 `../focus_experiment/no-steal-focus.patch`，再应用本目录 `non-presentation.patch`。后者相对已打焦点补丁的代码，仅删除一个显示类别标记并增加回读检查/日志；不删除焦点隔离。

使用 `../focus_experiment/build-manifest.json` 所列JDK17、Android Platform36和Build Tools36.0.0。按上游 `server/build_without_gradle.sh` 构建，`BUILD_DIR`必须是通过`mktemp -d`创建的新目录；上游构建脚本会清理该目录的构建中间文件，不能指向仓库或已有数据目录。产物为该构建目录下`scrcpy-server`。本次构建完全本地进行，没有调用ADB、云模型或安装全局工具。许可证沿用 `../focus_experiment/LICENSE.scrcpy`。
