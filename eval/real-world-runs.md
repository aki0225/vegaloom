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

## 2026-07-28 预注册：Core Real-World Pilot v1（未执行）

本条在任何正式 worker 运行前冻结三项真实 Issue 和固定顺序，不代表已经产生结果：

1. `CRWP-V1-01`：`BitMiracle-AI/Dormice#33`，基线
   `f26ba3748e79c7225f4aafb757c6f9f1f6b2d733`，验证 CLI timeout 的正整数输入边界；
2. `CRWP-V1-02`：`sequelize/sequelize#18265`，基线
   `f0cea95e38b4f2c9096267371ab305d08f7b8497`，验证 SQLite `sync({ alter: true })`
   重建表后仍保留 `AUTOINCREMENT`；
3. `CRWP-V1-03`：`openstates/jurisdictions#122`，基线
   `6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705`，验证 ancestor stub 文件名与模型 UUID
   一致。

详细任务合同、允许路径、独立 oracle、验证命令、成本口径、迭代预算和停止条件见
[`docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md`](../docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md)。

其中 Sequelize 项在冻结前已有公开 PR `#18274`，因此明确记录为 controlled public replay；
PR 与 diff 不进入 worker/reviewer 输入，只能在终态后由控制端比较，不能表述为独立发现。
Dormice 与 OpenStates 虽未发现公开修复 PR，Issue 正文也已给出实现线索，同样不是盲测。

首轮明确不覆盖并发、重试或分布式副作用；三个筛选样本只记录原始结果，不计算成功率。
Issue 或已冻结公开 PR 的资格变化、基线不能复现、验证或证据失败、路径越界、Provider 错误
和 timeout 都必须保留并 fail-closed，不换题、不挑成绩重跑，也不自动 commit、push 或发布。

## 2026-07-28 Amendment：CRWP-V1-01 测试文件路径（未执行）

本修订发生在任何正式 worker 启动之前。Dormice 冻结修订没有
`packages/cli/src/main.test.ts`，而当前 Runtime 会在 worker 新建未跟踪文件后于 verification
和 reviewer 之前 fail-closed；预先加入空测试文件又违反“准备提交只加入 `.vega.yaml`”的
合同。

因此允许测试路径改为已有的 `packages/cli/src/commands.test.ts`，`max_new_files` 改为 `0`。
测试仍必须直接覆盖 `main.ts` 的 Commander 解析边界，并证明无效 timeout 不会调用
`clientFromEnv` 或 `sandboxExec`。其他 Issue、oracle、验证命令和结果判定不变。

## 2026-07-28 Amendment：CRWP-V1-02 禁用 Nx daemon（未执行）

本修订发生在任何正式 worker 启动之前。原第一条命令 `corepack yarn build` 虽返回 `0`，
仍在目标仓库下留下 Nx daemon；控制端已用 `corepack yarn nx reset` 显式清理。

最终第一条验证命令改为 `set "NX_DAEMON=false" && corepack yarn build`。命令数、超时和
构建范围不变，原命令结果只作为控制端诊断，不计入最终基线。更新后必须重新执行并确认没有
遗留目标相关进程。

## 2026-07-28 执行登记：CRWP-V1 前置检查阻断

三个目标副本、最终 `.vega.yaml`、控制 oracle 和依赖环境已经准备完成。三次
`config check` 均通过，最终配置中的非 oracle 命令全部通过；三个 oracle 都在独立进程中
连续两次稳定以 `1` 退出，决定性输出字节一致。登记审查随后发现三个 oracle 仍有合同覆盖
缺口：Dormice 默认 timeout 未校验完整请求字段，Sequelize 未建立真实 SQL 注释负对照，
OpenStates 未证明首次幂等运行真实生成预期 YAML。因此这些结果只能作为待修订控制基线，
不能作为已经冻结的最终 oracle 证据。

正式 worker 没有启动。当前 Vega Runtime 无法为已安装依赖的目标仓库建立完整 ignored
工作区基线：

- Dormice 的 ignored 枚举超过固定 30 秒 Git 读取上限；
- Sequelize 与 OpenStates 的 ignored 文件数超过 4096 个元数据条目预算，
  `snapshot_workspace().capture_complete=false`。

