# Gate 4.5 R5 Readiness

> 复审日期：`2026-07-17（星期五）`
>
> R4 历史结论：`blocked`，保持冻结
>
> R5 确定性准备：`ready to freeze pre-registration`
>
> R5 真实执行：`not started`
>
> Gate 5：`not approved`

---

## 1. 结论

R4 暴露的三项确定性问题已经完成根因修复：

1. preflight 在 provider 调用前绑定脱敏认证模式；
2. `--ignore-user-config` 可以显式恢复受限的 loopback provider descriptor；
3. provider 返回的 credential-like 掩码片段和 request correlation 不再进入新证据。

当前状态是：

```text
R5 deterministic readiness = ready
R5 real preflight = not started
R5 business cases = not started
Gate 4.5 = blocked by pending R5 real evidence
Gate 5 = not approved
real provider calls in this readiness phase = 0
```

本 readiness 只批准冻结独立 R5 预注册合同，不把 fake Runner 或本地测试包装成真实
provider 证据。

## 2. R4 根因与 R5 修复边界

R4 的确定性根因是：

```text
auth mode = api_key
config mode = ignore_user_config
custom provider only exists in user config
live provider after ignoring config = openai
result = 401 Unauthorized
```

R5 不通过修改全局配置、读取 API key、切换模型或回写 R4 证据解决该问题，而是在
`CodexExecOptions` 内增加受限 provider descriptor：

```json
{
  "name": "sandboxproxy",
  "base_url": "http://127.0.0.1:18080/v1",
  "wire_api": "responses",
  "requires_openai_auth": true,
  "supports_websockets": false
}
```

稳定指纹为：

```text
dfbc5ee355e628d747bcbcb9e64a26f5ae9be4bab135c84c151397e364898f65
```

descriptor 不含认证值。API key 仍由 Codex 自己管理，Vega 不打开、不复制、不记录
credential store。

## 3. Provider descriptor 安全合同

`CodexProviderDescriptor` 固定约束：

- provider name 只能包含字母、数字、下划线和连字符；
- `base_url` 只允许 `http` loopback host；
- 必须显式提供大于 0 的端口；
- 禁止 userinfo、query、fragment、空白、反斜杠和 NUL；
- wire API 当前只允许 `responses`；
- provider 只能与 `ignore_user_config=true` 配合；
- 不接受任意 CLI 参数或任意 dotted TOML key。

Runner 使用固定顺序生成：

```text
model_provider
model_providers.<name>.name
model_providers.<name>.base_url
model_providers.<name>.wire_api
model_providers.<name>.requires_openai_auth
model_providers.<name>.supports_websockets
```

命令启动前还会对嵌套 descriptor 做严格重验证。复审期间专门验证了
`model_copy(update=...)` 产生的未校验外部 URL；修复前该对象可以绕过嵌套模型的默认实例
复用，修复后命令和 Runner identity 都会拒绝它。

## 4. 认证模式 fail-fast

R5 harness 新增：

```text
--expected-auth-mode api_key|chatgpt
```

真实 preflight 在 provider 调用前执行：

```text
codex login status
```

只解析：

```text
api_key
chatgpt
unknown
```

不持久化 stdout/stderr，不读取 key 值。认证模式不一致或无法识别时：

```text
preflight = blocked
runner_status = not_started_auth_mismatch
provider calls = 0
business case count = 0
```

API key 与 `--ignore-user-config` 同时使用时，CLI 合同强制要求完整的 loopback provider
descriptor，防止再次静默回落到内置 provider。

## 5. 命令、身份与证据

核心 Runner 新增统一的：

```text
build_codex_exec_command()
```

Gate 4.5 harness 不再复制 Codex argv 拼装逻辑，而是复用核心 Runner。Runner identity 新增：

```text
config_mode = isolated_provider
provider
provider_base_url
provider_wire_api
provider_requires_openai_auth
provider_supports_websockets
provider_descriptor_sha256
```

provider descriptor 同时进入：

- preflight command contract；
- worker/reviewer fixture `.vega.yaml`；
- `execution.json` Runner identity；
- Gate 4.5 `summary.json` 与 `preflight-result.json`；
- 报告中的 descriptor SHA-256。

