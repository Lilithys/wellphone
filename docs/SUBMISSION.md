# 提交前清单

## 已包含

- 可追溯的主线源码快照、独立抖音/Web实验源码及本地交付 Git 仓库。
- 一页入口 README、部署/环境变量、架构图、演示脚本、技术取舍、失败与边界说明。
- 公开验收摘要、来源提交号、文件完整性校验、离线检查入口。
- 保留上游许可和补丁来源；不包含密钥、虚拟环境、手机私聊截图、日历状态库或完整运行日志。

最终 `dist/wellphone-delivery-v1.zip` 是不含 `.git` 的可解压源码包，内含逐文件 SHA256 清单；`dist/wellphone-delivery-v1.git.bundle` 是保留本次交付提交历史与标签的可克隆 Git 包。若只有 ZIP，可自行 `git init`；若想保留提交记录，使用 `git clone wellphone-delivery-v1.git.bundle wellphone`。两者都不含原工作区运行状态。

## 仍需用户完成

1. **GitHub 仓库链接**：本轮未创建远程仓库、未推送。新建一个你有权使用的空私有仓库，确认招聘方能访问后，在本交付目录执行：

   ```sh
   git remote add origin YOUR_GITHUB_REPOSITORY_URL
   git push -u origin main
   git push origin wellphone-delivery-v1
   ```

   若已存在 origin，先检查 `git remote -v`，不要盲目覆盖。建议先私有分享；原快照代码/历史文档保留本地路径、机型、测试昵称等背景信息，不把“未打包私聊截图”称为全面匿名化。

2. **1–2分钟真实演示视频**：按 `DEMO.md` 实拍并核对无密钥/IMEI/私聊内容。包内没有伪造演示视频，也没有用旧记录生成动画代替实机。
3. **最终提交文字**：填入真实仓库和视频地址；不要提交占位链接，不要把实验完成度写成主线通过。

## 可直接用于提交的简介

“Wellphone 在同一台安卓手机上，通过任务理解与确定性路由，将日历写入交给系统 Provider，将页面操作交给隔离虚拟显示上的 AutoGLM。已在 HONOR Magic3 / Android14 验证自然语言双通道任务与用户主屏持续打字并存。电脑仅承担模型调用与调度，目标状态真实写入/读取手机。附带抖音固定 ASCII 输入、应用级音频限制及 Web 学习计划实验，明确列出未验收发送、Unicode输入和通用性边界。代码包含部署说明、可追溯基线、失败复盘和离线测试。”

## 运行状态不在公开包里

原 `wellphone-router/outputs`、`wellphone-douyin-experiment/outputs`、`wellphone-web-context/outputs` 均未改动或删除。日历资格/防重日志、未发送草稿与未知写入结果仍要在原设备核对。不要为让演示“从零开始”删除这些状态，或在不同副本同时控制同一手机。