因此本次只登记配置解析、非 oracle 命令、待修订控制基线和两个独立阻断，状态为
`registration-review-blocked / preflight-blocked / worker-not-started`。可信 workspace
baseline 尚未建立，也没有 worker 输出、reviewer verdict、Finish 结论或模型成败结果。
后续必须先补齐三个 oracle 合同并重新执行双次基线，同时修复并验证 Vega 的大型依赖目录
工作区清单策略；不能靠 `config check` 通过、删除依赖目录或放宽 fail-closed 语义绕过。

控制端在创建正式 run 前停止。Dormice 的 `snapshot_workspace()` 当前还会让
`subprocess.TimeoutExpired` 直接向上传播；若直接进入 auto loop，worker 不会被调用，但
run 可能停在 `running / current_step=worker`，不能把它表述为已经形成干净
`needs_human` 终态。

完整执行参数、哈希、基线命令、oracle 结果和停止依据见
[`docs/CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md`](../docs/CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md)。

## 2026-07-29 Control Amendment：CRWP-V1 oracle 合同完成

正式 Worker、Reviewer 和 Finish 仍未启动。本次只补齐三个独立 oracle 的控制合同并执行
双次基线。三份 oracle 均从 Git index 物化的 LF 字节执行，控制哈希与提交后内容一致：

- Dormice 两次均以 `1` 退出，stdout SHA-256 为
  `4308870971d831b3f42883a42ea06057da9267782574b278a527f614172095e1`；
- Sequelize 两次均以 `1` 退出，stdout SHA-256 为
  `0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b`；
- OpenStates 两次均以 `1` 退出，stdout SHA-256 为
  `e3cf488350744510cbe084c452e4dc4ce4a314724ea0d02910837084c6675f21`。

Sequelize oracle 只在真实 SQL 行注释负对照的 `describeTable()` 调用期间临时隔离冻结版本的
旧 `showConstraints()` 多行解析缺陷，并在 `finally` 恢复；AUTOINCREMENT metadata
待测路径与目标仓库均未修改。

Dormice 的一次并行控制尝试触发 `cli_timeout`，该无效现场被保留，随后在新目录串行执行两次
取得一致结果。OpenStates 的首次 index-byte 探测误用缺少目标依赖的系统 Python，该无效
现场也被保留；使用目标仓库既有 `.venv` Python 后双次结果一致。OpenStates 虽然 oracle
合同完整，但 PR `#125` 已触发
`eligibility-changed-before-run`，因此 Case 03 停止。后续只允许在重新完成 preflight 后运行
Case 01/02，不据此计算成功率。

## 2026-07-30 执行结果：CRWP-V1-01 Dormice #33

本条追加 Case 01 的首个正式 auto run，不修改此前预注册、Amendment 或前置阻断记录。

- Run：`20260730-223403-019133-bug-loop`。
- `gpt-5.4 / medium` Worker 正常退出，约耗时 `464` 秒，runner 报告
  `58,637` tokens。
- Worker 修改了预注册允许的 `packages/cli/src/main.ts` 和
  `packages/cli/src/commands.test.ts`，但同时新增未跟踪的
  `.pnpm-store/v11/index.db`。
- Workspace Gate 在 verification 和 reviewer 前拒绝现场，最终状态为
  `needs_human / workspace_check_failed`。没有运行 verification、Reflect、Reviewer
  或 Finish。
- 候选 diff 为 `344` 行新增、`210` 行删除，共 `554` 行，超过预注册的
  `max_diff_lines=350`。`main.ts` 还被整体重排为可导入的 `createProgram()`，因此即使
  人工清理缓存，候选仍不满足本题的小范围变更预算。
- 控制端没有删除缓存、修改候选或选择性重跑。目标现场继续保存在 ignored 本地目录，
  没有自动 commit、push 或 release。
- 最终 Worker prompt 在 `worker_started` 事件前已写入两份相同 artifact，SHA-256 为
  `bc045e73d25a921258dc952aec8fa85ca0e68d06ab567575e77cf030cb1586b9`。但该哈希在外部
  runner 启动后才由控制端读取并登记，构成输入哈希登记时序偏差；本结果不能描述为完全无
  协议偏差的正式样本。

该运行证明 Workspace Gate 能阻止带有额外未登记文件的 Worker 结果继续进入验证与审查。
它不证明候选代码正确，也不构成一次完成的 Coding Loop。Case 01 不按结果选择性重跑；
Case 02 在完成既定调用前负向输入扫描前不会启动。

