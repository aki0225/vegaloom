# Vega v0.3.1 发布候选说明

> 状态：发布候选，待合入 `main`、创建注解 Tag 和 GitHub Release。

v0.3.1 是面向跨目录接手与恢复可靠性的维护版本。它不增加新的公共 Agent
入口或成功路径，重点修复真实验收暴露的 Task Card、Workspace 摘要和重复恢复边界。

## 主要变化

### 可移植的 Handoff 摘要

- Task Card 内容摘要在计算前统一规范化为 LF，避免同一份卡片因 LF/CRLF checkout
  产生不同摘要；
- 新的 `git-blob-v1` Workspace 摘要同时绑定 Git mode（`100644`、`100755`、
  `120000`）和 Blob 身份；
- 恢复时按 Task Card 声明的摘要类型重新核对 Git 内容。路径、文件类型、权限或内容
  发生变化时仍然 fail-closed。

### 恢复现场

- 重复声明同一 Task Card 时，拒绝第二次恢复；
- 如果拒绝发生在新 run 尚未写入任何证据前，只清理这个空目录；
- 已经产生 Artifact 的失败 run 保持原样，便于排查；
- Task Card 的可读摘要显示实际使用的 Workspace 摘要类型。

### VALID-02 证据

`VALID-02` 记录了 V030-REAL-01 暴露的恢复和高风险审查问题修复。现有回归覆盖：

- LF 与 CRLF checkout 下的 Task Card 和 Workspace 恢复；
- 重复 Resume Claim；
- Git mode 绑定；
- 内容漂移、路径越界和损坏现场的 fail-closed 行为；
- 必审风险仍运行隔离 Reviewer，并保持 `needs_human`。

提交前本机完整验证快照为 `1456 passed, 2 skipped`；发布候选仍需通过本次 PR
的完整 CI、Package smoke 和授权的真实 Agent smoke，不能把候选分支证据写成已发布事实。

## 不变边界

- Worker 与 Reviewer 仍使用独立会话，Reviewer 不接收 Worker 的完整聊天或推理；
- Verification、Risk、Reviewer 和 Finish 的成功语义没有放宽；
- 不自动 push、merge、release、删除用户文件或写入长期 Memory；
- 跨机器恢复依赖 Git 可跟踪的任务分支、Task Card 和候选代码；Provider 会话 ID
  只用于原环境续接。

## 兼容性

旧版 Task Card 没有 `workspace_digest_kind` 时继续按旧的
`workspace-bytes-v1` 语义读取。新生成的 Task Card 使用 `git-blob-v1`，可在
不同 checkout 行尾策略下核对同一 Git 内容。
