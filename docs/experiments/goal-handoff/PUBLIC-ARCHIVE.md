# Goal/Handoff 公开集成说明

> 审计日期：`2026-07-23`
>
> 分支：`experiment/goal-handoff-integration`
>
> 状态：`experimental / integration candidate`
>
> 默认产品路径：公开 `main` 仍使用 Goal P0，不自动启用跨会话 Handoff

## 1. 公开目标

本分支验证一项与 LangGraph 无关的能力：

> 已完成 checkpoint 的 Goal，能否生成可校验、不可覆盖的 handoff，并让新的会话在重新读取
> 当前工作区、项目策略和权威 artifact 后，编译出受预算约束的接力上下文。

它不是聊天记录导出，也不把 handoff 当作新的业务事实源。业务状态、checkpoint evidence、
Git workspace 和项目策略仍分别由各自的权威载体拥有。

## 2. 本分支增加的能力

- `vega goal handoff`：为已完成 checkpoint 创建版本化 handoff。
- `vega goal handoff-context`：为指定 consumer session 编译接力上下文。
- 创建和消费阶段都重新绑定 Goal contract、checkpoint evidence、workspace fingerprint、
  项目策略以及声明的权威 artifact。
- handoff 与 consumer package 先写入 staging 目录，校验完整文件集后再发布。
- 发布成功但业务状态尚未登记时，可以在输入身份完全一致的前提下收养 orphan package。
- Goal mutator 使用单 run 修改锁，阻止 Vega 内部并发写者同时修改同一 Goal。
- 对文件、父目录和最终发布目录执行 symlink、junction、reparse point、hardlink 与越界检查。
- consumer 发布前再次捕获 workspace、policy 和 artifact，缩小检查与使用之间的漂移窗口。
- context 超出预算时返回 `split_required`，不静默截断，也不自动创建下一 checkpoint。

## 3. 状态与证据职责

```text
state.json / goal-state.json
  当前 Goal run 的业务状态

checkpoint-evidence.json
  checkpoint record 的持久化证据

checkpoint-handoff.json
  跨会话接力输入，不拥有业务成功语义

handoff-compile-result.json
  consumer 编译结果及输入、输出哈希索引

workspace / policy snapshot
  创建与消费时重新捕获的代码和项目规则绑定
```

Handoff 只能引用并解释权威事实，不能覆盖失败的验证、漂移的 workspace 或已经变化的项目策略。

## 4. 明确边界

本分支不声明：

- 文件系统写入、Goal state 和外部 Git workspace 已经成为一个原子事务；
- 本地 run mutation lock 能替代分布式锁或跨主机 lease；
- session id、worker epoch 或 artifact 写权限构成密码学身份认证；
- 对拥有持续本机写权限的恶意进程实现了完整隔离；
- LangGraph 对 Goal/Handoff 提供了额外收益；
- 已完成真实跨机器长任务、Provider 消费质量或生产部署验证；
- 会自动启动 worker、自动 commit、push、release 或写入长期 Memory。

本分支固定保持：

```text
memory_mode = off
source_chat_included = false
automatic_checkpoint_creation = false
automatic_worker_start = false
```

## 5. 公开移植与隐私边界

公开分支从公开 `main` 建立，不携带内部实验仓的提交历史。移植时只保留：

- Goal/Handoff Runtime；
- CLI、Goal Runtime、状态展示和修改锁的必要接入；
- 专项测试；
- 本公开说明。

内部 PR 编号、内部提交身份、旧分支接力记录、Provider transcript、本地 run artifacts、
绝对工作区路径和私人邮箱不进入本分支。安全测试中的受限路径是合成 fixture，并使用仓库
卫生规则要求的同一行显式标记。

## 6. 验证合同

基础检查：

```powershell
python -m compileall src
ruff check src tests
python scripts/check_repository_hygiene.py --base-ref origin/main
git diff --check
```

当前分支完整收集合同：

```text
collected = 591
unique = 591
```

专项测试：

```powershell
python -m pytest -q tests/test_goal_handoff.py
python -m pytest -q tests/test_run_mutation_lock.py
python -m pytest -q tests/test_cli_recovery_hardening.py
```

完整验证超过单命令 60 秒时，必须按测试文件或完整 node id 分片，并为每个分片配置独立的
`.tmp/pytest/runs/` 与 `.tmp/pytest/cache/`。Windows 验证应使用短检出路径，避免路径长度
限制被误判为 Runtime 失败。

只有明确的 collection、passed、skipped 和 failed 数量可以作为公开证据；timeout 不能计为
通过。

本次公开移植的本地验证记录见
[`PUBLIC-VALIDATION.md`](PUBLIC-VALIDATION.md)。

## 7. 接受条件

本分支可以推送公开远端，但不自动合入 `main`。进入后续合并评估至少需要：

1. 当前树与 `origin/main..HEAD` 可达历史均通过仓库卫生检查；
2. 专项测试和公开基线回归通过；
3. 未引入 LangGraph 或 Provider 依赖；
4. Goal P0 原有命令与成功语义没有回归；
5. 项目 owner 接受第 4 节的剩余边界。

在这些条件满足前，本分支只证明“实现并审计过引擎无关 Handoff 候选”，不证明生产可用性。
