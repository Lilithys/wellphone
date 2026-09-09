# 部署：命令行授权的抖音消息任务

> 已撤回下面描述的新自动入口。当前 `run.py` 使用旧监督执行器，仅数字1、无需API Key、保留人工提案和核对；部署与运行以 [当前入口说明](BASELINE-ENTRY.md) 为准。以下保留为上一版尝试记录。

根入口调用新的 `experiments/douyin/work/run_douyin_auto.py`。历史 `run_douyin_test.py` 保留人工监督实现供对照，不是新入口。日历、设置与Web均不运行。

## 本机

```sh
cd /Users/yishanma/Documents/Codex/wellphone-delivery-20260909
export WELLPHONE_BASE_VENV=/Users/yishanma/Desktop/wellphone/scrcpyvenv
python3 run.py verify
python3 run.py tests
```

仅检查文件/依赖和离线测试，不连接手机或模型。需要当前既有 Python 环境、adb、scrcpy4.1和ffmpeg。新电脑依赖清单在 `experiments/douyin/baseline/python-packages.txt`；不要为交付盲目升级。本地客户端是macOS arm64，动态依赖FFmpeg63/avutil61/swresample7及SDL3，详见 `otool -L experiments/douyin/work/frame_stream/scrcpy-live`。其他平台/手机尚未验收。

密钥读取当前终端的 `PHONE_AGENT_API_KEY`；若此前已导出，继续用同一终端。没有密钥则在任何手机连接前报错，不会弹出问答。不要把密钥贴进Git、录屏或聊天。新的单任务流程不使用DeepSeek。

## 先看解析，再运行

```sh
python3 run.py '在抖音给“联系人昵称”发送“1”' --dry-run
python3 run.py '在抖音给“联系人昵称”发送“1”' --serial YOUR_DEVICE_SERIAL
```

替换真实昵称和设备序列号；只有一台设备时可省略 `--serial`。`--dry-run` 无副作用，只输出联系人、内容与预算。实际运行即授权本条消息发送，不再确认首页、输入、发送或主屏观察。支持明确单句模板，如“打开抖音给某昵称发1”；带引号的名称/内容更无歧义。消息仅接受安全ASCII字符，不接受中文、换行、百分号或任意shell文本；昵称可中文。

手机已解锁、USB调试已授权、抖音已登录且目标会话存在。执行时主屏保持原App和原有键盘，不切App、不打开抖音、不点副屏。当前仍按原基线检查主屏Activity/键盘；无需手动回答是否正常，但不满足就停止。Ctrl+C停止后会清理自有副屏并尝试恢复抖音音频原值。

## 副作用、费用与结果

会读取副屏抖音画面并将任务和截图发给AutoGLM服务，可能包含私聊。默认30次模型请求，上限可用 `--max-steps 20` 降低，最大40；预算不足时不开始输入/发送。导航可能改变消息列表的展示状态。临时限制整个抖音包的音频/音频焦点，不修改全局音量、输入法或剪贴板，不是通用副屏音频隔离。

结果与私有截图保存在 `experiments/douyin/outputs/`。模型前后图核验通过只表示识别到了新增己方消息，不证明对方收到；新自动流程尚未真机验收。没有真人体感反馈，不会伪记声音/打字手感正常。

## 去重和中断

同设备、同联系人、同内容的默认任务标识固定。输入或发送已尝试后不自动重做；结果未知、旧草稿或同名对象会停止。不要删除日志、换目录或清空草稿绕过保护。

如果上一条已经确认完成，而你明确要发送另一条相同内容，可指定新任务标识，例如 `--request-id next-demo`。新标识不能绕过该联系人仍未解决的输入/发送记录。不同工作区的状态未自动合并，不要并行运行或依靠副本规避旧记录。

音频恢复失败时，使用本副本原值记录恢复：
```sh
python3 run.py audio --restore --serial YOUR_DEVICE_SERIAL
```
旧工作区的遗留音频限制必须用其对应原值记录，不猜测、不统一恢复为allow。当前命令没有运行权限/登录/付款代理；遇到这些页面会停。
