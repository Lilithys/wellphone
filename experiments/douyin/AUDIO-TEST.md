# 抖音音频专项：先验证限制与恢复，再验证真实播放

只修改包 `com.ss.android.ugc.aweme` 的 `PLAY_AUDIO` / `TAKE_AUDIO_FOCUS`，不修改 UID 级规则、不改全局音量、不录音、不转发音频、不调用模型、不发消息。影响测试期间**整个抖音应用**，并非只影响副屏。现有设置/日历基线保持不变。

已完成的真机权限回环：`outputs/audio-cycle-20260908-221015-0yuazflt/result.json`。两项从原来的 `allow` 临时设为 `ignore`、回读成功，再恢复并独立回读确认均为原值。没有启动抖音。这不代表实际音频隔离已通过。

2026-09-08 22:21 已进一步完成**实际播放专项验收**：`outputs/douyin-audio-20260908-222101-uxukv_l6/result.json`。41 次主屏采样无异常，13 次音轨状态采样覆盖 started 播放；用户确认连续视频无抖音声音、背景音乐及键盘正常，副屏关闭、权限恢复。记录含 `muted:appOps` 音轨，也含 `muted:none` 音轨，不能声称系统证明了每条音轨都静音；结论结合本轮人工听感，限定当前 HONOR Magic3 / Android 14 / 抖音环境。

## 现在运行

先手动播放一段背景音乐，再打开主屏短信草稿并保持键盘。不要在主屏使用抖音。

```sh
python3 /Users/yishanma/Documents/Codex/wellphone-douyin-experiment/run.py audio
```

输入 `yes` 后 5 秒准备，随后持续在手机打字。脚本先限制两项权限，再创建副屏打开抖音，观察 15 秒；不做任何点击、滑动或返回。完成后关闭副屏，确认抖音播放器不再处于 started 状态，再恢复权限并回读。

最后记录：副屏是否确实播放视频；是否听到抖音声音；背景音乐是否被暂停/压低；键盘是否正常。若停在静止消息页/暂停状态，或没有采到抖音 started 音轨，判为播放未充分验证，不把安静当作静音成功。背景音乐未测试则不能认定音频焦点连续性通过。

专项验收后，`douyin` 及其 `--preflight` 入口已接入同一音频事务及恢复逻辑。每轮启动前须重新限制和回读，不把上次成功当作本轮已生效；完整发送流程尚待真机验收。专项入口仍保留，且仍不调用模型、不操作消息。

## 恢复入口

```sh
python3 /Users/yishanma/Documents/Codex/wellphone-douyin-experiment/run.py audio --restore --serial AYYKVB1809001850
```

正常完成、异常、Ctrl+C 都尝试恢复。USB 断开、进程被强杀、系统状态未知或抖音仍在播放时，不能保证立即恢复；持久记录保存在 `outputs/audio-lease-*.json`。先重新连接原手机、手动关闭抖音使其不再播放，再运行恢复入口。脚本不强制停止应用、不删除数据，也不使用 `appops reset`。未完成恢复会阻止新的抖音测试。

恢复限定原设备、Android 用户、系统版本、应用 UID、这两个操作及原模式。发现第三方修改则不覆盖；保存原配置值，不尝试抹掉系统操作历史。“No operations / Default mode: allow” 恢复为实际 `allow`，不是字面 `default`。

## 其他检查

```sh
python3 run.py audio --cycle   # 只做权限设置/恢复，仍须明确确认；不启动抖音
python3 run.py verify         # 93 个冻结文件
python3 run.py tests          # 离线回归，不访问手机或模型
```

主屏键盘采样、抖音自身播放器状态、权限事务及人工观察写入本地输出。截图可能含个人内容，不提交 `outputs`；不保存原始完整音频服务 dump。

2026-09-09：所有音频入口（包括 `--restore`）与 `douyin` 使用同一共享设备锁，默认协调兄弟 `wellphone-router` 和 Web；同时保留本实验旧锁兼容。`WELLPHONE_STATE_ROOT` 如已设置，应与 Web 使用同一路径。恢复记录仍在原目录，不因共享锁迁移；其他任务占锁时先结束该任务再恢复，不抢占。不要同时运行不遵守该锁的旧 baseline 或其他 adb 控制程序。
