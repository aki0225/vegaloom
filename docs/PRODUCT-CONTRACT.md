# 产品契约

## 核心定位

Vega 是面向个人开发者的轻量 AI Coding Harness。它不替代 Codex、Claude Code
或其他编码模型，而是为一次真实研发任务补上可控的外层闭环：

```text
bug / feature
  -> 项目上下文编译
  -> 受控 worker 执行
  -> 确定性验证
  -> Reflect 证据与变更风险门禁
  -> 隔离 reviewer
  -> 修复或人工接管
  -> 交付报告与可恢复证据
```

Vega 的核心价值不是“拥有更多 Agent 功能”，而是回答四个问题：

1. worker 开始前应该看到哪些项目规则和任务事实？
2. 外部编码会话如何限制范围、时间和工作区污染？
3. 如何用测试和隔离 reviewer 避免 worker 自证正确？
4. 中断、超时或 provider 异常后，如何保留现场并安全交还人工？

## 日常入口

日常使用只要求理解以下入口：

```text
vega do bug|feature
vega status
vega finish
vega stop
vega recover
```

`brief`、`reflect`、`gate`、`review-pack`、`review` 和 `loop continue`
是可单独调用的流水线阶段，主要用于排障、人工接管和解释运行过程，不要求用户每天手工编排。

## 能力分层

### 核心能力

- `AGENTS.md`、项目画像和任务输入的上下文编译。
- worker/reviewer 角色隔离和固定 sandbox。
- Codex 配置只允许强类型白名单字段；Windows sandbox session override 仅允许固定值
  `elevated`，且必须与 `ignore_user_config: true` 配合。
- 显式 provider 只允许不含凭证的 `http` loopback descriptor，并且必须与
  `ignore_user_config: true` 配合；任意外部 endpoint 和任意 CLI/TOML 透传仍然禁止。
- 验证命令、变更预算、Prompt 预算与工作区污染门禁。
- auto 首轮拒绝已有 tracked diff；同一 run 的后续轮次保留上一轮 diff 作为基线。
- iteration-local risk gate 的结果与报告绑定 source reflect、iteration、结果哈希、风险和建议；Finish 会结合 trace、连续 iteration 与 Reflect 重算复核。缺失、篡改、语义不一致或绕过 `human-review` 时，不得给出 `ready_to_commit`。
- 独立 reviewer 也必须带上同一份风险门禁；`human-review` 下的 AI 审查只能提供辅助发现，不能成为 Goal checkpoint 的自动完成证据。
- reviewer 不能覆盖确定性验证失败。
- state、trace、execution、status、finish、stop 和 recover。

### 高级能力

- 独立执行 brief、reflect、gate、review-pack 和 review。
- 为大范围变更声明 scope profile。
- 本地 decision ledger。

### 实验能力

- Memory proposal / ledger。
- Goal P0 长任务人工状态层。
- Codex skill adapter。

实验能力不得反向扩大核心成功条件。未使用 Memory、Goal 或 adapter 时，bug/feature
主流程仍必须可以完整运行。

## 项目知识分层

项目知识按职责分为四层：

| 层级 | 内容 | 特性 |
|---|---|---|
| `AGENTS.md` | 稳定规范、架构边界、长期踩坑 | Git 版本化，面向人和 AI |
| `.vega.yaml` | 验证命令、预算、风险路径、runner 策略 | 机器可执行 |
| run artifacts | 本次任务、diff、验证、review 和恢复证据 | 单次运行事实 |
| accepted memory | 已人工确认、跨任务可复用的局部经验 | 可选，不是规范来源 |

稳定规则应优先进入 `AGENTS.md`，可机械执行的约束进入 `.vega.yaml`，单次任务事实留在
run artifacts。只有无法合理归入前三层、且有明确适用范围和来源证据的经验，才适合进入
accepted memory。

## 工作区文件卫生

仓库根目录不得堆放测试临时目录、验证日志或运行生成物。测试源码和静态 fixture 放在
`tests/`，可丢弃的测试临时文件放在 `.tmp/`，人工验证输出放在 `.local-validation/`，
Vega run artifacts 放在 `runs/`，本地 memory 数据放在 `memory/`，Python 构建产物
放在 `dist/` 和 `build/`。不得把这些文件写入其他项目或仓库根目录；详细目录职责见
`docs/WORKSPACE-HYGIENE.md`。

