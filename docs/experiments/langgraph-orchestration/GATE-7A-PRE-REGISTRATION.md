# Gate 7A Linear + Goal/Handoff 预注册合同

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
> 真实 provider 调用：`0`

---

## 1. 唯一问题

Gate 7A 只回答：

```text
在冻结 case 下，linear + Goal/Handoff 能否把 CP01 / CP02 / CP03 按预注册顺序推进，
并在单宿主机双节点模拟中保持文件白名单、hash 链、行为验证和终止条件完全一致？
```

不回答：

- LangGraph 是否更好；
- 自动拆解是否更强；
- gold diff 是否足够；
- 真实物理换机是否发生；
- provider 模型调参是否能扩大范围。

---

## 2. Case Identity

```text
schema_version = 1
case_id = gate7-flask-teardown-goal-handoff-v1
case sha256 = 9dcb5e157892b0bf0434c220366b52cb7da7a8789d8a163d940be46d4d36bdd9
plan sha256 = ad521158f5fbd13317dcee3bbe3378499e22866c4b1ae298966cb4f0f162257f
real_session = gate7a-flask-5928-real-v1
langgraph_session = gate7c-flask-5928-real-v1
pre_run_name = gate-7a-pre-run-v1
consumed_name = gate-7a-consumed-v1
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
```

---

## 3. 精确文件白名单

本 Gate 的最终精确 10 文件白名单如下，任何增删改都算 scope drift：

```text
tests/test_appctx.py
tests/test_basic.py
tests/test_blueprints.py
tests/test_helpers.py
tests/test_testing.py
src/flask/helpers.py
src/flask/app.py
src/flask/ctx.py
docs/appcontext.rst
CHANGES.rst
```

---

## 4. 三个 checkpoint

### 4.1 CP01

```text
scope = tests/test_appctx.py tests/test_basic.py tests/test_blueprints.py tests/test_helpers.py tests/test_testing.py
goal = 只改测试期望，不碰生产代码
expected state = robust 节点单跑时 test_robust_teardown 预期失败
```

CP01 的验收必须同时满足：

- 只改 5 个测试文件；
- 机器必须运行两个独立 pytest 进程；
- 进程 A 只跑 `tests/test_appctx.py::test_robust_teardown`，且必须 expected-fail；
- 进程 B 使用 `-k "not test_robust_teardown"` 跑其余 suite，且必须 `494 passed`；
- 不引入新依赖；
- 不借助 gold diff 代替测试；
- 不修改任何非白名单文件。

CP01 的人工计划还明确给出：新增 `test_robust_teardown` 覆盖四类 teardown
callback/signal，并把其余测试中的 response、stream、builder 改为确定性资源关闭。
它只提供阶段职责，不提供生产实现答案。

### 4.2 CP02

```text
scope = src/flask/helpers.py src/flask/app.py
goal = 直接修复 request/app teardown callbacks + signals 行为
expected state = robust 节点单跑时 test_robust_teardown 预期失败
```

CP02 的验收必须同时满足：

- 只改 2 个生产文件；
- request / app teardown callbacks 与 signals 全调用；
- 错误统一抛出；
- 机器必须运行两个独立 pytest 进程；
- 进程 A 只跑 `tests/test_appctx.py::test_robust_teardown`，且必须 expected-fail；
- 进程 B 使用 `-k "not test_robust_teardown"` 跑其余 suite，且必须 `494 passed`；
- CP02 另有直接行为 probe，且必须 pass；
- 不提前触碰 `ctx.py`、`appcontext.rst` 或 `CHANGES.rst`；
- 只允许 Git refs 与 sealed handoff bundle 在 `machine-e` 与后续 `machine-f` 之间传递。

CP02 的人工计划要求实现通用内部错误收集，并让 request/app 两层 callback 与 signal
在前序抛错时仍全部执行；不指定类名、具体控制流或 oracle patch。

### 4.3 CP03

```text
scope = src/flask/ctx.py docs/appcontext.rst CHANGES.rst
goal = 冻结文档与上下文收口
expected state = robust 节点 pass，然后完整 suite 全通过
```

CP03 的验收必须同时满足：

