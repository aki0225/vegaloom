# RCB-01 办公室接力记录（2026-08-06）

> 分支用途：跨机器接力，不合并到 `main`。
>
> 冻结代码：`4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105`
>
> 下一运行：`10-C5-B1`

## 当前状态

- `01-C1-A1` 至 `09-C5-A1` 已消费，禁止重跑。
- 中断发生在 `10-C5-B1` 的模型调用之前；远端接力应从序号 10 开始。
- 实验 Runner、Prompt、案例、模型、推理强度、预算、顺序和评分规则已经 Freeze，禁止修改。
- 办公室原始 Artifact 保留在本机忽略目录，没有进入 Git。
- 本分支不包含任何 Reviewer 结论、中间 finding、Token 或会话 JSONL，避免污染后续独立审查。

## 为什么不直接提交整个本地目录

RCB-01 预注册要求完整 Prompt、会话记录、本机路径和 Provider 原始元数据只保留在本地。
同时，若把前九次 Reviewer 输出提交到同一仓库的其他分支，后续只读 Reviewer 仍可能通过 Git ref
读取先前结论，破坏独立样本。因此本次只传递恢复控制面所需的确定性输入。

## 另一台机器的恢复步骤

1. 获取本接力分支，但不要在该分支上运行实验。
2. 从冻结提交建立新的 detached worktree：

```powershell
git fetch origin
git worktree add --detach <frozen-parent>/vegaloom 4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105
git worktree add --detach <handoff-worktree> origin/handoff/rcb01-20260806
```

3. 冻结运行 worktree 的目录名必须精确为 `vegaloom`，否则项目画像字节会漂移。随后在该 worktree 中建立 Python 3.12 环境：

```powershell
python -m venv .local-validation/rcb-01/venv
.local-validation/rcb-01/venv/Scripts/python.exe -m pip install -e ".[dev]"
```

4. 执行恢复脚本：

```powershell
pwsh -File `
  <handoff-worktree>/handoff/rcb01-checkpoint/restore.ps1 `
  -RepoRoot <frozen-parent>/vegaloom `
  -Python <frozen-parent>/vegaloom/.local-validation/rcb-01/venv/Scripts/python.exe
```

5. 确认 preflight 显示 `next_run = 10-C5-B1`。然后才能执行：

```powershell
.local-validation/rcb-01/venv/Scripts/python.exe `
  .local-validation/rcb-01/run_reviewer_experiment.py `
  run --sequence 10 --confirm RCB-01-RUN-10
```

## 恢复脚本验证

已在新的 detached worktree 中完整执行恢复：五案重新物化成功，Freeze 校验通过，preflight 明确返回
`next_run = 10-C5-B1`、`run_count_existing = 9`。测试 worktree 已删除，没有调用真实模型。
## 必须保持的边界

- 不重新运行 01 至 09。
- 不修改 `experiment-freeze.json` 或 Runner 来迁就环境差异。
- Codex CLI 版本、`gpt-5.6-sol/high`、`read-only`、`ephemeral` 和 900 秒上限必须保持一致。
- 家中环境若无法通过模型、Provider 或 preflight 检查，停止并保留现场，不换模型补样本。
- 最终评分必须同时使用办公室保存的 01 至 09 原始 Artifact 和后续机器的 10 至 20 Artifact。
- 本接力分支只用于传递恢复材料，实验完成后删除，不创建 PR、不合并。