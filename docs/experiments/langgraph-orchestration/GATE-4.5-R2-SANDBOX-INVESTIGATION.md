# Gate 4.5 R2 Windows Sandbox 调查

> 文档状态：`implementation-verified-without-live-call`
>
> 日期：2026-07-16
>
> 调查范围：解释 R2 的 Windows sandbox 漂移，并冻结受限修复边界
>
> 真实调用：`0`
>
> R2 历史结论：`blocked`，不修改、不改判

---

## 1. 结论

Gate 4.5 R2 的真实 preflight 已经证明：

```text
Codex CLI = 0.144.4
config mode = ignore_user_config
requested sandbox = workspace-write
observed sandbox = read-only
R2 preflight = blocked
Gate 4.5 = blocked
```

后续调查确认，问题不是 Vega 漏传 `--sandbox workspace-write`。根因是：

1. 用户 Codex 配置原本通过 `[windows] sandbox="elevated"` 提供 Windows sandbox session
   前置条件；
2. `--ignore-user-config` 会停止加载该用户配置；
3. 当前 Windows sandbox level 因而成为 `Disabled`；
4. 在该 session 条件下，显式 `workspace-write` 被降为 `read-only`。

本次修复只允许 Vega 在 `ignore_user_config=true` 时恢复这一项已确认丢失的配置：

```text
windows_sandbox_session_override = elevated
```

它固定生成：

```text
--config windows.sandbox="elevated"
```

该值同时写入 Runner identity。Vega 不开放任意 CLI 参数、任意 Codex config override 或
sandbox bypass。

## 2. 与 R2 历史证据的关系

本调查不修改以下历史文档：

- `GATE-4.5-R2-PRE-REGISTRATION.md`
- `GATE-4.5-R2-PREFLIGHT-RESULT.md`

R2 原始命令没有包含本次新增字段，因此 R2 的预注册命令、原始 summary、结果分类和
`Gate 4.5 = blocked` 必须保持原样。后运行修复不能回写历史实验，也不能把“已经找到根因”
包装成“R2 已通过”。

本调查只新增两个后续事实：

- 已解释 `workspace-write -> read-only` 的 Windows session 根因；
- 已实现一个窄白名单，使下一次全新 preflight 可以验证修复是否在真实 session 中成立。

## 3. 证据

### 3.1 R2 真实运行证据

R2 结果文档与忽略目录中的原始 evidence 一致记录：

```text
command contains = --sandbox workspace-write
command contains = --ignore-user-config
command shape valid = true
execution identity valid = true
runner status = success
observed sandbox = read-only
business case count = 0
```

这排除了“Vega 没有请求 workspace-write”这一解释。即使后运行 sentinel 解析器修复正确，
`observed sandbox=read-only` 仍然独立满足 `blocked` 条件。

证据索引：

```text
docs/experiments/langgraph-orchestration/GATE-4.5-R2-PREFLIGHT-RESULT.md
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/preflight-result.json
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/REPORT.md
.local-validation/gate-4.5/real-core-r2-preflight-20260716-private-gate-4-5-r2-preregistration-redacted/preflight/execution/execution.json
```

`.local-validation/` 证据继续保持 Git 忽略，不复制到本文档。

### 3.2 CLI 配置语义

本机 `codex-cli 0.144.4` 的 `codex exec --help` 明确说明：

```text
--ignore-user-config
  不加载 $CODEX_HOME/config.toml；认证仍使用 CODEX_HOME

--config <key=value>
  使用 dotted path 覆盖配置值
```

因此 `--ignore-user-config` 保留认证并不意味着保留用户 `[windows]` 配置。R2 对认证边界的
预注册仍然成立，但 Windows sandbox session 设置需要单独处理。

`0.144.4` 对应的 `openai/codex` 源码提交为：

```text
8c68d4c87dc54d38861f5114e920c3de2efa5876
```

固定版本调用链证据位于：

```text
codex-rs/utils/cli/src/config_override.rs
codex-rs/exec/src/lib.rs
codex-rs/config/src/loader/mod.rs
codex-rs/config/src/config_toml.rs
codex-rs/core/src/windows_sandbox.rs
```

