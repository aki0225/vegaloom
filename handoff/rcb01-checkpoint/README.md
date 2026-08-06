# RCB-01 跨机器接力包

本目录只用于在另一台机器恢复实验控制面，不是待合并功能。

包含：

- 冻结 Runner；
- 实验 Freeze 与模型可用性基线；
- 五个 Case 的冻结 verification 结果；
- 已消费序号与下一运行序号；
- fail-closed 恢复脚本。

不包含：

- Reviewer 最终输出；
- JSONL 会话记录；
- Token、finding 或中间评分；
- 本机绝对路径和 Provider 敏感元数据。

这样处理是为了避免后续 Reviewer 从 Git 分支读取先前结论。办公室的原始 Artifact 必须继续保留，
最终评分时再与另一台机器产生的后续 Artifact 合并；不得重跑已消费序号。

恢复时，目标目录名必须精确为 `vegaloom`，且仓库必须是冻结提交 `4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105` 的干净 detached worktree。
建议先建立独立 Python 3.12 环境并安装 `.[dev]`，然后执行：

```powershell
pwsh -File <handoff-worktree>\handoff\rcb01-checkpoint\restore.ps1 `
  -RepoRoot <frozen-run-worktree> `
  -Python <python-path>
```

恢复脚本会重新物化 C1 至 C5，创建 01 至 09 的已消费占位目录，并运行完整 preflight。
如果 Codex CLI 版本、模型路由、Runner、输入或 Freeze 任一项不一致，脚本或 Runner 会停止。
不要通过编辑 JSON 绕过检查。

该恢复流程已在新的 egaloom detached worktree 中完整验证，preflight 返回下一项 10-C5-B1。
