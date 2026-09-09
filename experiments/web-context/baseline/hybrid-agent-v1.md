# 已验收基线：baseline-hybrid-agent-v1

- 冻结代码：`3417742a35472b9f66d4926821ac5a2583417d7c`；Git 附注标签 `baseline-hybrid-agent-v1` 精确指向该提交，不包含后续扩展。
- 验收时间：2026-09-08 19:53–19:55，HONOR Magic3 / Android 14。
- 链路：DeepSeek `deepseek-v4-flash` 理解目标 → 规则与设备状态路由 → Calendar Provider 写入 → AutoGLM `autoglm-phone` 操作同机虚拟显示 → 回读和隔离验证。
- 日历：事件30真实新建，字段回读通过，13次主屏焦点/键盘采样正常，人工观察正常。
- GUI：4步到达关于手机页面，91次主屏采样通过，人工观察正常；本轮副屏48已清理。
- 总状态 `COMPLETED`。代码有157项离线测试；主屏采样是离散观测，不构成所有设备和所有场景的绝对零干扰证明。

## 原始证据（仅本地保留，不进 Git）

- `outputs/router-20260908-195319-j09rmkyh/result.json`
  - SHA256 `8e5c49ef21ecd82cc22f263b05376fbcef0f33f09f7c8e6090eb419354759992`
- `outputs/router-gui-20260908-195359-ph2szmg6/result.json`
  - SHA256 `a0fa75a696dc82f815da35f3f99501a8208756d7c42217f6240d78a606448cfa`
- 源码快照：`outputs/checkpoints/wellphone-hybrid-agent-v1.tar.gz`
  - SHA256 `f6c5d714e4808396832a6b0b4968d0c6c03b5338c1bc9d8263b19c582f8464dd`

快照由 Git 标签归档，仅包含该提交跟踪的文件，不包含 API key、虚拟环境、日志、手机数据或 SQLite 操作状态。快照内的旧 README 保留了提交当时的历史状态；本页补充之后完成的真实验收证据。没有上传 GitHub。

## 使用边界与回退

只验收单次无提醒日历写入，以及固定“设置→关于手机”GUI 任务。通用 Type、中文 GUI 输入、其他 App、通知监听、持续个人上下文索引等均不属于本基线。

恢复时优先在新 worktree 检出本标签，不用 `reset --hard` 覆盖当前工作。新目录仍需配置既有 Python 环境、密钥与已授权设备，并重新做设备专项资格验收。

原工作区的 `outputs/router-journal.sqlite3` 和 `outputs/qualifications/` 仍保留；前者有防重复写入作用，不要为重试而删除。源码快照不等于运行状态备份，回退前应核对已经创建的日程。原 `baseline-no-steal-v1` / `checkpoint-ascii-v1` 标签及对应工作区不变。
