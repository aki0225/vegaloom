# Vega v0.1.1 发布与迁移说明

v0.1.1 是 v0.1 冻结范围内的安全维护版本。它整合已经在独立 Dogfood 分支验证过的
路径范围、验证隔离、崩溃恢复和同一 run 并发保护，不增加新的 Agent 角色、数据库、
Web UI、长期 Memory 写入或自动提交能力。

## 主要变化

### 精确路径范围

`.vega.yaml` 可以声明：

```yaml
scope:
  allowed_paths:
    - src/auth/**/*.py
  forbidden_paths:
    - tests/**
    - .vega.yaml
```

`forbidden_paths` 优先于 `allowed_paths`。Runtime 会在 worker 后、verification 后和
review 前检查 staged 与 unstaged tracked diff，并拒绝 HEAD 漂移、
`assume-unchanged` 和 `skip-worktree` 隐藏状态。

### Reviewer 授权与 Finish 重算

reviewer 启动前会复核项目策略、工作区指纹、index 标记和 review artifact。Finish
不信任旧结论，会重新计算 scope、risk、artifact freshness 和项目策略绑定。

### Verification 隔离

每个 run、iteration 和 verification command 使用独立临时目录。实际路径通过子进程
环境传入，并与 verification artifact 绑定。Windows 命令执行保留原生命令行，避免嵌套
引号被二次转义。

### 崩溃恢复

半完成 iteration 会被标记为 `interrupted` 并保留现场。recover 使用本地一致性事务补全
state、trace、interruption report 和终态 supersede；后续 continue 使用下一连续
iteration，不覆盖旧证据。

### 同一 Run 并发保护

`start`、`continue`、`recover`、`finish` 和 decision append 使用同一个本地非阻塞
OS 文件锁。锁已被占用时命令立即失败，不等待或自动抢锁。

`vega stop` 不获取该锁，只向当前 `execution_id` 写入 stop request，避免旧请求误停新的
execution。

## 兼容性变化

### `.vega.yaml`

- 配置 schema 仍为 `version: 1`，旧配置不需要强制迁移。
- `scope.allowed_paths` 和 `scope.forbidden_paths` 默认都是空列表；未配置 scope 时保留
  v0.1.0 的路径行为。
- 绝对路径、仓库逃逸路径和歧义 Windows 路径会被拒绝。
- scope 规则必须使用 POSIX 仓库相对路径；盘符、反斜杠、空路径段、`.`、`..` 和
  `:` 会被拒绝。
- `scope.allowed_paths` 非空时，所有 tracked diff 都必须命中 allowlist。
- `scope.forbidden_paths` 命中时直接 fail closed。

升级前建议先运行：

```powershell
vega config check --repo .
```

### Verification 临时目录

`{{vega_verification_temp}}` 是可选占位符，必须作为未加引号的独立路径 token 使用。
Runtime 会在启动 shell 前把它替换为环境变量引用，实际目录位于：

```text
.tmp/vega-verification/<run_id>/iteration-<n>/command-<n>/
```

该目录按 run、iteration 和 command 隔离，但不会由 Vega 自动删除。目标仓库应忽略
`.tmp/`，任务完成后由人工或项目自己的清理策略处理。

### 旧 Run

旧 run 仍可用于查看和复盘。缺少以下 v0.1.1 证据时，Finish 不会把它提升为
`ready_to_commit`：

- 三阶段 scope gate；
- `project-policy-snapshot.json`；
- 根状态绑定；
- artifact v2 身份与 freshness。

需要继续执行的任务应创建新的 v0.1.1 run，不应手工补写旧 artifact。

新 run 使用 verification artifact v2，绑定 `run_id`、真实 iteration、shell 类型、
配置命令、实际执行命令、execution lease 和脱敏后的临时目录。

### 并发命令

同一 run 同时只能有一个生命周期写入者。第二个命令会收到 busy 错误；它不会排队，也
不会自动重试。确认原命令结束后再重新执行。

### Stop 与 Trace

第二个 CLI 发出的 `vega stop` 不再直接追加根 `trace.jsonl`。持锁 owner 消费 stop
request 后负责写入可信终态证据。

依赖 trace 的外部消费者需要识别新增的三阶段 scope 事件、
`loop_iteration_interrupted`、`run_terminal_superseded` 和带 `recovery_id` 的
`loop_recovered`，并且不能继续依赖第二个 stop CLI 写入
`execution_stop_requested`。

### 新增控制与证据文件

新 run 可能新增：

- 根级 `project-policy-snapshot.json`；
- 每轮三组 scope result/report；
- `interruption-report.md`；
- `.control/run-mutation.lock`；
- 临时 recovery transaction 和受限 owner 诊断信息。

lock 文件会长期保留。owner 信息和 recovery transaction 属于运行控制状态，不是数据库
事务、目标仓库锁或跨机器协调证据。

## 不变边界

- 不自动 commit、push、release 或删除目标文件。
- 不自动写入长期 Memory。
- 不提供目标仓库的操作系统级事务隔离。
- 不支持跨机器锁、网络文件系统锁、数据库事务或断电 `fsync` 保证。
- 当前 Windows 验证不能替代 POSIX `fcntl.flock` 和 POSIX shell 路径的发布验证。
- 旧 run 与本地 artifact 不用于抵抗拥有完整本地写权限的恶意攻击者。

## 升级检查

```powershell
python -m pip install -e ".[dev]"
vega --version
vega config check --repo .
python -m compileall src
python -m pytest
ruff check src tests
git diff --check
```

`vega --version` 应输出 `0.1.1`。升级不会自动修改目标项目配置，也不会迁移或重写历史
run。

## 发布包验证

- wheel 和 sdist 必须包含只读的 `engineering-change` baseline LoopSpec。
- workspace 中的同名 `loops/engineering-change.loop.yaml` 仍优先，用于显式覆盖。
- 在源码仓之外的空目录安装 wheel 后，`vega list-loops` 必须列出
  `engineering-change`。
- 正式标签发布前必须通过 Linux/POSIX 测试和干净 wheel 安装验证。

## 合并与发布检查

v0.1.1 候选变更必须先进入独立 `release/*` 分支，不因本地验证通过而直接合并或打标签。
每次候选变更按以下顺序收口：

1. 等待远端 CI 的静态检查、Python 3.11 全量、Python 3.12 分片、Windows 专项、
   POSIX 专项和发布包安装验证全部通过。
2. 复核候选分支相对 `main` 的完整 diff，重点检查项目上下文边界、Memory 仓库 scope、
   敏感 task 拒绝顺序和 Reviewer 隔离措辞。
3. 只有 CI 与复核都通过后，才允许把候选分支合并到 `main`；`v0.1.1` 标签和发布动作仍需
   单独人工确认。
4. 可公开 evidence pack 只能从绑定到真实 run 的原始 artifact 脱敏生成；缺少原始证据时
   保留文档结论，不拼接或伪造样例。

当前 Memory 仓库 scope 使用不暴露绝对路径的本地规范化路径哈希，解决同名仓库串用问题，
但不承诺跨机器或换目录后自动复用。Memory 仍是实验能力，不应为追求可移植性扩大 v0.1.1
发布范围。