## Memory 决策

Memory 是可选经验账本，不是每轮必须生成的流水线产物：

- brief 阶段不产生经验。
- loop 成功不等于产生了可复用经验。
- proposal 数量允许为 0。
- 只有用户在 reflect 阶段明确提供经验候选时才生成 proposal。
- 长期 ledger 仍必须由用户显式 accept/reject。
- accepted memory 可以参与后续上下文编译，但不能覆盖代码、测试、`AGENTS.md` 或当前任务事实。

当前不引入向量数据库、embedding、自动学习、自动冲突合并或自动长期写入。

## 外部 Runner 配置边界

Vega 不是 Codex CLI 配置代理，也不开放任意命令行透传。项目配置只能选择 schema 明确列出的
角色字段，runtime 再将其编译为稳定命令并写入 Runner identity。

Codex CLI `0.144.4` 在 `--ignore-user-config` 后会丢失用户
`[windows] sandbox="elevated"`。在 Windows sandbox level 为 `Disabled` 的环境中，这会使
显式请求的 `workspace-write` 在真实 session 中降为 `read-only`。为恢复这一项已确认的
session 前置条件，Vega 只增加：

```yaml
ignore_user_config: true
windows_sandbox_session_override: elevated
```

其产品合同固定为：

- 只生成 `--config windows.sandbox="elevated"`，不接受其他值。
- 只在 `ignore_user_config: true` 时合法，不能与 `profile` 同时使用。
- 值必须写入 Runner identity，并参与命令与 attempt 证据绑定。
- 不修改用户 `config.toml`，不读取认证存储，不记录凭证。
- 不开放任意 dotted config、任意 CLI 参数或
  `--dangerously-bypass-approvals-and-sandbox`。
- worker/reviewer 的 `workspace-write` / `read-only` 角色 sandbox 合同保持不变。

请求参数不是成功证据。真实调用仍必须从非敏感 live header 验证实际 sandbox；观测值与合同
不一致时必须停止并交还人工。

同样地，`--ignore-user-config` 会丢失用户 `config.toml` 中的自定义 provider。为支持
“认证仍由 Codex 管理、endpoint 必须显式冻结”的场景，Vega 允许：

```yaml
ignore_user_config: true
provider:
  name: sandboxproxy
  base_url: http://127.0.0.1:18080/v1
  wire_api: responses
  requires_openai_auth: true
  supports_websockets: false
```

该能力的产品合同固定为：

- `base_url` 只能使用 `http` loopback host 和显式非零端口；
- 禁止 userinfo、query、fragment、空白、反斜杠和外部 host；
- provider name 只能进入受限 dotted key，不允许点号或任意 TOML 路径；
- descriptor、规范化 endpoint 和 SHA-256 必须进入 Runner identity；
- 命令启动前必须严格重验证 descriptor，不能信任绕过 Pydantic 校验产生的模型实例；
- Vega 不读取、不复制、不输出 API key 或 Codex credential store；
- live provider/model/auth identity 不匹配时必须 fail-fast，不能自动切换或重试。

## 增长约束

新能力进入核心前，至少应改善以下一项并提供 dogfood 证据：

- 任务成功率。
- reviewer 有效缺陷发现率。
- 人工操作步骤。
- 中断恢复能力。
- 无关上下文、token 或执行耗时。

仅增加命令、artifact、状态字段或架构名词，不视为有效演进。没有真实使用证据的能力保持实验状态，
不得继续扩建。

## v0.1.0 停止线

v0.1.0 完成核心证据一致性和口径收口后进入功能冻结：

- 保持上下文编译、受控执行、确定性验证、隔离审查和恢复交接稳定。
- `loop continue` 必须绑定原仓库和 `needs_human` 状态。
- Reflect 与 Review 必须使用同一工作区快照，证据过期时停止并交还人工。
- Goal P0 完成前必须重新校验 child run 或 manual evidence。
- 不实现 Goal P1、多 Agent、数据库、Web UI、向量 Memory、后台 daemon 或自动提交发布。
- 新能力只有在多次真实 dogfood 暴露同一问题，并能改善增长指标时才重新评估。

当前是本地单用户 CLI，不支持多个进程并发写同一个 run。该限制作为明确边界保留，不为假设性
并发场景引入锁服务或数据库。
