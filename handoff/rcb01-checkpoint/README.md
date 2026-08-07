# RCB-01 跨机器检查点

本目录只用于恢复 RCB-01 实验控制面，不是待合并功能，也不改变 Vega 主线。

## 当前检查点

- 冻结代码：`4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105`
- 已消费：`01-C1-A1` 至 `12-C5-A2`
- 下一项：`13-C4-A2`
- 下一次确认：`RCB-01-RUN-13`
- Runner、Freeze、模型、推理强度、顺序和评分规则均保持冻结

## 两层材料

### 公开控制面

Git 中只保存：

- 冻结 Runner、Freeze 和五个 Case 的 verification 结果；
- 已消费序号与下一序号；
- 私有 Artifact 归档的文件名、大小和 SHA-256；
- 恢复脚本。

Git 中不保存 Reviewer 输出、JSONL、Token、finding 或本机绝对路径。

### 私有证据面

私有归档 `RCB-01-through-12-C5-A2.zip` 保存 Run 01～12 的原始 Artifact
（不包含可重新物化的 `candidate-worktree`），并附带逐文件 SHA-256 清单。
归档必须通过私有介质或加密存储传递，不能上传到公开仓库。

在源机器上可用以下命令重新生成后续检查点：

```powershell
<python-path> <handoff-worktree>\handoff\rcb01-checkpoint\private_checkpoint.py `
  export `
  --repo-root <repo-root> `
  --resume-state <handoff-worktree>\handoff\rcb01-checkpoint\state\resume-state.json `
  --runner <repo-root>\.local-validation\rcb-01\run_reviewer_experiment.py `
  --freeze <repo-root>\.local-validation\rcb-01\experiment-freeze.json `
  --output <repo-root>\.local-validation\rcb-01\checkpoints\RCB-01-through-N.zip
```

## 恢复步骤

目标冻结 worktree 的叶目录必须精确为 `vegaloom`，且必须是干净的 detached
worktree：

```powershell
git fetch origin
git worktree add --detach <frozen-parent>\vegaloom `
  4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105
```

只恢复控制面（原始 Artifact 留在另一台机器）：

```powershell
pwsh -File <handoff-worktree>\handoff\rcb01-checkpoint\restore.ps1 `
  -RepoRoot <frozen-parent>\vegaloom `
  -Python <python-path>
```

恢复控制面和私有 Artifact：

```powershell
pwsh -File <handoff-worktree>\handoff\rcb01-checkpoint\restore.ps1 `
  -RepoRoot <frozen-parent>\vegaloom `
  -Python <python-path> `
  -PrivateCheckpoint <private-checkpoint>
```

脚本会校验代码提交、Runner、Freeze、归档哈希、序号连续性和 Artifact
清单；任何不一致都会停止，不会覆盖已有现场，也不会补跑已消费序号。
恢复后必须看到：

```text
next_run = 13-C4-A2
run_count_existing = 12
```

恢复完成后，再按冻结确认值执行下一项。实验结束前不要删除源机器上的原始
Artifact；最终评分需要保留全部 01～20 的证据。
