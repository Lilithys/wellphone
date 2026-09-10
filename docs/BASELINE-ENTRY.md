# 当前入口：旧 AutoGLM 动作协议＋固定数字执行器

按用户要求撤回根入口的新JSON自动流程；原始 `wellphone-douyin-experiment` 仓库的 `checkpoint-douyin-ascii-v1` 标签及工作树未修改。交付副本用旧版AutoGLM原生 `do(...)`/`finish(...)` 协议生成导航提案，连接联系人参数；后续修正了输入前的只读层级获取路径。原有动作检查、坐标范围、固定输入1、发送核对及音频/焦点隔离策略保留。

```sh
python3 run.py '在抖音给“联系人昵称”发送“1”' --dry-run
python3 run.py '在抖音给“联系人昵称”发送“1”' --serial YOUR_DEVICE_SERIAL
```

只支持明确单条指令与数字1。联系人来自该条命令，没有默认真实联系人；旧实验的匿名默认名仅供历史测试。根入口在子进程中设置 `WELLPHONE_DOUYIN_RECIPIENT`，覆盖继承的同名变量；不永久导出、不修改当前终端环境。收件人检查、提示及旧式输入/发送日志哈希使用同一值。此变量不是API Key。

实际转到交付副本的旧入口：

```text
experiments/douyin/run.py douyin
  --send-one-flow --confirm-home-first
  --non-presentation --auto-messages --low-fps-trial
  --editor-read-mode activity-list
  --reviewer user --message 1 --max-steps 20
```

`--reviewer user` 如实记录本地核对来源。程序会创建旧版AutoGLM客户端，使用当前终端的 `PHONE_AGENT_API_KEY`，把本轮副屏截图按步发送到模型。模型返回原生受限动作，用户不再填写一步JSON。原有首页确认、输入前/后核对、发送确认及主屏观察仍保留；输入或发送可能真实改变聊天状态。

`--editor-read-mode activity-list` 使用 `dumpsys activity -c -p <抖音包名> -d <本次副屏> activities`。本机系统框架证实：原按 Activity token 读取路径的内部超时仅 60 ms，此列表路径为 2000 ms；没有修改系统超时或权限。新解析器要求唯一副屏、唯一匹配且已恢复的 Activity、对应应用 UID/进程、完整客户端层级结束标记和唯一获焦编辑框，前后还须确认同一 Activity/窗口。错误、截断、多 Activity 或未知格式均停止，不自动回退、重点击或盲打。

本机同一输入框连续三次只读检查通过；随后 2026-09-10 10:51 的完整监督式运行又在同一设备完成输入和发送，主屏观察正常。原 `activity-token` 路径和 `--allow-editor-reobserve` 保留供显式对照，根入口不再启用它们。诊断只记录耗时、长度和结构化证据，不保存原始转储、异常正文或聊天内容。详见 [读取问题与修复证据](INPUT-READBACK-BLOCKER.md)。

启动模型收到的是控制器拆分后的完整局部任务，“只点击消息”不需要同时暴露后续联系人和发送步骤。若模型仍误称任务被截断并索要补充，控制器只在没有执行任何手机动作时追加固定澄清、重问一次；不从自然语言猜坐标，第二次仍拒绝即停止。

消息导航的结构比较框限定在已测1080×2400布局的底部导航带。此前Tap y=961的通用比较框从原生y=2242开始，会纳入上方视频；当前模板从至少y=2268开始，确保平移搜索也位于导航带内。消息文字、徽标和左右导航仍参与匹配，相关度、位移上限和实际点击坐标不变；输入/发送沿用原检查。已用本轮私有截图离线复现并验证，见 `evidence/navigation-band-validation.json`。

如果缺少API Key，程序会在需要模型前安全停止或要求隐藏输入；不会退回手写坐标。该流程仍会等待有限的页面和发送核对，不是完全无人值守。

## 最近一次端到端验收

记录：`experiments/douyin/outputs/douyin-flow-20260910-105157-x5pfdk_y/result.json`（本机私有输出，不入 Git）。结果：`passed=true`、`fixed_input_verified=true`、`send_attempted=true`、`send_status=USER_CONFIRMED_OUTGOING`、`recipient_delivery_verified=false`；用户观察主屏正常、无额外声音，`virtual_display_removed=true`、`audio_cleanup_status=RESTORED`。这是一轮监督式通过，不把对方收件或连续火花写成已验证。

## 状态不能跟着代码回退

原基线仅确认过副屏草稿1和用户主屏正常反馈；该历史轮次未由程序发送。最新一次已完成监督式发送，但旧手机草稿、输入/发送尝试、音频原值记录仍保留；碰到旧记录不删除、不强制重跑。不同仓库状态不自动合并，尤其不能以切换副本绕过原实验的未决记录。

新 `run_douyin_auto.py` 和相关测试保留作失败方案记录，但根入口不再调用它；`--request-id` 不适用于旧基线。旧模型流程首请求解析失败，不对结果重试，也不假称模型发送已通过。

离线验证：`python3 run.py tests`；冻结文件验证：`python3 run.py verify --files-only`。旧发布ZIP、Git标签和已提交历史不会自动随入口变化更新。
