# Web 资料＋明确上下文 → 复习计划

独立工作树 `wellphone-web-context`，分支 `feature/web-context`，从已验收路由器 `e246814` 建立。不是通用浏览器 Agent，不使用隐藏浏览器、网页 JS、MCP 或 AutoGLM；公开资料在电脑上读取，手机端复用相同的 Calendar Provider 执行器。旧 GUI、日历、规划配置文件保持不变。

## 第一次运行

在原来导入过 DeepSeek Key 的 Terminal 中：

```sh
cd /Users/yishanma/Documents/Codex/wellphone-web-context
export WELLPHONE_PLANNER_MODEL="deepseek-v4-flash"
export WELLPHONE_PLANNER_BASE_URL="https://api.deepseek.com"
export WELLPHONE_WEB_PROXY="http://127.0.0.1:7897"
python3 run.py web init
```

代理变量仅适用于当前 Mac 已启用的本机代理，不修改系统网络设置。`init` 询问实际开始时间（北京时间 `YYYY-MM-DD HH:MM`）和时长（15–120 分钟），不猜日期、不联网。生成 `outputs/web-context-*/context.json`，打印下一步完整命令。

初始目标/背景来自 Wellphone 项目示例，应检查并编辑为真实情况。默认资料改为 [AOSP 显示支持/每显示焦点](https://source.android.com/docs/core/display/multi_display/displays)、[IME 支持](https://source.android.com/docs/core/display/multi_display/ime-support) 与 [Calendar Provider](https://developer.android.com/identity/providers/calendar-provider)。背景明确写入本项目焦点标记、定向输入和已验收边界，不把物理端口映射或全局焦点配置当成本项目实现。最多 3 个 URL、6 个关注点、3 个明确且不重叠的可用时段；时区固定 Asia/Shanghai。

按打印的具体路径运行 `web plan --context '路径'`。先展示模型服务、网页、目标、背景、关注点和时段，输入 `yes` 才获取网页并把正文与这些上下文发送给指定模型。读取 `DEEPSEEK_API_KEY`，缺失时隐藏输入，不借用 AutoGLM Key。不连接手机、不读取截图/日历、不写入或发通知。

计划成功后生成：

- `brief.md`：结合背景的取舍说明、资料要点、复习目标和步骤、来源链接；不自动用浏览器打开。
- `evidence.md`：将每项模型资料结论与它引用的提取段落并列呈现；由人核对语义支持，不冒充自动事实核查。
- `sources.txt` / `sources.json`：正文和 `S1:P1` 等编号，供核对引用。
- `bundle.json`：此次确定的上下文、来源、模型计划和内容哈希。
- `result.json`：阶段日志。此时为 `PLANNED_NOT_EXECUTED`，不是手机任务完成。

模型只选择时段 ID，不创造日期、shell、URI 或执行权限。学习步骤是建议；引用存在不代表语义正确。信息缺失/资料不相关时可返回 `CLARIFY`，无日程目标；非法字段/引用/截断响应则停止，保存脱敏诊断，不自动修复或付费重试。

## 确认后写入手机

计划满意后，运行打印的 `web apply --bundle '路径'`，可加 `--serial AYYKVB1809001850 --calendar-id 1`。

`apply` 不访问网页或模型。重新校验计划包和时段，读取手机实时资格，展示完整简报、日历 ID、标题和起止时间。输入 `yes` 后 5 秒准备，手机主屏持续打字，不发送短信、不打开日历。逐条精确回读并记录事件 ID；最后记录键盘与额外声音/弹窗/卡顿观察。

**日历中只写标题和起止时间，详细简报留在电脑。** 日程备注仍是冻结执行器的防重标记；不把未经验收的长文本/链接塞进 Provider。无提醒、邀请或手机通知，可能随日历账号同步。没有读取既有日程正文或忙闲时间，不能声称自动避免冲突，请自行确认时段。

本机默认复用 `wellphone-router/outputs` 的资格、单设备锁和 `router-journal.sqlite3`，避免换工作树丢失重试保护。不复制、清空或覆盖旧记录，仅在确认执行后追加操作；资格仍绑定设备、系统版本、日历 ID 和原执行实现哈希。新机器可设置 `WELLPHONE_STATE_ROOT` 指向自己的已验收路由工作树；没有兄弟路由目录时使用当前根目录，先运行 `python3 run.py router calendar-test` 完成资格验收。

多日程部分完成时，已写事件保留，不自动删除/回滚。超时只回查、不重发；重启后同一目标复用完全匹配的事件，已修改/删除或结果不确定则停止。**不要删除 journal，也不要修改标题或重新生成计划来绕过待核查的写入。** 计划包哈希用于发现内容变化，不是授权或数字签名；每次执行仍须人工确认。

## 网络与数据边界

- 默认直连：HTTPS/443、无账号密码/查询参数；DNS 全部地址须为公网，连接固定到已检查 IP，校验目标域名证书。不带 Cookie，不读浏览器登录态，不发现外链，不接受模型 URL。
- 当前 Mac 的 DNS 返回 `198.18.0.x` / 私有 IPv6，系统代理为 `127.0.0.1:7897`。显式设置 `WELLPHONE_WEB_PROXY` 后，仅两个默认官方域名可经本机 HTTP CONNECT 代理，保留目标 TLS 校验；目标 DNS 交由该可信本机代理，**不声称此模式仍校验最终目标 IP**。不改系统设置，不把其他域名交给代理；代理模式下换资料域名会停止。直连网络可 `unset WELLPHONE_WEB_PROXY`。
- 仅 HTML/纯文本，每页最多 1.5 MB、正文最多 24,000 字符；截断标记保留。最多 3 次同域重定向；跨域、登录/拒绝访问、PDF、动态页面无正文等停止。不绕过权限或执行网页命令。不执行 JS 也不能完美识别所有登录壳页面，内容相关性仍需核对。
- 本地输出权限随入口 `umask 077`，不提交 outputs。诊断脱敏不是通用隐私清除；SDK 失败仅显示异常类型，不保存 HTTP 请求头、密钥或模型思考。

## 验证记录

2026-09-09 首版：190 项离线测试通过（原157项＋新增33项），覆盖地址/DNS/代理/TLS、提取、schema、引用/时段绑定、计划包、无设备规划、取消/异常/中断/部分成功和跨运行防重。使用假网页、假 SDK 和内存 Provider，**不是真实模型或手机验收**。93 个冻结文件哈希通过。

默认两篇官网已实际通过本机代理读取：输入路由 75 段 / 4,816 字符，完整，HTML SHA256 `6c19ecd5c633f70f7cfa145cb7834668ad7a3caf11b7226baddf81041d281373`；日历文档 563 段 / 23,993 字符，截断，HTML SHA256 `e42998f388459b0cca38684e102a724385388fe4334aba025e0553090b191924`。指标属于当次快照，不保证官网不变；没有调用真实规划模型或写手机。

11:48 用户完成真实模型规划：`outputs/web-plan-20260909-114812-mlbqdqol/result.json`，状态 `PLANNED_NOT_EXECUTED`，没有手机连接或日历写入。内容复核发现：HTML 软换行被切成半句引用；背景过于概括，模型容易把物理端口映射资料套成本项目机制。旧计划包和结果保留，不作为内容质量通过证据。

本轮修正：按 HTML 结构而非源码软换行分段，行内标签不拆句；长段落优先按句子/词边界切分。默认增加具体项目事实和焦点/IME文档；提示词区分项目背景、资料结论、学习建议；`evidence.md` 可逐项核对引用。新增9项测试，共199项通过；93个冻结文件不变。

新版三篇官网实际读取通过：显示支持101段/16,252字符、IME支持39段/4,769字符，均未截断；日历141段/23,974字符，截断。关键 InputDispatcher 和 single IME 描述保留完整句子。该次只读取公开网页，没有调用模型或操作手机。

尚待验收：修正后的真实模型内容质量、新任务端到端日历执行。旧 context/bundle 不会自动升级；重新用 `web init` 生成并核对新版背景/资料，保留原来希望使用的时间。主屏焦点采样覆盖手机执行阶段；CPU/发热/掉帧未定量测试。未实现通知监听、屏幕上下文采集、长期记忆或自动读取个人日历。原 GUI/日历基线继续独立保留。
