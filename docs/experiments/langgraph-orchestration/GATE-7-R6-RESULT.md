# Gate 7 R6 API-key 真实执行结果

> 结果：`failed-at-cp01-transcript-budget`
>
> 执行日期：`2026-07-20（星期一）`
>
> 执行窗口：`16:25:24 - 16:35:38`
>
> 时区：`Asia/Shanghai`
>
> R6 baseline：`private-gate-7-r6-baseline-redacted`

## 1. 最终结论

R6 修复了 R5 暴露的远端 Git 控制面问题，但没有完成 Gate 7A 大任务。

```text
remote consumed tag control = passed
API-key auth = passed
Provider / model identity = passed
Machine E CP01 worker process = completed, returncode 0
CP01 transcript audit = failed
Gate 7A = failed
Gate 7C = not started
overall = failed-at-cp01-transcript-budget
```

CP01 的真实模型会话已经产生预期范围内的工作区副作用，但 transcript 和 token 同时超过
预注册上限。Harness 按合同写入 `checkpoint_failed` 后停止，没有把该工作区状态接受为
checkpoint，也没有进入 CP02、handoff、Machine F 或 Gate 7C。

R6 consumed tag 已经推送到远端，因此不得删除、移动、复用或重跑 R6。

## 2. 固定身份

```text
case SHA-256 =
b8475f796f9ec8bac1c51eee9f1d30975e00c5b4e70859933f158663867b3f8d
plan SHA-256 =
c0e372e5c56d6a322882ff147cee0e7e890bc4f9d20654fd18bb074f34ee8ddf

provider = sandboxproxy
base URL = http://127.0.0.1:18080/v1
wire API = responses
model = sandbox-model
reasoning = high
auth = api-key
Codex CLI = 0.144.5
multi_agent = disabled
request / stream retry = 0
```

```text
baseline A = gate-7a-pre-run-r6-v1
consumed A = gate-7a-consumed-r6-v1
baseline C = gate-7c-langgraph-pre-run-r6-v1
consumed C = absent

baseline A local/remote peel =
private-gate-7-r6-baseline-redacted
consumed A local/remote peel =
private-gate-7-r6-baseline-redacted
baseline C local/remote peel =
private-gate-7-r6-baseline-redacted
```

## 3. 实际执行顺序

```text
arm_started
-> authority_claimed
-> consumed A 本地 annotated tag 创建
-> consumed A 推送成功
-> remote peel 复核成功
-> Machine E CP01 checkpoint_started
-> Codex worker completed, returncode 0
-> transcript parser 完整解析
-> checkpoint_failed
-> Gate 7A terminal failed
```

Coordinator event chain：

```text
arm_started
-> authority_claimed
```

Machine E event chain：

```text
checkpoint_started
-> checkpoint_failed
```

两条 event hash chain 均已重新读取并验证通过。没有 `checkpoint_completed`、
`planned_migration_accepted` 或 `arm_completed`。

## 4. 决定性预算证据

| 指标 | 上限 | 实际 | 结果 |
| --- | ---: | ---: | --- |
| tool waves | 2 | 2 | pass |
| exec commands | 8 | 8 | pass |
| 单命令输出字节 | 8,192 | 1,450 | pass |
| 累计命令输出字节 | 32,768 | 6,400 | pass |
| duplicate commands | 0 | 0 | pass |
| unbounded reads | 0 | 0 | pass |
| transcript bytes | 65,536 | 89,908 | **fail** |
| tokens used | 45,000 | 53,469 | **fail** |

```text
transcript parser = gate7-r4-v1
parse_complete = true
transcript audit status = failed
runner status = completed
runner returncode = 0
```

这说明失败不是“命令无限读取”“重复探索”或“Provider 没有返回”。现有证据只能确定总
transcript 与 token 超限，不能仅凭 audit 把 token 精确拆分为输入、内部推理和最终生成
各自的贡献。

## 5. Provider 预算与重复执行

```text
preflight provider sessions = 0
real provider sessions started = 1
worker process starts = 1
automatic retry starts = 0
CP02 sessions = 0
CP03 sessions = 0
Machine F starts = 0
Gate 7C sessions = 0
```

唯一 execution artifact 状态为 `completed`，child `returncode=0`。失败发生在 worker
返回之后的确定性 transcript audit，不得解释为 Provider transport、API-key 或模型不可用。

## 6. 工作区副作用边界

CP01 worker 在 fixture repo 中留下未接受的工作区改动：

```text
HEAD = 7b0088693ece1bd3a9238a6fdf56ed8df7a4d43b
commit created = false
remote CP01 ref created = false
untracked files = 0
changed files = 5
diff lines = 106 additions
```

变更文件与 CP01 冻结 scope 完全一致：

