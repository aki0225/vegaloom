# PROVIDER-01：Claude Code Provider 真实 Smoke

日期：2026-09-03

## 场景

在一次临时 Git 仓库中，用一个已批准的 Change Contract 修复单文件函数实现。Vega
通过 Claude Code 完成以下链路：

1. 绑定 Worker Session；
2. 在受管 Worktree 中修改文件；
3. 冻结 Git Candidate；
4. 运行项目声明的 pytest 验证；
5. 使用独立、只读工具面 Reviewer 检查 Candidate；
6. 生成 Core Finish。

验证目标只允许修改 `calculator.py`；测试和 `.vega.yaml` 位于禁止范围。

## 运行方式

以下命令在临时目录执行，路径用占位符表示：

```powershell
vega start --repo <fixture-repo> `
  --contract <change-contract.json> `
  --execution-plan <execution-plan.json>
vega approve --run <run-id> --actor provider-01-smoke
vega run --run <run-id> --provider claude --timeout 600
```

## 结果

- Worker：`success`
- Candidate：单文件、1 行替换
- Verification：`python -m pytest -q -p no:cacheprovider tests/test_calculator.py`，退出码 0
- Scope Gate：前置和后置均 `success`
- Risk Gate：`passed`，低风险
- 独立 Reviewer：`approve`
- Finish：`ready_to_commit`
- Worker 与 Reviewer：不同 Claude Code Session，权限和工具面均通过初始化校验
- 当前用户分支：未修改；Candidate 只存在于 Vega 管理的 Worktree

同日另做了一次两回合 Session 探针：首回合使用新 Session，第二回合通过 `--resume`
继续同一 Session；两个回合均返回结构化 `success`。公开记录不保存 Session ID。

## 证据边界

这些运行证明 Claude Code 可以接入现有 ChangeRun、Candidate、Verification、Risk、
Reviewer 和 Finish 链路，并能恢复既有 Session；它们不证明任何生产代码安全，也不代表
自动 push、merge 或发布已经发生。运行时的完整日志、Session ID 和临时仓库没有进入公开仓库。
