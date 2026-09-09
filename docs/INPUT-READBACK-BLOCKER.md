# 副屏编辑框回读：原因与修复（2026-09-10）

结论：已定位本机客户端层级读取超时，交付入口已改用同权限、按副屏限定的另一条读取路径。新解析器在同一真机输入框连续三次通过；本轮没有输入、没有发送，不宣称完整任务通过。

## 为什么之前能输入，现在常停在输入前

固定 ASCII 命令本身没有在这些失败轮次执行。停止点是输入之前：程序必须先拿到完整且属于本次副屏的编辑框焦点证据。

只读提取这台 HONOR Magic3 / Android 14 的 `/system/framework/services.jar`，检查 DEX 调用常量发现两条路径不同：

| 系统读取路径 | 本机内部 TransferPipe 超时 | 结果 |
| --- | --- | --- |
| `ActivityTaskManagerService.dumpActivity`，按 Activity token | 60 ms | 同一目标两次输出 `java.io.IOException: Timeout`，输出长度不同 |
| `ActivityRecord.dumpActivity`，按包名和 display 过滤 `activities` | 2000 ms | 连续三次完整层级，均识别到同一个获焦 `SearchableEditText` |

这解释了为何旧路径可能偶尔拿到证据、也可能在同一页面中途截断；不是“成功过的 ASCII 输入没保存”。主机侧总耗时包含 ADB 等开销，不等于内部 TransferPipe 限时。提高主机 subprocess timeout 不能修改厂商硬编码的 60 ms。

框架 SHA-256：`5aead170501cc3e5a13ed432b5962de338781eae360c4db0071f27f417630cec`。框架副本仅留在本机临时目录，未加入仓库。没有刷机、root、修改系统文件或提升手机执行权限。该数值仅代表本次设备构建，不能推广到所有 Android 手机。

## 实际改动

- 根入口选择 `--editor-read-mode activity-list`；按包名和本次虚拟显示过滤客户端层级，不读取主屏客户端控件树。
- `douyin_activity_client.py` 核对唯一 display、唯一 Activity 及其 RESUMED 状态、用户和应用 UID/进程，并要求同一客户端 `View Hierarchy` 后存在 `Looper` 边界。截断、错误、多 Activity、未知结构均停止。
- 解析器不会伪造旧 `ACTIVITY ... displayId=...` 头；证据明确标记来源为 `activity_list`，虚拟显示身份仍由执行会话的实时检查提供。
- `require_editor` 前后核对同一 Activity/窗口；定向输入之前仍做两次独立焦点检查和画面核对。固定输入命令、单次日志、防重复和发送确认未变。新模式失败不自动退回旧路径或重试输入。
- 另将后台只读 ADB 子进程及低帧率 scrcpy 的标准输入断开，避免与终端 `input()` 竞争。这是独立修正，不把它当作客户端层级超时的原因。

## 验证与边界

- 旧读取在同一会话的两次主机耗时为 145 / 143 ms，均明确记录 `transfer_pipe_timeout`。
- 新读取连续三次主机耗时 137 / 141 / 221 ms，均匹配同一目标和同一可见、启用、获焦编辑框 `app:id/msg_et`。这是只读解析路径验证，不是输入或收件证明。
- 527 项执行器离线测试、17 项根入口测试通过；279 个冻结文件校验通过，原基线仓库工作树仍干净。
- 诊断副屏已移除，抖音两项音频设置已恢复并回读。没有模型请求、没有输入和发送。主屏结构化采样无异常；没有本轮真人观察反馈，主屏手感/背景音乐标记为未观察或未测试。

正常运行命令不变，见 [当前入口](BASELINE-ENTRY.md)。仍需一轮完整流程验收；若存在旧输入/发送尝试，必须先核对真实状态，不删除日志或换目录绕过防重复机制。

## 保留的历史结论

- 原 `checkpoint-douyin-ascii-v1` 曾确认定向输入草稿 `1` 和用户主屏正常反馈；那轮不是 AutoGLM 全自动发送验收，原始代码未改。
- 09-09 23:43:58 两次 `client_dump_failed` 的旧记录没有具体异常分类，不能追溯断言每次都是同一超时。
- 09-09 23:51:33 独立诊断返回 1537 行、227950 字节且无已识别错误，但编辑框候选为零；当时不能下结论。其记录保留，不因这次找到明确超时而改写为已证明的同源故障。

私有记录在 `experiments/douyin/outputs/`；不提交截图、原始控件树或聊天记录。公开摘要见 `evidence/editor-read-path-validation.json`。