summary schema 从 `4` 升级为 `5`。旧 R4 raw evidence 不回写、不迁移。

## 6. 脱敏加固

新增脱敏覆盖：

- `Incorrect API key provided: <masked-value>`；
- `API key provided: <masked-value>`；
- `request id`；
- `x-request-id`；
- `cf-ray`。

新证据仍保留可判定的：

```text
401 Unauthorized
authentication failure category
provider/model/network terminal status
```

但不保留 credential-like 片段和请求关联标识。R4 raw evidence 保持冻结，不做事后改写。

## 7. 确定性验证

本轮明确完成：

| 范围 | 结果 |
| --- | ---: |
| Gate 4.5 harness | `47 passed` |
| Codex provider / Runner / 配置相关 smoke | `42 passed` |
| Redaction + execution persistence | `42 passed` |
| Project config hardening | `3 passed` |
| **唯一测试合计** | **`134 passed`** |

关键覆盖包括：

- provider descriptor 的 loopback URL 安全边界；
- 固定 provider argv 和 Runner identity；
- descriptor SHA-256 稳定绑定；
- 变异 descriptor 的严格重验证；
- fixture 对 worker/reviewer 的同一 provider 传播；
- auth mismatch 时 provider 调用次数为 0；
- CLI 缺字段、配置冲突和外部 endpoint fail-fast；
- summary schema `5` 和 provider/auth 字段；
- masked key、request id、`cf-ray` 的持久化脱敏。

完整 fake Core Dogfood：

```text
session = fake-core-r5-provider-readiness-20260717
schema_version = 5
linear-low = passed
graph-low = passed
graph-crash-hitl = passed
conclusion = pass
elapsed = 277.544 seconds
```

三个 Case 均满足：

```text
worker start count = 1
worker execution count = 1
reviewer execution count = 1
verification = passed
artifact integrity = true
evidence freshness = true
```

`graph-crash-hitl` 还满足：

```text
fault triggered = true
decision count = 1
pending count = 1
consumption count = 1
graph state valid = true
checkpoint manifest valid = true
run status consumable = true
```

本轮曾探索性并行启动更宽的 pytest 分片，三个分片都超过外层时限，未形成可消费终态，
因此不计入通过数；命令行明确属于本仓库的残留 pytest 进程已经停止。随后所有用于 readiness
结论的节点均使用独立 `--basetemp` 和 cache 重新执行并得到明确 passed 计数。

## 8. 仍未关闭的风险

1. 尚未证明 loopback provider 在 R5 执行时仍在线；
2. 尚未证明当前 API key 对该 provider 和 `sandbox-model` 仍有效；
3. 尚未获得新的真实 worker、reviewer、transport 和 token 成本证据；
4. Windows abrupt process exit、SQLite/WAL 和 checkpoint manifest 的真实硬退出风险仍在；
5. fake dogfood 只能证明编排与证据合同，不能替代真实模型质量；
6. Gate 5 三路 reviewer、隔离 reducer 和 deterministic aggregator 尚未实现。

## 9. R5 准入顺序

下一步只能按以下顺序推进：

1. 完成静态检查并提交、推送本轮实现与本文档；
2. 以该干净实现提交的完整 SHA 冻结独立 R5 pre-registration；
3. 预注册 auth mode、provider descriptor、descriptor SHA-256、model、reasoning、sandbox、
   timeout、调用预算和停止条件；
4. 使用全新 session 执行一个完整 R5 业务命令，内置 preflight 最多一次；
5. auth mismatch、provider mismatch、命令漂移或 preflight 失败后立即停止，不创建业务
   fixture；
6. preflight 通过后才允许各执行一次 Linear、LangGraph low-risk 和 LangGraph crash +
   HITL；
7. 三个 Case 全部 `passed` 且所有安全不变量成立，Gate 4.5 才能改判 `pass`；
8. 只有 Gate 4.5 `pass`，才允许进入 Gate 5。

## 10. Readiness 判定

```text
auth mode binding = ready
explicit provider descriptor = ready
provider command / identity binding = ready
credential-like diagnostics redaction = ready
R5 pre-registration = approved to prepare
R5 real execution = not approved by this document
Gate 5 = not approved
```