这些源码分别证明 dotted TOML override 的解析、`ignore_user_config` 与 CLI override 的独立
传播、空用户配置层、Windows `workspace-write -> read-only` 降级判断，以及
`windows.sandbox="elevated"` 到 Windows sandbox level 的映射。

### 3.3 实现证据

受限实现由六部分组成：

1. `CodexExecOptions` 增加
   `windows_sandbox_session_override: Literal["elevated"] | None`；
2. Runner 在命令与 identity 构造前对全部 options 做严格重验证，拒绝绕过 schema
   生成的错误类型、冲突值和未规范化值；
3. `CodexExecRunner.build_command()` 只把该值编译为
   `--config windows.sandbox="elevated"`；
4. `build_codex_exec_identity()` 把
   `windows_sandbox_session_override=elevated` 写入 Runner identity；
5. 启用 override 后，成功进程必须从 Codex live header 观察到请求的实际 sandbox，
   缺失或降级时 Runner 转为 `error`；
6. Gate 4.5 harness 根据 repo、sandbox、配置来源、model、reasoning 和 ephemeral
   重建完整允许 argv，并与 Runner 命令精确相等；任何额外参数、错序或值漂移都判为
   `command_shape_valid=false`。

配置层和 runner 启动前校验都拒绝：

- 未启用 `ignore_user_config` 却设置 override；
- `elevated` 之外的值；
- `ignore_user_config`、`ephemeral` 等字段被变异成错误类型；
- 通过未知 schema 字段注入任意参数；
- `profile + ignore_user_config` 冲突。

## 4. 根因链路

```text
用户 config.toml
  [windows]
  sandbox = "elevated"
        |
        | --ignore-user-config
        v
用户配置未加载
        |
        v
Windows sandbox level = Disabled
        |
        | --sandbox workspace-write
        v
Codex live header = read-only
```

`--sandbox workspace-write` 描述模型命令的目标 sandbox policy，
`windows.sandbox="elevated"` 则恢复 Windows sandbox session 的运行前置条件。两者不是互相
替代的配置，下一次真实 preflight 必须同时冻结并验证。

## 5. 实现边界

### 5.1 允许

只允许以下组合：

```yaml
ignore_user_config: true
windows_sandbox_session_override: elevated
```

对应命令片段固定为：

```text
--sandbox workspace-write
--ignore-user-config
--config windows.sandbox="elevated"
```

对应 Runner identity 至少包含：

```text
config_mode = ignore_user_config
ignore_user_config = true
windows_sandbox_session_override = elevated
sandbox = workspace-write
```

### 5.2 不允许

本实现明确不允许：

- 任意 `--config key=value` 透传；
- 其他 `windows.sandbox` 值；
- 任意 CLI args 数组；
- `--dangerously-bypass-approvals-and-sandbox`；
- 修改用户 `config.toml`；
- 读取、打印、复制或持久化 credential；
- 因实现完成而自动发起真实 Codex 调用；
- 修改 R2 预注册、原始证据或结果分类；
- 把下一次 preflight 通过等同于 Gate 4.5 通过。

## 6. 本阶段验证范围

本实现阶段只允许本地确定性验证：

- 配置 schema 接受唯一合法组合；
- 配置 schema 拒绝无 `ignore_user_config` 的 override；
- runner 启动前再次拒绝非法或绕过 schema 构造的值；
- 命令精确生成 `--config windows.sandbox="elevated"`；
- Runner identity 精确记录 override；
- 成功进程的 live header 缺失或报告降级 sandbox 时 fail closed；
- preflight 完整 argv 拒绝额外 `--config`、`--full-auto`、sandbox bypass、错序和
  repo/sandbox/model/reasoning 漂移；
- Gate 4.5 summary schema 升级为 `4` 并由测试锁定；
- 项目配置摘要显示非敏感 override 值；
- 既有 `profile + ignore_user_config` 互斥和 sandbox 角色策略不回归。

