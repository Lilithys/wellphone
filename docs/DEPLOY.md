# 部署：命令行授权的抖音消息任务

当前 `run.py` 使用旧版 AutoGLM 原生动作协议＋固定数字执行器，仅接受数字1，并保留首页、输入框、草稿和发送核对。2026-09-10 已在 HONOR Magic3 / Android 14 完成一轮监督式端到端发送；部署与运行以 [当前入口说明](BASELINE-ENTRY.md) 为准。

根入口调用 `experiments/douyin/work/run_douyin_test.py`。`run_douyin_auto.py` 是历史失败方案，不是默认入口。日历、设置与 Web 均不运行。

## 本机

```sh
cd /path/to/wellphone-delivery-20260909
export WELLPHONE_BASE_VENV=/path/to/scrcpyvenv
python3 run.py verify
python3 run.py tests
```

仅检查文件/依赖和离线测试，不连接手机或模型。需要当前既有 Python 环境、adb、scrcpy4.1和ffmpeg。新电脑依赖清单在 `experiments/douyin/baseline/python-packages.txt`；不要为交付盲目升级。本地客户端是macOS arm64，动态依赖FFmpeg63/avutil61/swresample7及SDL3，详见 `otool -L experiments/douyin/work/frame_stream/scrcpy-live`。其他平台/手机尚未验收。

密钥读取当前终端的 `PHONE_AGENT_API_KEY`；没有密钥则在任何手机连接前停止，不会弹出问答。不要把密钥贴进 Git、录屏或聊天。该入口不使用 DeepSeek。

## 先看解析，再运行

```sh
python3 run.py '在抖音给“联系人昵称”发送“1”' --dry-run
python3 run.py '在抖音给“联系人昵称”发送“1”' --serial YOUR_DEVICE_SERIAL
```

替换真实昵称和设备序列号；只有一台设备时可省略 `--serial`。`--dry-run` 无副作用，只输出联系人、内容与预算。实际运行授权本条消息任务，但仍需按提示确认首页、输入框、草稿和发送按钮；不满足就停止。支持明确单句模板，如“打开抖音给某昵称发1”；带引号的名称/内容更无歧义。消息仅接受安全 ASCII 字符，不接受中文、换行、百分号或任意 shell 文本；昵称可中文。

手机已解锁、USB调试已授权、抖音已登录且目标会话存在。执行时主屏保持原App和原有键盘，不切App、不打开抖音、不点副屏。当前仍按原基线检查主屏Activity/键盘；无需手动回答是否正常，但不满足就停止。Ctrl+C停止后会清理自有副屏并尝试恢复抖音音频原值。

## 副作用、费用与结果

会读取副屏抖音画面并将任务和截图发给 AutoGLM 服务，可能包含私聊。默认最多20次模型请求；预算不足时不开始输入/发送。导航可能改变消息列表的展示状态。临时限制整个抖音包的音频/音频焦点，不修改全局音量、输入法或剪贴板，不是通用副屏音频隔离。

结果与私有截图保存在 `experiments/douyin/outputs/`。最新一轮记录为 `passed=true`、`send_status=USER_CONFIRMED_OUTGOING`；这只表示本机外发状态，不证明对方收到。最新一轮用户观察主屏正常、无额外声音，副屏和音频已清理；不外推到长期稳定。

## 去重和中断

同设备、同联系人、同内容的默认任务标识固定。输入或发送已尝试后不自动重做；结果未知、旧草稿或同名对象会停止。不要删除日志、换目录或清空草稿绕过保护。

如果已有输入/发送尝试，程序会优先停止要求核对；不要删除日志、换目录或并行运行来绕过防重复记录。不同工作区的状态未自动合并。

音频恢复失败时，使用本副本原值记录恢复：
```sh
python3 run.py audio --restore --serial YOUR_DEVICE_SERIAL
```
旧工作区的遗留音频限制必须用其对应原值记录，不猜测、不统一恢复为allow。当前命令没有运行权限/登录/付款代理；遇到这些页面会停。
