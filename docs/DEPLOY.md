# 部署：先保持已验收环境，不在交付前升级依赖

## 本机复现

此目录是独立交付副本，原工作区和旧标签没有改动。主线代码在 `core/`，根入口仅转交给它。先在终端 `cd` 到交付根目录。

```sh
export WELLPHONE_BASE_VENV=/Users/yishanma/Desktop/wellphone/scrcpyvenv
export WELLPHONE_PLANNER_MODEL=deepseek-v4-flash
export WELLPHONE_PLANNER_BASE_URL=https://api.deepseek.com
python3 tools/check_release.py
python3 run.py verify
python3 run.py router plan '明天下午4点安排30分钟的「交付演示」日程；打开设置，进入关于手机页面'
```

上述 `plan` 默认为有限规则理解，不联网、不操作手机。用 `--planner llm` 才发送任务文字/当前时间到指定模型，产生费用。模型与接口配置沿用已验收账号；不能把 DeepSeek 密钥当 AutoGLM 密钥。不需要安装附带的 ADBKeyboard.apk，主线不使用通用 Type。

## 首次连接这个工作目录

手机保持解锁，USB 调试已授权；只连接一台设备，或在 router 命令末尾加 `--serial YOUR_SERIAL`。主屏打开无收件人或不发送的短信草稿，调出原有键盘，不打开日历或设置。

```sh
adb devices
python3 run.py router doctor
python3 run.py router calendar-test
```

`doctor` 是只读设备和日历检查。`calendar-test` 不是 dry-run：经确认后真实创建一条明天下午的测试日程，无提醒/邀请，保留事件并回读；如有多个日历，明确加 `--calendar-id ID`。完成后资格写入 `core/outputs/qualifications/`。

本包刻意没有复制原日历 journal、设备资格、抖音草稿/发送日志。**复制源码不等于复制运行状态，也不撤销已经发生的写入。** 使用本包前结束其他工作区任务；旧目录的锁不会与新 `core/outputs/locks` 自动共享。旧记录都仍在原目录，不可删除以绕过失败保护。新目录首次重新做资格验收；不要重放旧的、不确定是否成功的写入任务。

## 密钥与运行

最省心的方法是不导出密钥，运行时在终端按提示隐藏输入（先 DeepSeek，执行 GUI 时 AutoGLM）。若已在原终端导出 `DEEPSEEK_API_KEY` / `PHONE_AGENT_API_KEY`，在同一终端运行即可。不要在 README、录屏、Git 或工单中粘贴密钥。

```sh
python3 run.py router run '明天下午4点安排30分钟的「交付演示」日程；打开设置，进入关于手机页面' --planner llm
```

每个确认都应基于真实观察回答，不预填 yes。任务执行期间持续在短信草稿打字；按脚本提示返回电脑记录观察，再回手机继续。结果与新状态在 `core/outputs/`。若拒绝、超时或连接中断，保留记录，不反复重跑；不要在程序执行中手动操作/关闭副屏。完成后再从手机日历查看新事件。

## 新电脑

- 当前预编译 `scrcpy-live` 是 macOS arm64，动态依赖 `/opt/homebrew/opt/ffmpeg/lib` 的 avformat/avcodec 63、avutil 61、swresample 7，以及 SDL3。先以 `otool -L core/work/frame_stream/scrcpy-live` 核对，`tools/check_release.py` 也会检查本机依赖是否存在。
- Python 验收环境为 3.14.6；源码注明 3.10+，未逐一验证其他版本。依赖清单在 `core/baseline/python-packages.txt`，上游最低依赖在 `core/app/requirements.txt`。新虚拟环境可执行 `python3 -m venv .venv`、`.venv/bin/python -m pip install -r core/baseline/python-packages.txt`，再设置 `WELLPHONE_BASE_VENV` 为该环境绝对路径；若版本不可获取，不自动升级，应先核对再复测。
- 不把最新 Homebrew 安装视为兼容保证；自带二进制缺依赖时，参照 `core/work/frame_stream/README.md` 重建，并经审查更新对应哈希，不能关闭校验。服务端补丁和构建来源在 `core/work/focus_experiment/`。
- Windows/Linux、Intel Mac、iOS、其他手机和输入法尚未验收；这里交付的是可在当前本机演示的研究原型。

## 实验源码

默认不运行 `experiments/`。其主入口及历史 README 是原分支快照，里面的绝对路径/测试对象需自行核对，不能当成开箱即用的通用任务。各副本的锁和防重复状态可能不共享，必须串行运行。抖音已有未发送 `1` 草稿的可能；换目录不表示可以再输入/发送一次。Web 没有手机完整闭环验收；这两个实验都不作为主视频成功任务。