## 2026-08-01 独立 fresh auto Dogfood：AnyIO #1231

本条是主线 `5ec575c` 上的独立能力验证，不属于 CRWP-V1，也不启动 Assurance Stage 4。
任务冻结在 AnyIO `2ba69a649011e4608ab9485a0dcfe72b6f956ecc`，正式运行前没有向
Worker 或 Reviewer 提供 Issue 评论、官方 PR、修复提交或补丁。Issue 正文和任务合同已经明确
期望行为，因此该案例不是盲目根因发现，也不能证明模型训练数据未见过上游修复。

- Run：`20260801-224148-327305-bug-loop`；Worker 与 Reviewer 均为
  `gpt-5.5 / xhigh`，最多两轮，实际一轮完成。
- Worker 耗时约 `380.6` 秒，Reviewer 耗时约 `51.7` 秒；runner 本次没有提供可核验的
  token usage，因此成本记为 `unavailable`，不按输出字符估算。
- Worker 只修改预注册的 `src/anyio/_backends/_trio.py`、`tests/test_taskgroups.py` 和
  `docs/versionhistory.rst`，共 `23` 行新增、`1` 行删除；没有新增文件、依赖或越界路径。
- 修复让 Trio `TaskHandle` 使用运行任务已经解析出的 `final_name`；回归测试同时覆盖默认名和
  显式自定义名，并核对 handle name、start value 与 repr。
- 五条验证命令全部通过：独立 asyncio/Trio oracle、相关测试
  （`24 passed, 486 deselected`）、完整 `test_taskgroups.py`
  （`496 passed, 10 skipped, 4 xfailed`）、Ruff 和 `git diff --check`。
- Workspace Gate、三阶段 Scope Gate、Risk Gate 均通过；risk 为 `low / self-check`。
  隔离只读 Reviewer 返回 `approve` 且 findings 为 `0`。
- Finish 为 `ready_to_commit`；artifact integrity 为 valid，review evidence freshness 为 fresh。
  Worker、Reviewer 与控制进程均已退出；60 个 run 文件的高置信凭证模式扫描为 `0`。
- 目标副本没有 remote；Vega 没有自动 commit、push、release 或写入长期 memory。

该案例证明当前主线能在一个冻结的真实 Python 异步库任务上，由新 Worker 生成小范围补丁，
经过确定性验证、范围与风险门禁、隔离 Reviewer 和 Finish 得到可人工提交的结果。它仍只是一个
单案例，不能解释为成功率、跨仓库泛化能力、生产安全或对未知上游修复的独立发现。

## 2026-08-02 Codex 上下文隔离 Dogfood：Node SemVer #512

本条验证 `codex exec` 角色关闭个人 memories、plugins、hooks 和 legacy notify 后，Vega 是否
仍能在 Windows 上完成真实 JavaScript 修复。任务冻结在 Node SemVer 的准备提交
`21fdfaaa9c5181ca4346e7334a65db0a7c95d132`，要求修复 `includePrerelease` 下 tilde X-range
的下界，并限制在 3 个既有文件内。Issue 正文已经给出期望行为，因此该案例不是盲测。

- Run `20260802-001152-196015-bug-loop` 的 Worker 产生了范围内补丁，但 Codex 写工具留下空的
  根 `.agents/`，Workspace Gate 在验证前停止；该现场证明原 Gate 会把工具自身的空目录副作用
  当成项目污染。
- Run `20260802-004006-025433-bug-loop` 已禁用个人上下文，Codex 仍会在使用写工具时创建空
  `.agents/`。加入“仅豁免完全为空、非链接、可读取的根目录”规则后，continue 通过 Workspace、
  Scope 与 3 条验证，但 Reflect 拒绝了整文件 CRLF diff。目标副本继承了系统级
  `core.autocrlf=true`，与既有 `windows_autocrlf_enabled` 预检告警一致；该负结果保留，未通过
  放宽 evidence 语义绕过。
- 最终 Run `20260802-014034-608450-bug-loop` 使用新的 `--no-checkout` 本地副本，在 checkout 前
  固定 `core.autocrlf=false`，其余任务、HEAD、配置、模型与迭代预算不变。Worker 与 Reviewer
  均为 `gpt-5.5 / xhigh`，实际一轮完成；Worker 约 `278.5` 秒，Reviewer 约 `52.4` 秒，runner
  未提供可核验 token usage。
