# Vega v0.4.0 发布说明

> 状态：已发布。注解 Tag `v0.4.0` 绑定提交
> `bcc079e0fe4fce99bb20637fa09b021537f27abe`，GitHub Release 同时提供 wheel 和 sdist。

v0.4.0 把自然语言任务接到现有 ChangeRun。用户只描述 Bug 现象或功能目标时，Vega 可以先让
Coding Agent 在只读 Workspace 中调查，再把结果编译为待批准的 Change Contract 和
Execution Plan。批准后仍沿用同一条 Worker、Git Candidate、项目验证、风险门禁、独立
Reviewer 和最终报告链路。

## 主要变化

### 从自然语言目标开始

```powershell
vega start --repo . --text "商品数量大于 1 时总价偏低"
vega run --run <run_id> --timeout 900
```

Planner 输出带来源引用的事实、假设、未决问题、建议范围和 Work Item。Planning 阶段保持
只读，不创建 Candidate，也不启动 Worker。

### 确定性编译任务合同

Contract Compiler 对照固定 source revision、`.vega.yaml` 和仓库规则检查：

- 目标、验收、不变量和非目标；
- 允许路径与修改预算；
- 已登记的验证命令；
- 数据库、资金、权限、并发、部署和外部写入等风险；
- Work Item、Repair 和 Replan 预算。

模型自由生成的命令不会直接进入自动执行。引用失效、范围越界、验证来源不明或风险声明缺失时，
ChangeRun 停在 `needs_human`。

### 可选的低风险自动批准

仓库可以在 `.vega.yaml` 中预先登记 bounded 策略。只有调用方显式选择 `--approval bounded`，
并且 Contract 同时满足路径、验证、风险、副作用和预算限制时，才跳过初始人工批准。

策略不匹配不会降级门禁，也不会改变最终 Git 交付边界。

### 同一 Worker 会话自动返修

Planner 与 Worker 默认复用同一个 Codex Provider Thread。Reviewer 返回具体 Finding 后，
Supervisor 生成 Fix Packet，并在合同和预算内继续同一个 Worker Thread。Reviewer 仍使用独立
只读 Thread，不继承 Worker 的完整聊天或中间推理。

### 恢复与现场处理

- Provider 原生上下文压缩后，下一 Turn 重新注入 Task Anchor；
- Worker 中断时先对账进程、Workspace 和 partial diff；
- 无法证明现场安全时停止，不启动第二个 Writer；
- Task Card 可以随任务分支跨目录或跨机器传递；
- 恢复后重新检查当前 Candidate，历史 Gate 不作为当前通过证据。

## 真实验收

AUTONOMY-05 使用三个可丢弃仓库和一个中断夹具完成：

1. 低风险任务按 bounded 策略自动批准并达到 `ready_to_commit`；
2. Human 模式完成调查、批准、Reviewer 打回和同 Worker Thread 自动返修；
3. 数据库迁移与权限任务拒绝自动批准，人工批准后仍由风险门禁保持 `needs_human`；
4. Codex 原生压缩后继续绑定正确 run、Contract、Plan、Work Item 和 Checkpoint；
5. Worker 中断保留 partial diff，没有启动第二个 Writer 或自动重放；
6. Task Card 在另一个 checkout 恢复任务，但不复用旧验证结论。

完整记录见：

- [`../eval/autonomy-05-real-agent.md`](../eval/autonomy-05-real-agent.md)
- [`../eval/real-world-runs.md`](../eval/real-world-runs.md)

## 边界

- v0.4.0 的自动 Provider Adapter 只完成 Codex 验收；
- Claude Code 尚未接入同等级真实执行链；
- 未知数据库写入、支付、部署和外部 API 副作用不会自动重放；
- Vega 不操作用户当前分支，也不自动 push、创建或合并 PR、发布或部署；
- `ready_to_commit` 仍表示候选可以交给人检查，不表示已经证明生产安全。

## 升级

```powershell
python -m pip install .\vegaloom-0.4.0-py3-none-any.whl
vega --version
vega capabilities
```

Wheel 和 sdist 随 GitHub Release 提供；当前没有声明 PyPI 发布。

稳定 Python 导出仍只有 `vega.__version__`。内部模块不承诺跨版本 SDK 兼容。
