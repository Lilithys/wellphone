# checkpoint-douyin-ascii-v1

2026-09-09，HONOR Magic3 / Android 14。本地可恢复的**固定数字输入里程碑**，不是完整发消息或无人值守 AutoGLM 验收。

## 已确认

- display88：同一次非展示屏、5fps 虚拟显示，从首页到消息列表，滑动定位已有一对一小号336789，打开并聚焦空白聊天框。
- 系统客户端层级确认该副屏、抖音 UID、Activity/窗口和 `SearchableEditText app:id/msg_et` 的焦点归属；实际输入前再次核对。
- 专用执行器仅一次 `input keyboard -d 88 text 1`；副屏新图显示草稿完整内容恰为 `1`、发送按钮可见，没有副屏软键盘。不使用上游通用 Type、不切 IME、不使用剪贴板。
- 副屏由助手依据本轮原生截图核对，未调用 AutoGLM。用户随后反馈：“主屏没有受影响”，并说明自己已关闭副屏。
- 清理回读：本次虚拟显示已移除，抖音 PLAY_AUDIO / TAKE_AUDIO_FOCUS 原值已恢复。

## 不能混淆的结果

尚未点击发送；不证明对方收件、中文、通用输入或其他 App。旧草稿 `1` 可能保留；不得自动清空、追加或重输，输入尝试日志继续生效。

原始结果仍为 `passed=false`、`fixed_input_verified=false`：脚本原本暂停在输入后确认；用户关屏后，我们输入取消以触发清理，未在旧会话继续批准发送。用户对主屏的事后确认记在本说明及会话中，不篡改原结果为全流程通过。

20:39:02 等待期间还出现 `state.default_ime: STDERR_PRESENT` 只读采样失败；日志未提供可用观察状态，不能断言主屏实际发生异常，也不能宣称整轮自动监测无错误。用户主观主屏观察正常；本轮声音/背景音乐没有完整补充验收。

## 本机证据（私聊画面不入 Git）

- 结果：`outputs/douyin-executor-20260909-203103-niju45t8/result.json`
- 结果 SHA256：`927890cc3d600c01b56cfb192c8c992c4097de7af4058ba237ab980f1835a17b`
- 输入后图：同目录 `after-input-one.png`
- 图片 SHA256：`4b35e4b5b90ab8dbda661b64fd571ab5924239f8e28d67e8f9531800e01b7d7e`
- 单次输入日志：`outputs/douyin-input-one-3905a1eb966fd9efff080352.json`。不可为重跑而删除。

版本保存原有93个冻结文件；487项离线测试通过，不将离线结果当作手机实测。恢复代码可从 tag `checkpoint-douyin-ascii-v1` 新建分支/工作树；这不撤销手机草稿或历史尝试日志。中文试验另放临时目录，不改本基线代码。
