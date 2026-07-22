# Gate 7 协议附录

> 文档状态：`frozen-before-run`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`
>
> 真实任务：`pallets/flask PR #5928`
>
> 本附录只冻结 Gate 7 的协议边界，不扩展新的产品承诺，也不替代 Gate 7A / Gate 7C 的预注册合同。

---

## 1. 冻结目标

Gate 7 只回答一个问题：

```text
在同一个真实任务上，先用 linear + Goal/Handoff 完成冻结 case 的预注册、接力和验证，
再在成功条件满足后，触发 Gate 7C 的 LangGraph 对照，是否能保持相同的事实边界、停止条件和证据链？
```

本 Gate 明确不回答：

- 是否把 LangGraph 变成默认产品引擎；
- 是否把自动拆解升级为 Agent 产出；
- 是否允许真实物理换机叙事；
- 是否允许 gold diff 直接替代项目测试与行为验证；
- 是否允许 checkpoint 覆盖人工预注册输入。

---

## 2. 冻结原则

### 2.1 人工预注册优先

checkpoint 必须由人工预注册给出，属于输入，不是 Agent 产出。

自动拆解属于另一个实验变量，不能混入 Gate 7A 的 linear + Goal/Handoff 路径。

### 2.2 线性先行，成功后才触发对照

Gate 7A 只允许：

- linear 执行；
- Goal / Handoff 接力；
- 单宿主机双节点模拟；
- 只读 Git refs 与 sealed handoff bundle 交换。

Gate 7C 只有在 Gate 7A 成功后才可触发，且只作为 LangGraph 对照，不得回写 Gate 7A 事实。

Gate 7C 的 `CP01 -> CP02 -> CP03` 必须是实际持久化的 LangGraph 节点：

- Machine E 执行 CP01、CP02；
- SQLite checkpointer 在 CP02 后中断；
- Machine F 打开同一个 checkpoint，只恢复并执行 CP03；
- `resume_external_attempts = 0` 与 `replayed_external_attempts = 0`；
- CP03 是恢复后的唯一新 external attempt。

### 2.3 单宿主机双节点模拟

允许的模拟方式只有：

- 本地 bare remote 作为唯一 Git 交换面；
- `machine-e` 使用独立 clone 和独立控制进程；
- `CP02` 后按计划迁移到 `machine-f` 的 fresh clone 和新控制进程；
- 只允许 Git refs、hash 和 sealed handoff bundle 传递。

禁止把这套安排描述成真实物理换机。

控制面还必须从已推送 baseline tag 建立严格干净的独立 clone。父工作树中的用户
`uv.lock` 不属于 baseline，也不能被写成 clean-check 例外。

### 2.4 gold diff 的作用边界

gold diff 只用于 worker 隔离与证据比对：

- 只保存元数据和 SHA；
- 不保存完整 oracle 文本；
- 不替代项目测试；
- 不替代行为验证；
- 不作为正确性的唯一来源。

最终 tree、canonical diff bytes 与 SHA 仍要与冻结 merge 身份一致，但它们只构成
“目标身份复核”；项目测试与 CP02 direct behavior probe 仍是独立且不可省略的正确性证据。

---

## 3. 真实事实锚点

```text
date = 2026-07-19
branch = experiment/langgraph-comparison
repo task = pallets/flask PR #5928
base = 7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
merge = c34d6e81fd8e405e6d4178bf24b364918811ef17
merge tree = a5b249e710d1253bee4c099faf91e45f9ebfbddd
changed files = 10
diff command = git diff --binary --full-index <base> <merge>
diff bytes = 19266
diff sha256 = d8e20d91ebe30ca5056be1b3e4d84d989dbba6fd2a16829baecf0620bdc4d33b
base full suite = 494 passed
merge full suite = 495 passed
Python = 3.12.11
uv = 0.10.10
case sha256 = 9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9
plan sha256 = ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f
```

这些事实是冻结输入，不可被 checkpoint、handoff、LangGraph cursor 或 reviewer 输出重写。

---

## 4. Gate 链路

### 4.1 Gate 7A

Gate 7A 的职责是把冻结 case 跑成可审计的 linear + Goal/Handoff 事实链。

Gate 7A 成功条件：

- three checkpoint contract 已预注册；
- 精确 10 文件白名单不漂移；
- CP01 / CP02 的 robust teardown 必须在独立 pytest 进程中 expected-fail；
- CP01 / CP02 的其余 suite 必须在新 pytest 进程中用 `-k "not test_robust_teardown"` 达到 `494 passed`；
- CP02 的 direct behavior probe 必须 pass；
- CP03 必须先让独立 robust 节点 pass，再让完整 suite 达到 `495 passed`；
- 机器边界、事件 hash chain、attempt / retry / recovery / migration 字段齐全；
- 自动重试保持 `0`；
- Codex provider `request_max_retries = 0` 且 `stream_max_retries = 0`；
- shared authority claim 唯一；
- source chat、memory、machine path canary leak = `0`；
- Authorization bearer / API key 形态命中 = `0`；
- commit / tree / ref / handoff / plan / case hash 全复核通过；
- 最终 tree 与 canonical diff bytes / SHA 等于冻结身份；
- 失败均进入 terminal state，不伪装成通过。

### 4.2 Gate 7C

Gate 7C 只在 Gate 7A 成功后触发。

它的唯一职责是：

- 用 LangGraph 对照 Gate 7A 的线性合同；
- 不扩大文件范围；
- 不改变 checkpoint 定义；
- 不改变停止条件；
- 不改变真实任务事实。
- 不把 cursor round-trip 当成 LangGraph 编排；真实 checkpoint 必须由持久化图节点推进。

---

## 5. 终止条件

任一情况出现时必须停在 terminal state：

- scope 不是 exact paths；
- diff budget 或 SHA 不匹配；
- 最终 tree、canonical diff bytes 或 SHA 不匹配；
- event hash chain 缺失；
- attempt / retry / recovery / migration 字段不完整；
- 自动重试不为 `0`；
- request / stream retry 配置不为 `0`；
- source chat 或 memory 泄漏；
- machine path canary 泄漏；
- artifact 扫描发现 Authorization bearer 或 API key 形态；
- 试图把人工预注册改写成 Agent 产出；
- 试图宣称真实物理换机；
- 试图让 gold diff 代替行为验证；
- 任何完整测试结果不是明确 `passed`。

---

## 6. 交付边界

本附录只负责说明：

- 为什么 Gate 7A 是 linear + Goal/Handoff；
- 为什么 Gate 7C 必须等 Gate 7A 成功；
- 为什么双节点模拟只能靠 refs + sealed bundle；
- 为什么正确性仍然依赖项目测试与行为验证。

具体运行合同由 `GATE-7A-PRE-REGISTRATION.md` 和 `eval/gate-7/flask-teardown-case.json` 冻结。