本阶段不运行真实 preflight，不启动 worker/reviewer，不创建业务 fixture，不调用 provider，
不使用真实调用结果更新 Gate 4.5。

最终本地分片验证结果：

```text
Codex Runner targeted nodes = 11 passed
Gate 4.5 harness with project LangGraph venv = 32 passed
Core suite = 340 passed, 1 skipped
LangGraph suite with project venv = 148 passed, 1 skipped
```

默认 Python 未安装 `vegaloom[langgraph]` 可选依赖，因此 LangGraph 结果只采用项目内
`.tmp/langgraph-validation-venv`。所有 pytest 分片均使用独立 `.tmp/pytest/runs/`
basetemp 和 cache，没有写入 `tests/`、`runs/` 或仓库根目录。

已知的
`test_abrupt_process_exit_keeps_resumable_checkpoint_without_finally`
在一次组合分片中曾出现 checkpoint hash 不一致；隔离复跑通过，随后
`test_crash_windows.py` 整文件得到 `8 passed`。这不能关闭 Windows abrupt-exit 残余风险，
因此它仍保留在下一次真实 preflight 的启动前停止条件中。

## 7. 下一次真实 preflight 的启动前停止条件

只有以下条件全部满足，才允许项目 owner 另行授权一个全新的真实 preflight：

1. 新的预注册合同已经冻结并提交，不修改 R2 历史合同。
2. HEAD、远端 SHA、工作区状态和预注册基线完全一致。
3. 受限实现及其测试已经进入该干净基线。
4. Codex executable 路径和 CLI 版本已重新记录；若不再是 `0.144.4`，必须重新评审合同。
5. 命令同时冻结 `--sandbox workspace-write`、`--ignore-user-config` 和
   `--config windows.sandbox="elevated"`。
6. Runner identity 同时冻结 `ignore_user_config=true`、
   `windows_sandbox_session_override=elevated` 和 `sandbox=workspace-write`。
7. 原始命令只以 SHA-256 绑定，脱敏命令与 identity 可以一致复核。
8. 使用新的隔离 preflight fixture 和新的 `.local-validation/` 结果目录。
9. 外部调用预算重新明确为最多 1 次 preflight、0 次 worker、0 次 reviewer、0 次自动重试。
10. provider、model、reasoning、ephemeral、timeout、sentinel 和数据出站范围重新冻结。
11. 不读取或写入 credential、`.env`、Authorization header 或用户认证存储。
12. Windows abrupt-exit checkpoint 残余风险仍被单独列为未关闭风险。

任一条件不满足时，停止在创建 session、fixture 或外部 execution 之前。

## 8. 下一次真实 preflight 的运行中与运行后停止条件

下一次全新 preflight 启动后，出现以下任一情况必须立即分类为 `blocked`，不得自动重试、
切换配置或进入业务 Case：

- evidence command 缺少三个冻结参数中的任意一个；
- `execution.command_sha256`、脱敏命令或 Runner identity 不一致；
- live header 的 Codex 版本、provider、model 或 reasoning 与新合同不一致；
- live header 的 sandbox 不以 `workspace-write` 开头；
- Runner 不是成功终态，或 execution artifact 不完整；
- sentinel 缺失；
- preflight fixture 前后不 clean；
- 创建了任何业务 fixture、Vega 业务 run、worker 或 reviewer；
- 出现未预注册的数据出站、配置来源或调用次数；
- 无法确认 owned process 已终止。

即使全部通过，也只允许记录：

```text
new preflight = passed
R2 historical result = blocked
Gate 4.5 = blocked
business case count = 0
```

是否进入 Gate 4.5 业务 Case，必须由项目 owner 在审查新 preflight 证据和残余风险后再次明确
授权，并冻结新的业务调用合同。

## 9. 当前停止点

本调查在实现与本地确定性验证后停止。本阶段不触发真实 Codex 调用，不创建新的 Gate 4.5
session，不消费任何外部调用预算，也不改变 R2、Gate 4.5 或 Gate 5 的历史状态。
