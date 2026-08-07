# RCB-01 跨机器接力记录（更新至 2026-08-07）

> 分支用途：跨机器接力，不合并到 `main`。
>
> 冻结代码：`4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105`
>
> 当前已消费：`01-C1-A1` 至 `11-C5-B2`
>
> 下一运行：`12-C5-A2`

## 当前状态

- Run 01 至 Run 11 均已登记，禁止重跑。
- Run 11 在冻结的 `gpt-5.6-sol/high`、只读、临时会话和 900 秒上限下正常结束。
- Runner、Prompt、案例、模型、推理强度、预算、顺序和评分规则继续保持 Freeze。
- 主工作区的原始 Artifact 仍保留在忽略目录，没有进入 Git。
- 本分支不包含 Reviewer 结论、中间 finding、Token 或会话 JSONL。
- 私有归档 `RCB-01-through-11-C5-B2.zip` 保存 Run 01～11 的原始证据，
  不包含可重新物化的 `candidate-worktree`；归档的 SHA-256 已写入
  `handoff/rcb01-checkpoint/state/resume-state.json`。

## 为什么分成两层

公开 Git 只传递恢复控制面：冻结输入、序号、下一运行和哈希。
Reviewer 输出和 Provider 原始元数据可能包含本机路径或中间结论，不能放进公开
分支，也不能让后续独立 Reviewer 通过 Git ref 读取。

完整 Artifact 通过私有介质或加密存储传递。没有私有归档时仍可恢复控制面并继续
实验，但最终评分必须找回所有机器上的原始 Artifact，不能把缺失证据当成成功。

## 另一台机器的恢复步骤

1. 获取本接力分支，但不要在该分支上运行实验。
2. 从冻结提交建立目录名精确为 `vegaloom` 的干净 detached worktree：

```powershell
git fetch origin
git worktree add --detach <frozen-parent>\vegaloom `
  4e195df3f27a9ce8037d9ba6ccbd173fdd8c0105
git worktree add --detach <handoff-worktree> origin/handoff/rcb01-20260806
```

3. 在冻结 worktree 中建立 Python 3.12 环境并安装开发依赖：

```powershell
python -m venv .local-validation/rcb-01/venv
.local-validation/rcb-01/venv/Scripts/python.exe -m pip install -e ".[dev]"
```

4. 如果需要保留历史原始证据，把私有归档放到一个不受 Git 跟踪的位置，然后执行：

```powershell
pwsh -File `
  <handoff-worktree>\handoff\rcb01-checkpoint\restore.ps1 `
  -RepoRoot <frozen-parent>\vegaloom `
  -Python <frozen-parent>\vegaloom\.local-validation\rcb-01\venv\Scripts\python.exe `
  -PrivateCheckpoint <private-checkpoint>
```

   如果只需要继续运行而原始证据仍在另一台机器，可省略 `-PrivateCheckpoint`。

5. 恢复脚本完成后，必须确认：

```text
next_run = 12-C5-A2
run_count_existing = 11
```

6. 只在预检通过后执行下一项：

```powershell
.local-validation\rcb-01\venv\Scripts\python.exe `
  .local-validation\rcb-01\run_reviewer_experiment.py `
  run --sequence 12 --confirm RCB-01-RUN-12
```

## 2026-08-07 恢复验证

已从冻结提交建立新的 detached worktree，并使用私有归档完整执行恢复：

- 五个 Case 均重新物化成功；
- 146 个原始证据文件通过逐文件 SHA-256 校验；
- Runner 和 Freeze 哈希保持不变；
- preflight 返回 `run_count_existing = 10`、`next_run = 11-C5-B2`；
- 全程没有调用真实模型；
- 人为篡改归档后，恢复工具在写入目标目录前因归档 SHA-256 不一致而拒绝执行。

Run 11 完成后又执行了一次私有归档往返校验，161 个证据文件全部通过清单
与 SHA-256 检查，恢复出 11 个连续运行目录，下一项仍为 `12-C5-A2`。

## 恢复约束

- 归档哈希、Runner、Freeze、代码提交或序号不一致时立即停止。
- 不修改 `experiment-freeze.json` 或 Runner 来迁就环境差异。
- 不重跑已经消费的序号；Provider 失败也属于已消费结果。
- 不把私有 Artifact 复制到公开 Git 分支。
- 实验完成前保留源机器和私有归档；最终评分需要 Run 01～20 的完整证据。
- 实验完成后删除临时接力分支，不创建 PR、不合并主线。
