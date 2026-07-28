# MA-2B Node Project Profile 探针 V3 结果

> 运行日期：2026-07-28
> Candidate：`MA2B-NODE-PROFILE-V3`
> 冻结提交：`47c68f8534e15f77e0512ff7d2d393ac67b58e92`
> 结果：`inconclusive / provider_auth_blocked`

## 一、直接结论

本轮按预注册完整启动了：

```text
S Worker：1 次
M Worker：2 次并行
Planner：0
Reviewer：0
Retry：0
```

实际消耗 `3/3` 次 Worker Provider 调用，但三次调用都没有进入模型执行。Codex CLI 先发生
WebSocket 请求超时并回退 HTTPS，随后 Provider 以无效 API key 的 `401` 拒绝请求。三次均
以退出码 `1` 正常结束，没有出现 `termination_unconfirmed`，也没有产生代码改动、Token
usage 或最终 verifier 结果。

因此本轮仍然不能评价单 Worker 与双 Worker 的完成率、墙钟、Token 或经济性。V3 能确认的
不是 Multi-Worker 能力，而是一个新的控制面缺口：

> `--ignore-user-config` 只隔离 `config.toml`，不会隔离认证状态；V3 没有在消耗实验预算前
> 验证 `CODEX_HOME` 中的认证是否可用。

## 二、调用前资格

调用前门禁结果如下：

- 当前分支 HEAD 等于冻结提交，Tracked 工作区 clean；
- candidate 已推送到远端；
- source workspace HEAD 为
  `a4ab2dbaf6e8ed6676bdc207bf42a384bf42a2ef`；
- source tree 为 `61efd1dc116be8101000f464739b817b0eb33f16`；
- 新鲜红基线为 `11 failed in 7.05s`；
- task 与两个 context packet 和 V2 按字节一致；
- candidate、harness、verifier 与 prompt 哈希检查通过；
- M 两个 prompt 继续保持上下文包互斥；
- Windows 解析到原生 `codex.exe`；
- 命令实际包含 `--ignore-user-config` 和 `--ignore-rules`；
- S、M 与控制目录均为新目录，没有覆盖 V1/V2 现场。

模型与客户端继续冻结为：

```text
Codex CLI：codex-cli 0.144.6
Model：gpt-5.6-sol
Reasoning effort：medium
Worker timeout：480 秒
```

## 三、真实运行结果

| Treatment | Provider 调用 | 状态 | 调用耗时 | Token | Workspace 改动 | 最终 verifier |
|---|---:|---|---:|---:|---|---|
| S | 1 | `provider_auth_error` | 130.402 秒 | 不可用 | 无 | 未运行 |
| M / Node 检测 | 1 | `provider_auth_error` | 133.162 秒 | 不可用 | 无 | 未运行 |
| M / 合同与上下文 | 1 | `provider_auth_error` | 135.100 秒 | 不可用 | 无 | 未运行 |

S probe 总墙钟为 `131.594` 秒，M probe 总墙钟为 `137.079` 秒。虽然结构化 summary 计算出
M 比 S 慢 `4.17%`，但这只是认证失败与重连耗时，**不是 Worker 性能数据，禁止用于经济性
结论**。

三次事件均只有：

```text
thread.started
turn.started
transport reconnect errors
one completed error item
turn.failed
```

没有 `turn.completed`，所以没有可信 Token usage。三个 workspace 均未形成 tracked diff，
diff SHA-256 都是标准空内容哈希
`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

## 四、为什么 `--ignore-user-config` 仍然读取了错误认证

本机 `codex exec --help` 对边界有明确说明：

```text
--ignore-user-config 不加载 CODEX_HOME/config.toml；auth still uses CODEX_HOME
```

运行后只检查了环境变量是否存在，没有读取或提交任何值：

```text
OPENAI_API_KEY：不存在
CODEX_API_KEY：不存在
OPENAI_BASE_URL：不存在
CODEX_HOME：存在
```

结合三次相同的 `invalid_api_key` 结果，最可信的解释是：V3 成功隔离了用户配置，但认证仍从
`CODEX_HOME` 的认证状态进入 Codex CLI，而该状态在本轮不可用或已失效。公开证据不包含密钥、
真实认证文件路径、Provider 会话标识或原始错误全文。

## 五、停止语义复盘

S 不是 timeout，也没有出现终止未确认；它以普通 runner error 结束。V3 的冻结规则允许在
“普通失败且终止状态可信”后继续 M，因此 Driver 启动了两个并行 Worker，最终完整消耗三次
预算。

这暴露出 V3 Driver 的分类缺口：

- `worker_error` 混合了“模型尝试后任务失败”和“模型执行前认证失败”；
- Provider auth error 应属于输入/Provider 控制面错误；
- 该类错误应在 S 后阻断 M，而不是被当成可比较的能力失败。

本轮不修改冻结结果、不补跑，也不把 M 已启动解释为能力证据。

## 六、相对 V2 能说明什么

V2 被 Windows 终止确认阻断；V3 三个原生 `codex.exe` 都以退出码 `1` 正常结束，且
`termination_unconfirmed=false`。这说明本轮没有复现 V2 的终止未确认问题，但因为三次都没有
触发 timeout，不能据此证明 480 秒超时路径已经在真实 Provider 任务中稳定。

V3 仍然不能证明：

- S 或 M 能完成冻结 Node 任务；
- M 的两个切片可以成功确定性集成；
- 窄上下文能提高完成率；
- M 比 S 更快、更省 Token 或更省人工；
- 当前 Node candidate 已具备正式 MA-2B Pilot 资格。

阶段结论固定为：

```text
node_profile_v3_probe_inconclusive
provider_auth_preflight_missing
multi_worker_capability_not_tested
multi_worker_economic_comparison_unavailable
formal_ma2b_pilot_readiness_blocked
```

## 七、下一步边界

V3 candidate 到此结束，预算已耗尽，不追加 Provider 调用。任何后续 candidate 必须先离线
冻结认证边界：

1. 明确绑定可用且不泄密的 Codex auth source；
2. 把 `--ignore-user-config` 与“认证仍使用 `CODEX_HOME`”作为两个独立合同；
3. 在 S/M 预算前增加不修改仓库的认证健康门，或为单独的健康调用预注册预算；
4. 将 `invalid_api_key`、认证缺失和 Provider endpoint 错误分类为阻断 M 的控制面错误；
5. 认证门通过后才重新决定是否值得再跑 Node candidate。

这不是继续扩建 SDK、Planner、Reviewer 或证据框架的理由。当前仍应回到原始问题：在输入、
认证与执行控制都可信的前提下，双 Worker 相对单 Worker是否有真实完成率或经济收益。

结构化脱敏结果位于：

```text
eval/experiments/multi-agent-coordination/results/MA2B-NODE-PROFILE-V3-2026-07-28.json
```

原始 Provider JSONL、execution、prompt 与本机 workspace 继续只保留在忽略的
`$repoRoot/.tmp/m2n/`，不进入公开 Git 历史。
