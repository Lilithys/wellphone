# 冻结基线说明（原 README，供追溯）

已验收范围：HONOR Magic3 / Android 14，用户主屏短信连续打字，AutoGLM 在同机副屏进入关于手机；87 次采样均主屏顶层焦点、主屏 IME、键盘显示，用户反馈正常。**副屏文字输入仍禁用**。设备和应用的普遍兼容性未验证。

`主屏：用户 + 原输入法` ｜ `副屏：焦点隔离 scrcpy → 原生视频 PNG → AutoGLM → display 定向动作`

## 本机运行

```sh
python3 run.py verify               # 93 个原始代码/资源文件哈希校验
python3 run.py tests                # 离线测试，不操作手机
python3 run.py baseline --preflight # 副屏设置/关于手机/返回，不调用模型
python3 run.py baseline             # 已通过的 AutoGLM + 连续打字任务
```

手机连接 USB 并授权调试；主屏打开短信草稿，按正式脚本提示持续输入、不发送。环境变量 `PHONE_AGENT_API_KEY` 可选，缺失时 Terminal 隐藏输入；`WELLPHONE_BASE_VENV` 可选，默认 `/Users/yishanma/Desktop/wellphone/scrcpyvenv`。不复制密钥、不自动装依赖。需要本机 adb、scrcpy 4.1、ffmpeg、Homebrew 动态库；依赖版本在 `baseline/python-packages.txt`。

`app/` 是桌面 AutoGLM 代码的逐文件一致副本，`work/` 是已通过的脚本及二进制副本；入口明确加载本目录 app，不加载桌面源码。运行时创建被 Git 忽略的 `app/scrcpyvenv` 链接，只复用 Python 依赖。**这不是完整环境或手机镜像；实验不得升级共享依赖或切换全局输入法。**

## 基线与实验

基线标签：`baseline-no-steal-v1`。`main` 保留基线；键盘实验在 `experiment/virtual-keyboard` 分支及独立目录 `../wellphone-keyboard-experiment` 中进行。实验失败时关闭实验副屏，直接从本目录重新运行 `python3 run.py baseline`，不需 reset、不删除实验记录、不覆盖原代码。手机若出现键盘异常，须先恢复主屏输入并重新验收；代码基线不能自动回滚手机运行状态。

成功验收摘要在 `baseline/acceptance.json`。原始运行记录、设备标识和截图只保存在本机 `private_evidence/`，被 Git 忽略；新测试在 `outputs/`，也不提交。原始桌面项目和旧工作区均保留。原始文件哈希在 `baseline/source-sha256.json`，便于确认没有误改基线。

本地 Git 仓库尚未推送 GitHub。历史细节见 `work/AUTOGLM_FOCUS.md`；其中旧路径/状态仅为冻结时记录，以本文及验收摘要为准。上游 AutoGLM 许可证见 `app/LICENSE`，scrcpy 许可证见 `work/focus_experiment/LICENSE.scrcpy`。