- Worker 只修改 `classes/range.js`、`test/fixtures/range-parse.js` 和
  `test/fixtures/range-include.js`，共 `10` 行新增、`2` 行删除；根 `.agents/` 保持完全为空，
  没有新增依赖或越界路径。
- 两条 Node 行为 oracle 与 `git diff --check` 全部通过；Workspace Gate、三阶段 Scope Gate、
  Risk Gate 均通过，risk 为 `low / self-check`。隔离只读 Reviewer 返回 `approve`、findings 为
  `0`，Finish 为 `ready_to_commit`，artifact integrity valid 且 evidence freshness fresh。
- Worker 与 Reviewer 的 execution command 均显式包含 `notify=[]` 以及关闭 hooks、memories、
  plugins 的参数。run artifacts 高置信凭证模式扫描为 `0`，相关控制进程均已退出；目标副本的
  push URL 被禁用，Vega 没有自动 commit、push、release 或写入长期 memory。

这组三次运行证明了两点：个人 Codex 上下文可以从 Vega 角色输入中移除而不破坏主闭环；空
`.agents/` 可以按严格条件视为工具残留，同时其中任何内容仍会触发 fail-closed。它也再次证明
Windows 行尾策略必须在 checkout 前冻结。最终成功仍只是一个公开 Issue 单案例，不能外推为
跨仓库成功率或未知任务泛化能力。

## 2026-08-02 主机断电恢复 Dogfood：packaging #1232

本条记录主线 `3d2b45a` 上的一次独立恢复验证，不属于 CRWP-V1，也不是无中断 fresh auto
成功样本。任务冻结在 pypa/packaging 的准备提交
`93c303e0e7e36f24aa45fc339ba78cbf1ca3e257`，其上游基线为
`b34d12acb28c9ad3a6b0b3cc82f03a4b0b98c8c0`。任务要求修复 `Requirement` 相等对象可能产生
不同哈希的问题；官方修复没有进入 Worker 或 Reviewer 输入，但 Issue 已明确期望行为，因此
该案例不是盲目根因发现。

- Run：`20260802-022549-926957-bug-loop`；Worker 与 Reviewer 均配置为
  `gpt-5.5 / xhigh`，最多两轮。
- 第 1 轮外部 Worker 运行期间宿主机关机。重启后原 owner/child PID 均已不存在，`recover`
  生成 Recovery ID `42e2dc380946424f86050ef51b3faff9`，将该轮冻结为 `interrupted`；它不参与
  成功、验证或 Reviewer 判定。
- 中断前 Worker 已在 3 个允许文件中留下候选：`20` 行新增、`1` 行删除。恢复前登记和完成后
  复核的 diff Git object ID 均为 `f3b9416c8a97d1e369c3c010fc0f2e0c7d15a0f3`；期间没有人工源码
  修改、清理候选或启动新 Worker。
- 用户明确要求继续后，`vega loop continue` 创建第 2 轮，只对保留现场重建验证、门禁和
  Reviewer 证据。独立 hash/equality oracle、完整 `tests/test_requirements.py`
  （`5311 passed`）、Ruff 和 `git diff --check` 全部通过。
- pre-verification、post-verification 和 pre-review 三阶段 Scope Gate 均通过；Risk Gate 为
  `low / self-check`。独立只读 Reviewer 返回 `approve` 且 findings 为 `0`。
- Eval 全部通过，artifact integrity 为 valid，evidence freshness 为 fresh，Finish 为
  `ready_to_commit`。登记过的 Worker、Reviewer 与控制进程均已退出，run artifacts 的高置信
  凭证模式扫描为 `0`；目标副本没有 remote，Vega 没有自动 commit、push、release 或写入长期
  memory。

该案例证明单个真实 Python 包任务在宿主机关机后，可以保留 Worker 候选、冻结中断轮次，并在
用户明确继续后重建可信验证和隔离审查证据。它不能证明无中断 auto 成功率、重复崩溃恢复、任意
中断点的一致性或跨仓库泛化能力。复盘还暴露了一个状态展示缺陷：根状态已经终止时，旧的
`running` execution 曾被误报为当前活动进程；配套修复只调整状态选择和提示，不改变恢复或成功
语义。
