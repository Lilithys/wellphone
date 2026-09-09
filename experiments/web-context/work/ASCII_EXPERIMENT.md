# 副屏定向 ASCII：阶段 2（待实机验收）

```sh
python3 /Users/yishanma/Documents/Codex/wellphone-keyboard-experiment/run.py ascii
```

关闭上轮副屏，解锁手机，主屏打开短信草稿并调出键盘，不发送。每阶段在电脑回车后有 5 秒准备，随后主屏持续输入汉字；不要手动输入 `7` 或 `abc123`，不要在电脑副屏手动输入。异常立即 Ctrl+C。无需 API key，不调用 AutoGLM，不开启其 Type，不切 IME、不用剪贴板、不清空、不按 Enter。设置可能自行记录搜索历史，脚本不会清除历史或改任何设置。

| 阶段 | 动作与人工确认 |
|---|---|
| A | 创建副屏、打开设置，确认首页顶部搜索框可见且未滚动 |
| B | 点击首页搜索框 (540,410)；确认已到搜索页，不要求软键盘出现 |
| C | 点击搜索页顶部编辑框 (540,75)；确认框为空，仅有提示文字 |
| D | 检查副屏窗口、编辑框焦点和主屏 IME；仅发送 `7` |
| E | 人工确认 D 的文字仅落在副屏、主屏正常后，追加 `abc123`；预期 `7abc123` |
| F | 关闭本次副屏，继续观察主屏键盘 |

坐标仅对应本机 HONOR Magic3 的 1080×2400/420 原生帧。A/B/C 中的页面确认与 D/E 的落点确认都必须输入 `yes`；不确定就停止。各阶段主屏观察依提示输入数字，`0` 才是正常。

输入通过 `input keyboard -d <owned-display> text <fixed-payload>`，每次重新检查本次 scrcpy 显示存在、焦点标记已接受、主屏 top focus/IME display=0 且键盘显示、默认 IME 未变、主屏不在设置。通过 `dumpsys window displays` 确认副屏焦点窗口，再按副屏 `topResumedActivity` 的精确对象 ID 查询客户端层级（包名与 display 双重限制），要求唯一可见、启用、已获焦点的 EditText/SearchAutoComplete；不支持的 OEM 输出会停止，不降级为盲发。

View 层级解析依据 [AOSP View.toString 的标志位](https://android.googlesource.com/platform/frameworks/base/+/8360153/core/java/android/view/View.java)：第二组标志的第二位 F 才表示当前获焦点，不把第一组的“可获焦点”当成功。系统检查和实际输入不是原子操作；不能据此承诺任意应用、任意时刻绝不串屏，因此先测单字符并要求人工核验。命令成功返回也不代表文字送达；超时绝不重试。

记录在本目录 `outputs/ascii-input-*`：副屏 PNG、焦点采样、人工确认、每次输入尝试及脱敏层级元数据。不保存原始客户端层级或主屏内容，不上传。帧新鲜度是电脑接收时间证据，必须结合屏幕观察。实机结论最多覆盖本机设置页的这两个固定 ASCII 输入；不代表中文、通用 Type 或其他应用可用。

已有日志表明副屏 `showSoftInput` 遭到 display ID mismatch 拒绝，主屏输入保持正常。本实验验证的是“没有副屏软键盘时，定向字符能否抵达副屏编辑框”，不是继续要求弹出第二个键盘。

基线 `wellphone-versioned` 和 `baseline-no-steal-v1` 不变。若实验失败，关闭副屏；必要时手动恢复主屏草稿键盘，再运行 `python3 /Users/yishanma/Documents/Codex/wellphone-versioned/run.py baseline`。无需回滚或覆盖代码；代码快照不等于手机运行状态快照。

开发验证：85 项离线测试通过，93 个原始基线文件哈希一致；`ascii --help` 可启动，非交互运行会在操作手机前拒绝。尚未进行真实 ASCII 输入验收。

## 2026-09-08 16:41 实测：发送前的客户端层级校验停止

原始记录 `outputs/ascii-input-20260908-164151-c343wkrh/result.json` 保留不改。A–D 人工观察均正常；53 次采样均主屏 focus/IME display=0、键盘显示。输入尝试列表为空，说明尚未发送 `7`。副屏正常销毁，默认输入法未变。

已匹配副屏 43 的 Settings Activity 和焦点窗口，但客户端返回未通过 ACTIVITY 头校验。旧记录未保留返回头，不能断言是格式差异还是查询未找到目标。销毁后重放精确查询返回 no activities match，这不能证明当时输出相同，也不能仅凭随后的 Unknown command 提示认定过滤参数不受支持。

新增 `client_dump_metadata`：只记录 Activity 头数量、组件/对象 ID、PID、已知数字后缀、层级段数量和固定错误分类；不保存界面文字、未知后缀值或原始 dump。校验条件和输入流程尚未放宽。新增 2 项诊断/隐私测试，共 87 项离线测试通过。需要保留一次副屏搜索页，停在 D 阶段回车提示，进行只读诊断后再决定修正方式。

## 2026-09-08 17:05 只读定位与修复

用户将 `ascii-input-20260908-170002-q9kdk1x6` 停在 D 的回车提示，允许只读检查。副屏 45 的精确查询返回：

```text
ACTIVITY com.android.settings/.HWSettings 27befce pid=10368 userId=0 uid=1000 displayId=45(type=VIRTUAL)
```

失败原因已确认：旧正则要求行在 `pid` 后结束，误拒绝本机默认附带的三个详细字段；同时原输入框类名匹配未涵盖 `com.hihonor.android.widget.SearchView$HwSearchAutoComplete`。16:48 那次新诊断也记录了相同的完整后缀，排除了查询没有找到 Activity 的猜测。

修复仅接受旧式头或上述完整、已核对的详细头，详细头必须显示本次 display ID 且类型为 VIRTUAL；不忽略任意后缀。保留精确 Activity ID、组件、唯一窗口、编辑框可见/启用/获焦点和主屏 IME 校验。荣耀输入框仅新增**精确类名 + `android:id/search_src_text`**组合，不按模糊 AutoComplete 后缀放行。

17:05:19 在同一副屏只读复核成功：唯一输入框 `5f1c1d6` 的标志为 `VFED..CL. .F......`；查询前后 Activity/窗口相同，主屏 top focus=0、IME display=0、键盘显示。副屏只读结构证据存为该输出目录的 `readonly-editor-check.json`，不改运行中脚本的 `result.json`。本次没有点击、输入、切 IME 或关闭副屏。

新增 9 项回归测试，总计 96 项离线测试通过，93 个原始基线文件哈希仍一致。**只读校验通过不等于 ASCII 输入通过**。运行中的脚本已加载旧规则，必须 Ctrl+C 正常结束、等待副屏关闭，再重新运行同一个 `ascii` 命令测试 `7`，确认后才追加 `abc123`。