```text
tests/test_appctx.py
tests/test_basic.py
tests/test_blueprints.py
tests/test_helpers.py
tests/test_testing.py
```

```text
workspace diff bytes = 5445
workspace diff SHA-256 =
713067bf6c2cc7b4d90a65d535658425c220285ccc8ec75b371ac6370cb04fff
```

由于 transcript audit 先于 checkpoint verification，R6 没有运行 harness 的 CP01
`robust regression` 与 remaining suite 验证。不能把 worker 的 `returncode=0` 写成
“CP01 业务验证通过”。

## 7. 安全只读复核

停止后只读取 canonical artifacts、五个变更文件和 event ledger：

```text
provider header = passed
runner output safety = passed
coordinator event chain = passed
Machine E event chain = passed
canary hit count = 0
sensitive material hit count = 0
files scanned = 13
```

这些是 post-stop 只读复核，不会反向把失败 session 改写为成功，也不替代未执行的
checkpoint verification。

## 8. 本地终态证据

```text
<short-checkout>\.local-validation\gate-7\gate7a-flask-5928-real-r6-v1\
  terminal-state.json
  coordinator-events.jsonl
  preflight-machine-e.json
  machine-e\events.jsonl
  machine-e\executions\cp01\execution.json
  machine-e\executions\cp01\process-output.txt
  machine-e\executions\cp01\transcript-audit.json
```

该目录属于本机忽略产物，不随 Git 分发。跨机器交接时，以远端 baseline/consumed tags、
本文件记录的 artifact hash 和最终决策文档为可携带复核入口；不得把缺少本地原始 artifact
误写成实验已在另一台机器重放。

```text
terminal-state.json SHA-256 =
85e0dcd0e4e58d8d86217d6ab78bf86f19b18aee9d9272365f68700ba585337e
coordinator-events.jsonl SHA-256 =
b004355f85f09bee4fb1860386289ba6b4c19982204190ce80bab04a0e95eae2
machine-e/events.jsonl SHA-256 =
25a6994e56be0c0f9863fb3ed3e637337f42ff44310cc42c2c00e7dd7672a2d0
execution.json SHA-256 =
44179134e187c8ef041ccebd7c235010f81a1813df35ead418ffb97fcebfb626
process-output.txt SHA-256 =
1a77c891c3a448d13be0fb70e0475217cf31ce8e29a68b67a93e90e5a8a77a2b
transcript-audit.json SHA-256 =
79d9f6a6b95dad438a629370c54754abab397a1c9fc2a92c1ddfcc8401d23dd8
```

这些运行证据位于忽略目录，不进入 Git。API key 值没有写入项目源码、文档或已复核的
实验 artifact。

`terminal-state.json` 已成功保留 Machine E 的 Python traceback，但 Windows 子进程
使用本地代码页输出的中文异常文本被父进程按 UTF-8 解码，局部显示为乱码。决定性数值
来自 UTF-8 的 `transcript-audit.json`，因此不影响本次结论；强制内部控制进程使用
UTF-8 仍是一项后续 observability 技术债。

## 9. 架构结论

R6 得到了三个独立结论：

1. **R5 控制面缺陷已关闭。** consumed tag 创建、推送和远端 peel 复核均成功，执行进入
   真实 worker。
2. **API-key 路径可用。** Provider、模型和 CP01 Codex worker 正常完成，不是认证失败。
3. **当前大任务合同未通过。** `sandbox-model + high` 在冻结的 CP01 上超过
   `65,536 transcript bytes / 45,000 tokens`，因此 Gate 7A 失败，Gate 7C 无资格启动。

所以 R6 不能给出 Linear / LangGraph 的真实大任务等价性结论，也不能用于提升默认引擎。
它证明的是安全停止线真实生效：即使模型进程成功并修改了工作区，只要证据预算越界，
系统也不会接受 checkpoint、继续长任务或启动对照 arm。

## 10. 后续停止线

- 不删除、移动或复用 R6 baseline / consumed tag；
- 不重跑 `gate7a-flask-5928-real-r6-v1`；
- 不启动 R6 Gate 7C；
- 不通过事后提高阈值把 R6 改写为成功；
- 如果继续，必须创建独立 R7，并只预注册一个新的实验变量。

R7 前应先用现有 transcript 做离线分解，明确下一轮优先回答哪一个问题：

```text
A. 保持严格证据预算，进一步压缩任务、prompt 或模型行为；
B. 保持当前真实大任务，显式提高 transcript/token 预算并接受更高成本；
C. 将部分探索或测试生成改为确定性步骤，降低外部 Agent 的自治范围。
```

这三种方案回答的问题不同，不能在同一 revision 中同时修改后再宣称因果成立。
