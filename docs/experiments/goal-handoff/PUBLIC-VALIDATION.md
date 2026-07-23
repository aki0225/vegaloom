# Goal/Handoff 公开移植验证记录

> 日期：`2026-07-23`
>
> 基线：公开 `main`
>
> 分支：`experiment/goal-handoff-integration`
>
> 当前阶段：本地审计通过，等待公开远端 CI

## 1. 迁移范围

本次没有复制内部 Git 历史，只从公开 `main` 新建分支，并移植以下业务增量：

- Goal/Handoff Runtime；
- CLI、Goal Runtime、状态展示和单 run 修改锁接入；
- 50 个 Goal/Handoff 专项测试；
- 公开范围、边界和验证说明。

内部实验计划与状态文档没有原样公开，因为其中包含内部 PR、分支和提交身份。公开说明按
当前分支事实重新编写。

## 2. 隐私与仓库卫生

本地审计结果：

```text
敏感文件名命中 = 0
高置信凭据模式命中 = 0
未豁免绝对路径命中 = 0
UTF-8 BOM 命中 = 0
git diff --check = passed
repository hygiene worktree scan = passed
```

专项测试中有一个用于验证拒绝逻辑的合成 Windows 绝对路径。该行保留测试语义，并按公开
仓库规则增加了同一行 `allow-test-fixture` 标记；它不是本机路径。

## 3. 静态与收集验证

```text
python -m compileall src = passed
ruff check src tests = passed
pytest collected = 591
pytest unique = 591
```

CI 收集合同已从公开基线的 541 个节点更新为 591，并新增独立 Goal/Handoff Linux 分片与
Windows 专项覆盖。当前实验分支也被加入 push 触发范围，推送后必须由远端 CI 重新验证。

## 4. 本地专项验证

Goal/Handoff 50 个 node 使用完整 node id 分片，覆盖核对为 50/50：

```text
25 passed
12 passed
13 passed
```

相关回归：

```text
tests/test_run_mutation_lock.py = 10 passed
tests/test_cli_recovery_hardening.py = 37 passed
```

首次在较长的临时检出路径运行 Goal/Handoff 时出现 Windows `WinError 3`。相同代码改用
短检出路径与短 `--basetemp` 后，50 个专项 node 全部通过，因此该结果被归类为路径长度
造成的验证基础设施失败，不是 Runtime 失败。

## 5. 扩展回归状态

本地分层回归使用精确 node id、唯一 basetemp 和唯一 cache：

```text
已完成分片 = 27
已覆盖 node = 141
passed = 141
failed = 0
errors = 0
```

该长跑在未出现测试失败的情况下人工停止。原因是当前 Windows 环境中部分未变更基线测试
单个小分片仍需要两分钟以上，继续串行完成 591 个节点的本地验证成本过高。

因此不能把这 141 个 node 包装成完整回归。完整 591-node 结论必须以本分支推送后触发的
公开远端 CI 为准；远端未通过前，本分支不满足合并条件。

## 6. 当前结论

```text
privacy audit = passed
static checks = passed
targeted goal/handoff = passed
local full regression = incomplete
remote CI = pending
merge readiness = pending
```

这组证据足以允许把独立实验分支推到公开远端接受 CI，但不足以允许合入 `main`。