- 只改 3 个文件；
- 先单独运行 robust 节点且必须 pass；
- 再运行完整 suite 且必须 `495 passed`；
- 文档与实现一致；
- 不新增任何额外 scope。

CP03 的人工计划要求 `AppContext.pop` 完成 request teardown、request close、app teardown、
context reset 与 `appcontext_popped` 的跨层收口，并同步文档和变更记录。

---

## 5. 验证合同

### 5.1 预期验证顺序

1. 先校验 frozen case JSON 可解析；
2. 再校验 scope 是否为 exact paths；
3. 每个 checkpoint 前后对 scope 外累计 diff 做二进制摘要，禁止后续阶段回改前序文件；
4. 再校验事件 hash chain 与 attempt / retry / recovery / migration 字段；
5. 再跑 CP01 的两个独立 pytest 进程；
6. 再跑 CP02 的两个独立 pytest 进程和 direct behavior probe；
7. 再跑 CP03 的 robust 节点和完整 suite；
8. 最终 tree、canonical diff bytes 与 SHA 必须等于冻结身份；
9. 最后做 `git diff --check`。

### 5.2 完整性规则

- 任何失败都必须落入 terminal state；
- 不允许自动重试；
- 不允许把失败转换为成功；
- 不允许靠回滚他人改动清空冲突；
- 不允许使用未授权路径作为中转目录；
- 不允许把线性流程伪装成多节点真实迁移。
- 真实执行必须从远端 baseline tag 新建严格干净的 control clone；
- 父工作树中的用户 `uv.lock` 不进入 baseline，也不是 clean-check 例外；
- consumed tag 只允许在 fixture、依赖、base suite、CLI/auth/loopback 检查完成后，
  且即将启动第一个真实 worker 前创建并推送；
- Linear cursor 必须保存 CP01 / CP02 的 checkpoint evidence hash，供 Machine F 复核。

---

## 6. Provider 与运行预算

```text
provider = sandboxproxy
base_url = http://127.0.0.1:18080/v1
wire_api = responses
model = sandbox-model
reasoning = high
auth = chatgpt
session = ephemeral
memory = off
automatic retries = 0
request_max_retries = 0
stream_max_retries = 0
preflight session = 0
fresh provider sessions per arm = 3
```

每个 arm 都必须使用 3 个 fresh provider sessions，不允许 provider preflight session。
`provider preflight session = 0` 表示 readiness 只做 CLI、auth 和 loopback TCP 检查，
不额外消耗一次模型会话。

---

## 7. 拓扑与命名

```text
topology A = linear + Goal/Handoff
topology C = LangGraph compare
```

命名固定为：

- `gate-7a-pre-run-v1`
- `gate-7a-consumed-v1`
- `gate-7c-langgraph-pre-run-v1`
- `gate-7c-langgraph-consumed-v1`
- `gate7a-flask-5928-real-v1`
- `gate7c-flask-5928-real-v1`

Gate 7C 只有在 Gate 7A 成功后才允许启动。

---

## 8. 事件与权限

必须复核并记录：

- event hash chain；
- attempt 字段；
- retry 字段；
- recovery 字段；
- migration 字段；
- shared authority claim 唯一；
- source chat canary = 0；
- memory canary = 0；
- machine path canary = 0；
- Authorization bearer / API key 形态命中 = 0；
- artifact 扫描文件数与跳过文件数必须进入机器 summary；
- `machine-f` 不读取 `machine-e` 的路径事实，只读 refs + sealed bundle。

---

## 9. 停止条件

一旦出现以下任一项，必须立即停止：

- 任一白名单外文件被改动；
- JSON 解析失败；
- scope drift；
- diff budget 不一致；
- 后续 checkpoint 修改前序 checkpoint 已形成的累计 diff；
- 最终 tree、canonical diff bytes 或 diff SHA 与冻结身份不一致；
- hash chain 断裂；
- 自动重试不为 0；
- request / stream retry 配置不为 0；
- artifact 扫描发现 canary、Authorization bearer 或 API key 形态；
- 需要回滚他人改动才能继续；
- 任何完整测试结果不是明确 passed；
- 试图把人工预注册改写成 Agent 产出；
- 试图声称真实物理换机；
- 试图让 gold diff 代替行为验证。
