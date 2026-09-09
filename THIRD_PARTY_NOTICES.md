# 来源、修改与许可

- `core/app/` 继承 Open-AutoGLM / Phone Agent 源码及随附资源，保留 `core/app/LICENSE`（Apache-2.0）、原 README、示例及资源说明。Wellphone 的副屏适配和约束相较上游有修改；本包不是上游官方发行版。
- scrcpy 项目来源：<https://github.com/Genymobile/scrcpy>，基于 v4.1。许可证保留在各快照 `work/focus_experiment/LICENSE.scrcpy`。Wellphone 的显示焦点、非展示屏及原生视频流补丁、构建方法和校验值随源文件一同交付。
- 本地 `scrcpy-live` 动态链接用户已安装的 FFmpeg 与 SDL3 等库，本包没有复制这些系统动态库或 JDK/Android SDK；其许可与可再分发条件由各自发行版本决定。
- Python 第三方包不随包复制；记录了验收环境版本，使用时由用户在自己的虚拟环境准备。模型服务需用户自有账号/密钥，调用费用和数据处理取决于所选服务。
- 各子目录历史说明保留原版权和来源。新增 Wellphone 交付脚本与文档按 Apache-2.0 提供，许可正文采用 `core/app/LICENSE`。保留这些说明不表示已替第三方授权其商标/品牌，也不保证未验收平台兼容。

精确源码快照与来源提交在 `evidence/provenance.json`。不是 clone 某个工作区的未跟踪内容：源代码从明确 Git 提交归档，运行数据不入包。
