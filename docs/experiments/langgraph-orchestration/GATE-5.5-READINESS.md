# Gate 5.5 Reviewer Topology 真实执行 Readiness

> 状态：`ready-for-baseline-freeze / provider not started`
>
> 日期：`2026-07-18（星期六）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 冻结 tag：`gate-5.5-pre-run-v1（提交后创建）`
>
> 真实 provider 调用：`0`

---

## 1. Readiness 结论

Gate 5.5 的正式预注册、12 个合成 case、显式 ground truth、评分器、调用预算、真实 Runner
harness、复跑规则和 fake readiness 已完成。

截至本文写入时：

```text
Gate 5 = pass
Gate 5.1 = pass
Gate 5.5 pre-registration = frozen
Gate 5.5 fake readiness R6 = pass
Gate 5.5 real preflight = not started
Gate 5.5 real reviewer sessions = 0
```

真实调用前仍必须完成以下串行手续：

1. 提交本文和全部 Gate 5.5 实现；
2. 创建不可变 annotated tag `gate-5.5-pre-run-v1` 指向该提交；
3. 推送分支和 tag；
4. 从该 tag 对应的 clean checkout 运行；
5. harness 再次确认 `HEAD == gate-5.5-pre-run-v1^{commit}`；
6. 执行唯一一次 provider preflight。

## 2. 冻结 Commitment

```text
dataset =
eval/gate-5.5/cases.json

dataset SHA-256 =
50d2fac3f04260b6f9bbb13831fd2fbd2b9db39d064d98d5e1f4719d3b042bb1

ground truth =
eval/gate-5.5/ground-truth.json

ground truth SHA-256 =
2c5839d7c770a3a4e58f918a2c2fdfc5f548b7249dea2256df2281bf3e7a782b
```

数据集固定为：

```text
clean = 3
correctness = 3
verification_adequacy = 3
security_design = 3
total = 12
```

调用 readiness 固定为：

```text
single = 12
adaptive = 24
fixed_three = 36
initial reviewer sessions = 72
provider preflight = 1
initial external sessions = 73
hard limit = 90
automatic retries = 0
```

## 3. 真实执行身份

2026-07-18 的本地只读观察：

```text
Codex CLI = 0.144.5
codex login status = Logged in using ChatGPT
127.0.0.1:18080 = reachable
```

冻结身份：

```text
auth = chatgpt
provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
reviewer sandbox = read-only
preflight sandbox = workspace-write on isolated sentinel fixture
ephemeral = true
memory = off
```

真实模式会拒绝 CLI 改写上述字段、timeout 或 90 次预算。

## 4. 验证证据

Gate 5.5 新增测试：

```text
reviewer topology harness / scorer / dataset = 57 passed
```

相邻 Gate 5 回归：

```text
parallel review contract / graph = 46 passed
real adapter critical nodes = 2 passed
```

静态检查：

```text
python -m compileall -q src scripts/langgraph_reviewer_topology_eval.py = pass
ruff check src tests scripts = pass
git diff --check = pass（仅既有 LF/CRLF 提示）
```

超过 60 秒的完整 fake pytest 节点未计入通过。单元测试拆分后取得明确终态；12-case 全量
readiness 改为独立脚本运行。

## 5. Fake Readiness R6

```text
session =
gate55-fake-readiness-r6-20260718

decision = dry-run-passed
case count = 12
topology block count = 36
planned reviewer sessions = 72
real provider sessions = 0
failures = []
```

该 fake readiness 在提交 `private-gate-5-pass-redacted` 上完成，
只证明当前实现的确定性准备链路通过；它不替代后续冻结 tag 上的唯一真实 preflight
和正式 Gate 5.5。

安全审计：

```text
reviewer context leak = 0
workspace writes by reviewer = 0
ground truth prompt leak = 0
cross-topology output leak = 0
cross-replicate output leak = 0
aggregate reconstruction mismatch = 0
```

R1-R4 使用旧 commitment 或早期实现，只保留为开发过程记录，不进入真实准入证据。

## 6. 已关闭复审风险

- 真实 model、provider、auth、Codex version、timeout 和 90 次预算不能由 CLI 改写；
- execution baseline 必须绑定 `gate-5.5-pre-run-v1`；
- fixture 路径、Git commit 和 Reviewer 可见 case id 全部使用 `case-01..12`；
- 最终 Runner prompt 会检查 evaluator id、canonical rule id 和 cross-canary；
- process output 会审计 ground-truth 路径、合同路径和原始 evaluator case id；
- ground truth 显式覆盖全部 12 case、expected verdict 和 false-blocker 条件；
- scorer 使用 exact-first、预注册 alias、severity range 和无歧义匹配；
- aggregate 必须从结构化 result 确定性重建；
- clean false-major、fixed-three comparison 方向和预算不足规则已进入回归；
- provider/preflight 异常会停止新 session，并原子发布 `blocked` summary/report；
- 初始 topology 成本与 replicate 成本分开记录。

## 7. 残余边界

当前 Windows `read-only` sandbox 不是同一用户下的 OS 级读取保密边界。本实验通过以下方式
实现可审计盲评：

- ground truth 不进入公共 evidence、role prompt 或 run artifact；
- Reviewer 明确只能使用公共 evidence；
- 最终 prompt 执行 DLP；
- fixture Git 历史和路径使用中性 identity；
- process output 检查私有路径和原始 evaluator identity；
- 发现访问痕迹立即 `fail`。

因此本 Gate 可以评估按合同执行的真实 Reviewer topology 收益，但不证明能够隔离主动恶意
枚举宿主文件系统的 Reviewer。最终结果必须保留该限制。

## 8. 启动条件

只有以下条件同时成立才允许真实 preflight：

```text
current branch = experiment/langgraph-comparison
worktree = clean
HEAD = gate-5.5-pre-run-v1^{commit}
dataset / ground truth SHA = frozen values
Codex CLI = 0.144.5
auth = chatgpt
provider / model = sandboxproxy / sandbox-model
reviewer sessions = 0
active Gate 5.5 process = 0
```

本文不表示 preflight 或真实评测已经通过，只表示可以在 tagged clean baseline 上启动唯一一次
preflight。
