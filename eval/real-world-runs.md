# 真实任务运行记录（Real-world runs）

在公开开源项目的真实 Issue 上运行 Vega 的记录。目的不是刷成功率，而是验证核心语义在
真实仓库上是否成立：worker 与 reviewer 上下文隔离、确定性验证终态确认、证据不足时 fail-closed。

## 协议

- **预注册**：运行前登记目标 Issue 与验证要求，不换题、不挑成绩重跑。
- **oracle 封存**：对有官方修复的 Issue，执行前封存官方修复（oracle），运行期间只保留哈希，
  运行结束后才 materialize 对比。
- **隔离**：worker 在可写沙箱中修改，reviewer 在只读上下文中仅依据 diff 与测试证据评审，
  不接触 worker 的对话内容。
- **fail-closed**：验证失败、基础设施受阻或证据不足时停止自动执行，保留现场交还人工。

## 运行记录

| Issue | 仓库规模 | 结果 |
|---|---|---|
| [pycodestyle #1072](https://github.com/PyCQA/pycodestyle/issues/1072) | 小型 | ✅ 单轮通过；diff 1 增 2 删，与官方修复（PR #1073）逐字节一致（blob 哈希相同）。全程约 222 秒 |
| [attrs #1085](https://github.com/python-attrs/attrs/issues/1085) | 中型 | ✅ 通过验证与隔离审查，完成闭环 |
| [CPython #82369](https://github.com/python/cpython/issues/82369) | 6000+ 文件 | ✅ 在超大仓库上完成修改、验证与隔离审查的完整闭环 |
| [Django #33368](https://code.djangoproject.com/ticket/33368) | 大型 | ⛔ 定位正确，Windows sandbox 阻止写入 → **fail-closed 安全停止**，现场保留交还人工 |

fail-closed 的记录与成功记录同等保留：它验证的是"证据不足或基础设施受阻时不硬跑"这条
语义真的会触发，而不是仅停留在设计文档里。

## 这些运行不能证明什么

- 样本量小且经过选择，**不构成成功率统计**，不应从表格推导出任何百分比。
- pycodestyle 一例与官方修复逐字节一致，**不能排除该修复存在于模型训练数据中的可能**；
  多仓库运行降低了单点记忆效应的解释力，但没有消除它。
- 未覆盖长周期任务、request_changes → fix 多轮循环的全部路径，以及高频日用下的 token 成本。

## 产物

每次运行的 state、trace、验证输出与审查报告按 [ARCHITECTURE](../docs/ARCHITECTURE.md)
所述结构保留在本地运行档案中，包含各阶段产物哈希，可按需提供复核。

## 2026-07-25 追加记录：pycodestyle #1187 的 reviewer-repair 对照

该条目补充了一对同一真实 Issue 的脱敏证据，不改写上表中的历史记录：

- 初始 auto run 的固定测试通过，但 worker 的最小删减修复会让普通小写变量比较产生误报；
  初始 worker 阶段曾受外部 runner 中断影响，后续人工 continue 进入验证与审查；隔离 reviewer
  返回 `request_changes`，run 保持 `needs_human`，没有自动提交或覆盖现场。
- 后续使用**新的隔离快照和预先写明的 follow-up 行为合同**，将 reviewer 指出的正反例纳入
  验证后重新运行。该 run 一轮通过完整测试、定向 oracle、三阶段 scope gate、risk gate 和
  隔离 reviewer。
- 两个结果的静态、脱敏摘要见
  [examples/evidence/real-world-pycodestyle-1187](../examples/evidence/real-world-pycodestyle-1187/)。

这是一对“reviewer 拒绝后修订合同”的案例，不是原始任务无条件一次成功的证明，也不用于计算
成功率。它补充的是 reviewer 能发现测试遗漏、拒绝不安全 diff，以及新合同下可完成闭环的证据。

## 2026-07-25 预注册：Click #2939（未执行）

这一条在执行前冻结下一次真实 Issue 验证的边界，不代表已经产生成功或失败结果：

- 目标：`pallets/click` Issue #2939，修复 `CliRunner` 对 stdin 文件迭代的 EOF 回归。
- 基线：Click `8.2.1`，源码修订 `fd183b2ced1cb5857784fe7fb22f4982f671f098`。
- 已确认的基线行为：链式命令通过 `click.File("r")` 读取 `-` 输入的一行内容后错误退出，
  返回非零状态并出现 `Aborted!`。
- 封存 oracle：上游修复修订 `93c6966eb3a575c2b600434d1cc9f4b3aee505ac` 已在独立参考缓存中
  记录哈希；正式执行副本不保留远端、官方修复提交或其 diff。
- 允许修改：仅 `src/click/testing.py` 与 `tests/test_chain.py`。
- 行为合同：链式 stdin 文件输入应正常消费最后一行并以零状态结束；交互式 prompt 在真正 EOF 时仍应
  保持现有的终止语义。
- 验证：定向 `tests/test_chain.py`、完整 pytest、独立的 stdin 链式迭代 oracle、`git diff --check`
  与路径范围门禁。
- 结论规则：任一验证、证据完整性、范围门禁或隔离 reviewer 不通过，即保留现场并以
  `needs_human` 或更严格状态结束；不自动提交、push 或发布。

## 2026-07-25 执行结果：Click #2939

本条是上方预注册的对应执行结果，不回写或修改预注册条件：

- 真实 auto run：`20260725-204241-079497-bug-loop`；worker 与隔离 reviewer 均正常结束。
- 一轮完成：worker 只改动 `src/click/testing.py` 与 `tests/test_chain.py`；三阶段 scope gate
  均通过，risk 为 `low`，reviewer verdict 为 `approve`。
- 四条验证命令均通过：定向链式测试、独立 stdin 迭代 oracle、完整 pytest
  （`688 passed, 72 skipped, 1 xfailed`）与 `git diff --check`。
- 独立 oracle 还复核了 prompt 在真实 EOF 时继续走既有中止语义，避免把修复退化为简单取消
  EOF 处理。
- Finish 结果为 `ready_to_commit`；目标执行副本没有 remote，Vega 没有自动 commit、push、
  release 或写入 memory。

该样例的脱敏状态、trace、diff、验证、gate、review 与 finish 摘要见
[examples/evidence/real-world-click-2939](../examples/evidence/real-world-click-2939/)。
这是一个历史回归上的单次真实闭环，不构成模型成功率、跨仓库泛化能力或独立于训练数据的证明。

## 2026-07-26 公开证据范围说明

上述 pycodestyle #1187 与 Click #2939 目录是核心阶段的脱敏摘要，不是原始运行目录的完整副本。
公开包没有包含原始 `eval.md`、`finish-summary.json` 或 `finish-report.md`，因此第三方可以检查
状态、trace 摘录、diff、验证摘要、范围/risk gate 与 reviewer verdict，但不能仅凭公开包
独立重建完整 Finish/Eval 判断。

pycodestyle #1187 follow-up 的“执行前冻结合同”来自本地运行记录；当前公开 Git 历史同时引入
合同与结果，不能独立证明两者的先后顺序。Click #2939 则保留了先预注册、后追加结果的两个
独立提交，可通过公开提交顺序核对。

## 2026-07-26 主线自举验证：`--repo` 普通文件拒绝

该条目记录 Vega 使用当前主线 runtime 审查旧版 Vega 修复的 assist Dogfood，不计入真实 Issue
成功率，也不改写此前路径过长、验证超时和人工停止的负结果。

- 最终 run：`20260726-163845-755884-bug-loop`。
- 目标 Bug 基线：`80ece20`；运行前加入独立的 Windows `taskkill` 输出解码修复
  `82baf94`，但不包含待验证的 `--repo` 修复。
- 最终 run 复用了此前 worker 已生成的同一补丁，因此 `worker_status=skipped`。该结果验证的是
  assist 接管、验证、门禁、隔离 reviewer 和 Finish，不是一次新的模型生成能力样本。
- 补丁只修改 `src/vega/cli.py`、`src/vega/cli_support.py` 和
  `tests/test_cli_recovery_hardening.py`；pre-verification、post-verification 和 pre-review
  三阶段 scope gate 均通过。
- 10 条预注册验证命令全部通过：5 个 pytest 分片合计
  `714 passed, 5 skipped`，另有 `compileall`、仓库卫生检查、Ruff 和
  `git diff --check` 通过。
- Windows 本机的 pytest cache provider 会在 session finish 阶段阻塞；本次 fixture 通过
  `PYTEST_ADDOPTS=-p no:cacheprovider` 让嵌套 pytest 同样禁用缓存，并将单命令超时预先设置为
  900 秒。测试文件覆盖保持完整，没有删减用例。
- 主命令按约 25 秒间隔持续输出 verification/reviewer 进度；长分片没有再表现为无反馈等待。
- risk gate 判定为 `low / self-check`；独立只读 reviewer 返回 `approve` 且 findings 为 0。
  reviewer 前后的可信工作区指纹一致，Finish 状态为 `ready_to_commit`。
- 两个测试分片仍记录到 10 条 `PytestUnhandledThreadExceptionWarning`，来源是其他 Windows
  子进程文本解码路径收到非 UTF-8 输出。它们未改变退出码或 reviewer 结论，但属于后续应独立
  修复的兼容性风险，不能被本次成功状态掩盖。

该样例证明当前主线能够在 Windows 慢测试环境中完成有进度、可追溯、fail-closed 的
assist 验证与隔离审查闭环。它不能证明新 worker 每次都能生成正确补丁，也不能证明所有
Windows 子进程解码问题已经解决。
