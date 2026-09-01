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

## 2026-08-02 独立 fresh auto Dogfood：testify #1585

本条是在主线 `72abe84` 上对 `stretchr/testify#1585` 的受控公开重放，参考 PR 为 `#1587`。
任务冻结在上游基线 `a61e9e59d659c95de716df845b99f2ac6c443939`；官方补丁只用于筛选任务
规模，没有进入 Worker 或 Reviewer 输入。Issue 已明确期望行为，因此该案例不是盲目根因发现，
也不能证明模型未见过上游修复。

- 首次 Run `20260802-105756-990084-bug-loop` 使用的验证命令包含 POSIX 单引号；Windows
  `cmd.exe` 没有按预期保护 `-run` 和 `-skip` 中的元字符，导致 oracle 假通过、包测试命令被
  解释为管道。Reviewer 因证据不可信返回 `needs_human`，终态为
  `needs_human / review_run_failed`。该候选保持原样、禁止复用，也不计作 Worker 成败样本。
- 修正后的协议先通过 `cmd.exe` 预检，再以 fresh target 和准备提交
  `5f2bb474faf1b1e1fefb437c037ef8a4eeff1f77` 启动 Run
  `20260802-111105-614884-bug-loop`。Worker 与 Reviewer 均为 `gpt-5.5 / xhigh`，实际一轮完成。
- Worker 约耗时 `216` 秒、报告 `49,841` tokens；Reviewer 约耗时 `54` 秒、报告
  `16,797` tokens。候选只修改 `assert/assertions.go` 和 `assert/assertions_test.go`，共
  `19` 行新增、`0` 行删除；diff object ID 为
  `cee77d4002f15b58dbb5a4c95d3898e50226951d`，stable patch ID 为
  `e7dda663e64f7c6f10dea379ef602e815dca8aeb`。
- 独立 byte-slice Regexp oracle、预注册排除 4 个 Windows symlink 权限测试后的完整 `assert`
  包、`go vet ./assert` 和 `git diff --check` 全部通过。三阶段 Scope Gate、Workspace Gate、
  Risk Gate 均通过；Risk 为 `low / self-check`，隔离只读 Reviewer 返回 `approve` 且 findings
  为 `0`。Eval 无 FAIL，artifact integrity 为 valid，evidence freshness 为 fresh，Finish 为
  `ready_to_commit`。
- Worker 自检曾把 Go cache 写入仓库外的盘符根临时目录，共 `2,317` 个文件、
  `109,042,875` bytes。该目录在用户明确确认后由控制端精确删除并验证不存在；没有触碰同级其他
  内容。此事实暴露的是通用 Worker prompt 缺少输出位置约束，不代表 Vega 拥有操作系统级写入
  隔离。后续 prompt 仅明确要求自检缓存和临时输出留在目标仓库专用目录，Runtime 行为和成功语义
  均未改变。
- 两次 run 登记的 Worker、Reviewer 与 verification 子进程均已退出；共 `117` 个相关 run 和
  控制 artifact 的高置信凭据模式扫描为 `0`。v2 目标无 remote、无未跟踪文件，候选未被控制端
  修改、提交或推送。

该案例证明当前主线能在一个冻结的真实 Go 库任务上生成范围内补丁，并通过确定性验证、门禁、
隔离 Reviewer 与 Finish；同时证明协议命令必须按宿主 shell 预检，prompt 约束不能替代 OS 级
边界。它仍只是单案例，不能外推为总体成功率、跨仓库泛化能力或生产安全证明。

## 2026-08-04 `v0.1.4` 精确 Tag 发布 Smoke

本条只验证已经发布的 annotated Tag `v0.1.4` 所指向的精确提交
`289a1ad0431e0aaa2e74768c517058e62a33fdbf`，不修改或重建 Tag、Release，也不把候选提交上的
历史 smoke 代替最终发布提交。

- Run：`20260804-093946-135999-feature-loop`；任务只允许修改 `README.md`，实际新增
  `1` 行、删除 `0` 行，目标仓库 HEAD 未变化且没有 remote。
- Worker JSONL 共 `20` 行，Reviewer JSONL 共 `4` 行，全部可解析，两个角色各有一条最终
  `agent_message`；两个 `process-stderr.txt` 都为空。
- 终端共显示 `25` 条固定安全进度，不包含目标路径或 Codex 执行命令。
- `python -m pytest -q` 正常退出并报告 `1 passed`；Reviewer 返回 `approve`，findings 为
  `0`。
- Finish 为 `ready_to_commit`；artifact integrity 为 valid，evidence freshness 为 fresh。
- `54` 个 run 文件的高置信凭据模式扫描为 `0`，登记的 `4` 个进程均已退出。
- 本机审计摘要保存在该 Tag worktree 的 `.tmp/v014-smoke-audit.json`，不进入 Git。

该 smoke 证明发布 Tag 对应的代码能完成这一个受控 JSONL 写审流程，并验证 stdout/stderr
分流、安全进度、确定性验证、隔离 Reviewer 和 Finish。它不能证明任意任务成功率、模型泛化
能力或操作系统级隔离。

## 2026-08-04 执行结果：CRWP-V1-02 Sequelize #18265

本条追加 CRWP-V1 Case 02 的唯一正式运行结果，不修改此前的预注册、Amendment、Case 01
失败记录或 Case 03 资格变化记录。

- 正式登记提交为 `f748a2f5712e85e96b26110d91e3963dcd18df1f`，冻结 Runtime 提交为
  `77192aaec3be4baffe90657bbd7d2d343c45062a`；控制 manifest SHA-256 为
  `9f24c2f352f12c64f5920131a3ba1ba67686209f2cb38dd74df77f75b44a7902`。
- fresh baseline 最终为 `accepted=true`；两次 oracle 输出一致，SHA-256 都是
  `0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b`。
- 正式调用前扫描 `945` 个 tracked 文件、`9,657,058` bytes，冻结负向词表命中为 `0`；
  最终 Worker prompt 再次扫描后命中仍为 `0`。
- Run：`20260804-130626-039900-bug-loop`。`gpt-5.4 / medium` Worker 达到冻结的
  `900` 秒 timeout，Vega 确认终止 owned process，最终状态为
  `needs_human / timed_out`。
- 已记录的 Worker stdout 含 `49` 行可解析 JSONL，没有最终 `agent_message`；最后可见事件
  仍在调查并设计 SQLite `CREATE TABLE` 解析方案。execution 同时注明输出读取线程关闭超时，
  因此不能宣称该文件覆盖了外部进程的全部输出。
- Worker 没有修改文件；目标仓库保持 clean、remote 为空。Workspace Gate、Verification、
  Reflect、Risk Gate 和 Reviewer 均未启动。
- Finish 为 `needs_human`；artifact integrity 为 valid，evidence freshness 为 false，
  唯一原因是 `trusted_review_missing`。
- `process-output.txt`、`process-stderr.txt`、`supervisor-summary.json` 和
  `control-summary.json` 的 SHA-256 分别为
  `503e596f9e00dec67477ce8f0eb2089c1d8b7d65cbea30f1e63b40837717fe45`、
  `bcc6ea5f8090892e132b2b292fe46f88b7b6de10130472c4b0e383c12c2d3ff2`、
  `226c25e037c75c1bf1f21728419ea101cdff157b21d1b5f4dfbc07c5e882e5` 和
  `2a4ae64f8187b04cecb20ea31890e359d7fe328e0d5b5db5185e21c45c0658f1`。

本结果只证明 Vega 在冻结 timeout 到达后停止后续验证与审查、保留现场并交还人工。它不能
解释为 Worker 已修复或未能修复目标缺陷，也不能与 Case 01、Case 03 合并计算成功率。按预注册
禁止选择性重跑、延长 timeout 或更换结果；CRWP-V1 三个 Case 至此都已有合同允许的终态。

## 2026-08-05 Phase 4 Codex assist：未公开 Java 结算项目

本条是日常使用 Phase 4 的第一条真实记录。目标是一个无 remote 的隔离副本；公开内容不包含
仓库名称、业务文件名、源码 diff、业务数据、本机绝对路径或原始 `runs/`。本条记录的是实际
控制链和裁决，不是可独立重建完整 Finish/Eval 的证据包。

- 执行前由主会话完成只读调查和计划确认。首次 assist Run
  `20260805-101755-725252-bug-loop` 的执行文本仍保留调查阶段的“不要修改”要求，人工明确
  reject，没有让歧义任务进入实现。
- 第二次 Run `20260805-101926-723431-bug-loop` 在主会话修改代码并执行自检后，两轮都被
  Workspace Gate 停止：新增未跟踪文件为 `0`，但 ignored 构建目录相对启动基线发生变化，
  Verification、Reflect 和 Reviewer 均未启动。该现场暴露了真实使用矛盾：Worker Prompt
  允许选择性自检并建议把产物放进仓库 `.tmp/`，Workspace Gate 却会拒绝除 Harness 专用目录
  外的 ignored 变化；Maven 等常见工具默认写入项目构建目录。
- 后续审查先指出高风险分支缺少直接回归；补测试后，固定验证又暴露夹具无法生成目标业务行。
  这些中间结果均保持 `needs_human`，没有被最终通过样本覆盖或改写。
- 最终 Run `20260805-103942-170225-bug-loop` 使用 assist 模式，外部主会话完成修改，
  `worker_status=skipped`。实际只修改 `3` 个 tracked 文件，其中 `1` 个生产实现、`2` 个测试，
  共新增 `238` 行、删除 `8` 行，没有新增依赖或越界文件。
- 两条固定 Maven 验证分别耗时约 `22.95` 秒和 `23.99` 秒，均正常退出；验证覆盖普通范围逐行
  累加、单行实际数量、负向证据不重复计数、不同商品独立归属、高风险分支保留既有去重口径，
  以及明细、合计和月汇总传递。
- pre-verification、post-verification 和 pre-review 三阶段 Scope Gate 全部通过。
  Risk Gate 为 `high / human-review`，命中“退款与结算”命名高风险。隔离 Reviewer 正常完成，
  findings 为 `0`，风险披露为 `no_obvious_issue`；最终仍固定为 `needs_human`，等待人工检查真实
  重复记录是否代表独立有效数量，以及高风险分支在实际输入链路中的判定。
- 该运行最初还发现一个 Vega 缺陷：高风险必审会把 Reviewer verdict 固定为 `needs_human`，
  旧 Finish 因只接受 `approve` 的受审工作区指纹，错误显示验证结论未知。PR `#44` 修复后，
  使用合并提交 `a8493aa` 重新生成同一 Finish，结果为 `verification_passed=true`、
  artifact integrity valid、evidence freshness fresh，同时保持
  `finish_status=needs_human`。
- 目标副本没有 remote，Vega 没有自动 commit、push、release、删除业务文件或写入长期
  memory。

本案例同时覆盖 Codex assist、Reviewer 发现阻塞问题、Workspace Gate fail-closed 和命名高风险
人工确认。它只有定向测试，没有完整真实数据验收或全量项目回归，不能证明结算口径安全、生产
可用性、任意任务成功率或跨仓库泛化能力。Claude Code assist 与边界清晰任务的 `vega do`
仍需独立验证；ignored 自检产物问题也应以单独的小改动处理，不放宽现有 Gate。

## 2026-08-05 独立 fresh auto Dogfood：Echo Vault 登录页语言切换

本条验证边界清晰的小型前端任务能否直接通过 `vega do` 完成。目标冻结在 Echo Vault 提交
`29bd2a5aaa4397e5ac23fbd080a1c984e3a658d8`，任务要求登录页标题和字段文案随既有中英文状态
切换，并补充回归测试。目标副本没有 remote，正式运行没有接收历史正确补丁。

- 首次 Run `20260805-124844-791095-bug-loop` 中，Worker 正常退出并只留下预期的 3 个
  tracked 文件修改，但其执行的 Trellis Python 上下文命令在
  `.trellis/scripts/common/__pycache__/` 写入 ignored `.pyc`。Workspace Gate 检测到
  `baseline_ignored_changed=true`，在 Verification、Reflect 和 Reviewer 前停止，终态为
  `needs_human / workspace_check_failed`。
- 该失败同时暴露 Finish 的展示缺陷：没有 Reviewer 时仍使用“review 后工作区变化”和
  “现有 approve 已失效”等措辞，并把未获得可信变更列表显示为 `0` 个文件。失败现场保持原样，
  没有清理缓存后继续，也没有把未执行的验证或审查写成通过。
- 修复后的 Codex Worker/Reviewer 进程固定继承 `PYTHONDONTWRITEBYTECODE=1`；Finish 区分
  “尚无可信 Review”和“已有 Review 但快照过期”，无法获得可信变更列表时文件数写为
  `null`，Markdown 显示“未知”。本次重跑所用 Runtime 改动随后由 Vega 提交
  `7d49f6a` 固化，并补充回归测试。
- fresh Run `20260805-150612-164026-bug-loop` 使用相同目标提交和任务重新开始。Worker 实际
  执行 `get_context.py` 的默认、`phase` 和 `packages` 三种模式，结束后目标内 `.pyc` 数量
  仍为 `0`；Workspace Gate 通过且 `baseline_ignored_changed=false`。
- Worker 只修改 `frontend/src/ui/i18n.tsx`、
  `frontend/src/ui/pages/LoginPage.test.tsx` 和
  `frontend/src/ui/pages/LoginPage.tsx`，共新增 `17` 行、删除 `9` 行，没有新增文件、
  依赖或越界路径。
- `pnpm --dir frontend test -- LoginPage.test.tsx` 正常退出，实际报告
  `14 files / 135 tests passed`；`pnpm --dir frontend build` 通过。三阶段 Scope Gate、
  Workspace Gate 和 Risk Gate 全部通过，Risk 为 `low / self-check`。
- 隔离只读 Reviewer 返回 `approve` 且 findings 为 `0`。Finish 为
  `ready_to_commit`，artifact integrity valid，evidence freshness fresh，可信变更文件数为
  `3`。目标副本仍无 remote，Vega 没有自动 commit、push、release 或写入长期 memory。

这组配对运行证明 Python 自检缓存会被现有 Workspace Gate 如实拦截，也证明禁用 Worker/
Reviewer Python bytecode 后，同一边界清晰任务可以通过确定性验证、范围与风险门禁、隔离审查
和 Finish。它仍只是一个已明确验收标准的低风险前端案例，不能证明模糊任务调查、Claude Code
assist、高风险修改、任意仓库成功率或生产安全。

## 2026-08-05 Claude Code assist：Echo Vault 历史会话重新打开

本条验证用户只报告界面现象、不知道缺陷位置时，Claude Code 能否先调查并形成计划，再把外部
修改交给 Vega 的 assist 判断链。目标冻结在缺陷父提交 `b64e192`，隔离实验基线为
`608bd71`；目标副本没有 remote，也不包含后续正确修复。

- 用户现象是“历史页能看到以前的会话，但找不到重新打开并继续聊天的入口”。Claude Code
  先进行只读调查，正确定位到历史页缺少设置当前会话并跳转聊天页的操作。
- Claude Code 的初始计划使用了目标项目不存在的 npm、type-check 和 lint 命令，并倾向让标题
  区域承担隐式点击。主会话据实际脚本改为 pnpm 测试、构建和 `git diff --check`，同时要求使用
  明确的“打开”按钮；人工确认计划后才进入修改。
- 第一版候选超出预设差异预算，并包含依赖 CSS class 的脆弱断言。主会话要求缩减后，正式候选
  只修改 `frontend/src/ui/pages/HistoryPage.tsx`，并新增
  `frontend/src/ui/pages/HistoryPage.test.tsx`，共新增 `129` 行，没有新增依赖或越界文件。
- Run `20260805-172130-495761-bug-loop` 的第一次 `continue` 检测到新增测试仍是未跟踪文件，
  Workspace Gate 在 Verification 和 Reviewer 前停止。人工核对后只把该测试加入 Git index，
  没有 commit，也没有修改候选内容；第二次 `continue` 才继续执行。
- 第二轮三阶段 Scope Gate 全部通过。前端完整测试报告 `12 files / 72 tests passed`；TypeScript
  构建、Vite 构建和 `git diff --check` 通过。Risk Gate 为 `low / self-check`。
- 独立只读 Codex Reviewer 返回 `approve`，findings 为 `0`。Finish 为
  `ready_to_commit`，artifact integrity valid，evidence freshness fresh；Vega 没有自动
  commit、push、release 或写入长期 memory。
- 人工最终 Diff 复核确认，认证后的应用布局启动时已经加载全局会话列表，聊天页会根据
  `activeId` 读取历史消息并继续发送；因此本候选没有同步历史页局部列表到全局列表，不阻塞本次
  “重新打开并继续聊天”的目标。该判断仍是代码级复核，不是浏览器端到端验证。
- 一个未参与执行、只读取 `finish-report.md` 的全新 Claude Code 会话，能够识别两个变更文件、
  两条已通过验证、低风险评级，以及“仍需人工检查、尚未提交”的状态。它也指出第一屏没有列出
  测试用例名称，`Scope：未记录` 容易误解，且第一屏的 `Workspace：skipped` 与第二轮
  `workspace-check.md` 的 `passed` 展示不一致。

本案例证明 Claude Code 可以作为外部调查与修改会话，最终复用 Vega 的 Workspace、Scope、
Verification、Risk、独立 Reviewer 和 Finish 判断链。证据只覆盖一个低风险前端任务；导航
测试使用 mock，不等同于真实浏览器与后端端到端验证，也不能外推为任意任务成功率、跨仓库
泛化能力或生产安全。Finish 暴露的展示问题先作为观察项保留，不在本次验收中顺势扩建 Runtime。

## 2026-08-14 Supervisor Agent Gate 2B：真实 Codex Adapter

本条追加 Gate 2B 两个冻结案例的唯一正式结果，不改写此前 Goal P1、CRWP 或日常使用记录。
两个目标都使用无 remote 的隔离副本；Vega 没有自动 commit、push、release、清理目标 Diff
或写入长期 Memory。完整合同、预算、Amendment 和退出条件见
[`../docs/SUPERVISOR-AGENT-GATE-2B-PLAN.md`](../docs/SUPERVISOR-AGENT-GATE-2B-PLAN.md)。

### SAG2B-01：Echo Vault 历史会话重新打开

正式 R4 之前的三个登记现场均保留：

1. 首次运行在 Worker 启动前发现 assist child 创建的受控运行目录没有进入批准 Checkpoint，
   导致 Workspace 指纹漂移；目标没有 Diff；
2. R2 在 owned process 创建前发现带前缀的 operation identity 不满足 Windows Job 十六进制
   约束；目标没有 Diff；
3. R3 启动了 Codex 进程，但目标项目启用的 `multi_agent_v2` 配置在当前 CLI 下启动失败，
   模型 turn 尚未开始，目标没有 Diff；Supervisor 保守进入 `human`。

对应修复分别为 `a213f0e`、`fa99682` 和 `9ed0b62`。每次后续执行均经人工确认，使用新的
Agent run 和全新隔离目标，没有覆盖旧 Artifact，也没有按结果更换冻结模型。

最终 R4 使用 Agent run `20260814-163054-agent`、child
`20260814-163130-576570-bug-loop`，operation 与 execution 均为
`4665591800dc466ab95043cf837d10c3`。真实 Worker 正常退出并形成窄 Claim，execution 为
`completed`、`termination_unconfirmed=false`。

机器重新采集到两个变更文件：`frontend/src/ui/pages/HistoryPage.tsx` 是 tracked 修改，
`frontend/src/ui/pages/HistoryPage.test.tsx` 是新增未跟踪文件。现有 Core 在 Verification
前按既有规则阻断未跟踪文件，Verification 为 `blocked`，Risk 和 Reviewer 为 `not_run`。
Supervisor 没有把 Worker Claim 当作完成事实，而是确定性选择 `human`，写入 blocked
Checkpoint 并解除 active Writer binding。没有执行 repair、replan 或自动重试。

R4 tracked Diff SHA-256 为
`d9ec27bc03d707eb2157f5adb4ca63ce2e291b2b292969b13be8749b33d72355`，未跟踪测试文件
SHA-256 为 `d67710bc61a82edd71536ce46e3a5892078b07aec43ee78036f322aed04d9bf1`。
Agent State、Trace、Observation、Decision 和 execution 的 SHA-256 分别为
`656b3e348b4c2059aae4f1e6d45ee5b1594ff80b5d7cbcb09d7579f8de0fe52a`、
`f1ec9c9c91b5ad0d7584a831e6aaf3858847e1fe454b9f9d6de2fe74e2b1cca6`、
`fc1c29eddefe2d5d53700d6c59667372eba7bd268725782d505bc94a490afe5d`、
`4642d465e67b76394f09e49e0bd7f04c307b37b7f58f24d482f6c85acbe63e13` 和
`b89b1aacde9a14bc49fe3fdaa85387701b03569450289840f5573b7c1b074190`。

### SAG2B-02：packaging `Requirement` 哈希中断

原冻结准备对象已随旧本地副本丢失。该 Case 在 Agent、Worker、Verification 和 Reviewer
启动前取得人工 Amendment，改为从同一上游缺陷基线重建固定 tree 和准备提交。正式执行使用
准备提交 `26dc3e4982c5e8738553384abb1c85dd019a2e01`；目标对象库不含公开修复对象。

Agent run `20260814-173144-agent` 启动 child
`20260814-173736-094408-bug-loop`，operation 与 execution 均为
`0ac99dd93b6743a4bda15cf8dd67d101`。约 `75.112` 秒后，控制端首次检测到允许范围内的
tracked Diff：`src/packaging/requirements.py`。随后约 `0.7` 秒内调用
`vega agent stop`；停止命令验证当前 binding 与 owned execution 后，写入包含相同 execution
ID 和启动时间的 stop request，没有直接 kill PID。

最终 execution 为 `stopped`，`termination_unconfirmed=false`，owned process tree 已静止，
active Writer binding 已解除。partial Diff 原样保留，目标没有未跟踪文件；Plan 路径门禁通过，
Verification、Risk、Reviewer 和 child Core 均为 `not_run`。Supervisor 根据机器 Observation
确定性选择 `human`，Agent 进入 `needs_human`，`checkpoint-002` 保存 blocked 现场。
没有启动第二 Worker、恢复、重试或评价补丁正确性。

外部轮询脚本检测 Diff 时读取了 Agent State envelope，却没有进入 `data` 字段，因此停止原因
使用了“Writer 活性无法同时确认”的保守措辞。Vega 自身的停止命令仍只对活动且身份匹配的
execution 返回成功；stop request、operation marker 和最终 lease 使用同一 ID。该记录偏差
不影响停止身份边界，也没有通过补跑改写。

保留 Diff SHA-256 为
`aa6b0c3e0b8e2e830e3aa5fb14ff7878052404d66b826d8183353b60b14f5270`。
Agent State、Checkpoint、Observation、Decision、execution、stop request 和 Trace 的
SHA-256 分别为
`dd973a834da237df518d8a334d91b15d05f5a8ee0cb28417b3fbbab7853e060d`、
`36f58e889112dc674b7e875406611f9fda4d1e8d90dd766efac270d94687f079`、
`0fb5cc461bef4030f99852f79353562d39fb3497bf030c1f043b5f3c7629c288`、
`404e871717a3c1fb94743413f8ed87aeff1858f13c9bb2b19db6f2a0e29da74b`、
`1f5d708f37d86af4748a2f74e329db2d57be72b68f5737e5ca410cc93c3de2d4`、
`1db3ee4e664a63d158405676a5cf0e2e74d5ef6dfbd014f373d474a07e68d0df` 和
`73ec847a02ce89232d484fc134de89142de7d92664c6e654fcd860b05d77cdcb`。

### Gate 2B 判定

两个案例均形成冻结合同允许的 Supervisor Decision：SAG2B-01 证明真实 Worker 的成功 Claim
不能越过未跟踪文件门禁；SAG2B-02 证明身份绑定的 stop request 能保留 partial Diff 并交还
人工。真实案例退出条件已满足，判定为 `real-case-pass / merge-pending`。

该结果不证明两个目标补丁正确、Codex 通用修复成功率、任意仓库泛化能力、多 Work Item
累计归因、跨机器未完成 WIP 恢复或 Claude Code Adapter。包含运行中三项集成修复和本结果文档的
最终分支 HEAD 仍需通过 PR CI 与合并前审阅；完成前 PR 保持 Draft，不进入 Gate 3。

## 2026-08-14 Supervisor Agent Gate 2B 合并后状态

本条只追加 Gate 2B 的后续合并事实，不改写上方正式运行发生时的 `merge-pending` 裁决。

- PR `#58` 已完成最终 CI 与合并前独立审阅；
- 合并提交为 `63096bcb453d2dfc7446113b3523b5fde961e2e5`；
- Gate 2B 当前最终状态为 `gate-exit-pass`；
- 两个真实案例的能力边界保持不变，仍不证明补丁正确、完整成功路径、多 Work Item、跨机器恢复
  或 Claude Code Supervisor Adapter；
- 后续先执行 Gate 2C 当前主线真实完整成功路径，原 Gate 3 拆分后继续保持冻结。

## 2026-08-14 Supervisor Agent Gate 2C：SAG2C-01 验证入口无效

本条只记录首次 Gate 2C 正式运行及其协议缺陷，不删除或覆盖 run Artifact、目标 Diff 或原冻结
协议。Agent run 为 `20260814-233225-agent`，child 为
`20260814-233312-366836-bug-loop`。

- 真实 Worker 使用 `gpt-5.6-terra / xhigh`，execution 正常完成，只修改
  `CHANGELOG.rst`、`src/packaging/requirements.py` 和
  `tests/test_requirements.py`，共新增 12 行、删除 1 行；
- 三阶段 Scope Gate 全部通过，Workspace 没有新增未跟踪文件，Risk Gate 为
  `low / self-check`；
- 四条验证中，缺陷复现命令、Ruff 和 `git diff --check` 通过，完整 pytest 命令失败；
- 独立只读 Reviewer 使用 `gpt-5.6-sol / xhigh`，返回 `request_changes`；
- Finish 为 `needs_fix / needs_human`，Supervisor 没有采用 Worker 的完成 Claim，而是根据
  `Verification=failed` 与 `Reviewer=failed` 确定性选择 `replan`；
- 没有启动第二 Writer、repair、自动重试、commit、push、release 或长期 Memory 写入。

事后核对发现，冻结命令
`python -m pytest -q -o pythonpath=src tests/test_requirements.py` 在 pytest 启动阶段已经从
控制仓库虚拟环境加载 `packaging`。后续 `pythonpath=src` 不能替换 `sys.modules` 中已导入的
包，因此失败输出混合了控制环境实现，不是目标仓库的受信验证。

改用“在导入 pytest 前把目标 `src` 放入 `sys.path`”的命令后，同一 Worker Diff 的完整
`tests/test_requirements.py` 为 `5308 passed`；在不含目标修复的干净基线上为
`5307 passed`，而目标哈希复现仍按预期失败。这证明问题来自验证入口。

SAG2C-01 判定为 `invalid-harness`：不计为 Gate 通过，也不计为模型修复失败。原 Case 不重跑；
修正验证入口、禁用运行缓存并使用全新目标的 SAG2C-02 由
[`../docs/SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md`](../docs/SUPERVISOR-AGENT-GATE-2C-R2-PLAN.md)
单独冻结。

## 2026-08-14 Supervisor Agent Gate 2C：SAG2C-02 完整成功路径

本条记录修正验证入口后的全新正式运行，不覆盖或重跑 SAG2C-01。运行使用
`pypa/packaging` 的冻结基线和单一 Work Item，目标仓库只作为项目内隔离现场使用。

- Agent run：`20260814-235155-agent`；
- child：`20260814-235220-433171-bug-loop`；
- operation：`e05b1abb7bb4414d8f484b1f6d2207a7`；
- 目标 HEAD：`a2ac3ee0d68da64bdc765e5189911b206d9ebd91`；
- Worker：`gpt-5.6-terra / xhigh`；Reviewer：`gpt-5.6-sol / xhigh`；
- Worker 只修改 `CHANGELOG.rst`、`src/packaging/requirements.py` 和
  `tests/test_requirements.py`，没有越出批准路径；
- 缺陷复现、目标完整需求测试、Ruff 和 `git diff --check` 均通过；目标完整需求测试结果为
  `5308 passed`；
- Workspace、Scope Gate、Artifact integrity、Evidence freshness 和 Risk Gate 均通过；
- Reviewer 返回 `approve`，findings 为 `0`，覆盖 `3/3` 个变更文件；
- Finish 为 `ready_to_commit / success`，Supervisor 根据机器 Observation 进入
  `finalize`；
- Worker、Reviewer 和 Vega owner 进程均已退出；目标仓库 HEAD 未变化，未执行自动
  commit、push、release 或长期 Memory 写入。

SAG2C-02 判定为 `gate-exit-pass`。这条证据证明当前主线在一个低风险、单 Work Item、
可重建真实案例中，能够完整通过 Worker、Verification、Risk、独立 Reviewer、Finish 和
Supervisor 裁决；不证明目标补丁已被人工合并，也不证明多 Work Item、跨机器恢复、Claude
Code Adapter、Memory 或通用修复成功率。

## 2026-08-15 Supervisor Agent Gate 3A：同机 Handoff 机械往返

本条只记录 Gate 3A 的 Handoff 生产端和同机双隔离副本往返，不把结果外推为真实跨机器恢复、
真实模型继续执行或日常价值。Vega 没有自动 `git add`、commit、push、release、删除文件或写入
长期 Memory。

A 侧使用 Agent run `20260815-132909-agent`。批准 Plan 只有一个未完成 Work Item `W1`，
WIP 只有 `src/example.py`。旧 Writer 已停止，最近 Checkpoint 为 safe，外部副作用为 `none`。
执行：

```powershell
vega agent checkpoint --run 20260815-132909-agent --handoff --reason "Gate 3A same-host isolated clone transfer."
```

生成的 Task Card 为 `handoff_ready`，旧 Verification、Risk、Reviewer 均写为 historical
`not_run`。人工只暂存 `src/example.py` 与 Task Card；`git diff --cached --check` 通过后形成
fixture Handoff 提交 `5e856ab`。Task Card SHA-256 为
`29d39c0a0c7f83d0ab1da8ea795f21f0d20ecb4da548e73ead77f57b41211a9e`；A 侧
manifest 与 Handoff Checkpoint SHA-256 分别为
`8515cb23737b92fba53ba52b85e22ca933af42cd02407fc70e97f98b91da6842` 和
`15e59ea311527d3e316e910670e624fb0c31e51e2c5e5f57695d33d4281d358a`。

B 侧由该提交重新 clone，不包含 A 侧 `runs/`、Trace、SQLite 或聊天。执行
`vega agent resume --repo .` 后创建新 run `20260815-144839-agent-resume`：

- `phase=ready`；
- `current_work_item=W1`；
- `handoff_status=handoff_ready`；
- `allowed_actions=repair,human`；
- Trace 包含且仅包含本轮 `task_card_resumed`；
- 新 Verification、Risk、Reviewer 均为 `not_run`；
- B 侧 HEAD 与包含 Task Card 的 Handoff 提交均为 `5e856ab`，Workspace 无额外 Diff。

B 侧 State、Trace、Task Brief 和状态卡 SHA-256 分别为
`a59479216e80f91faa1dc62daba8486f29fb70abbbd458f1272ad350e01a2b93`、
`ae6df44b9d443eb9a469d21839fe797267c348491d8417a9409cd44686756ae5`、
`a87dc81804674e98e94604807c7eba2491e897c8a1fdc6dc95dfdbe55e816488` 和
`92a57e0679cb6ccd1d8aa7e164dfd21c964ba9888324526b71037d88c2c801e7`。

安全回归覆盖 active Writer、`needs_human` 保留、Workspace 漂移、Task Card 目录链接/
junction/reparse point、绝对路径、fake key、Artifact 发布失败、错误仓库历史和错误 HEAD。
本地 CI 同款节点合计 `1239 collected / 1227 passed / 12 skipped / 0 failed`；Ruff、compileall、
repository hygiene、architecture growth 和 `git diff --check` 通过。

当前判定为 `local-dogfood-pass / merge-pending`。PR CI 与合并前审阅通过后才能将 Gate 3A
记为 `gate-exit-pass`。Gate 3B 真实跨机器接力与 Gate 3C 日常价值观察仍冻结。

## 2026-08-15 Supervisor Agent Gate 3A：PR CI 与退出判定

本条只追加 Gate 3A 的后续验证事实，不改写上方正式运行发生时的
`local-dogfood-pass / merge-pending` 裁决。

实现提交 `33c4ac1` 推送到 PR `#60` 后，workflow run `31871901115` 的 9 项任务全部通过，
覆盖静态检查与分片完整性、POSIX 临时目录、wheel/sdist 构建安装、Python 3.11 兼容性、
Windows 专项与 wheel smoke，以及 Python 3.12 的四个测试分片。两轮独立本地审阅已整合，
没有剩余合并阻断项。

因此 Gate 3A 判定为 `gate-exit-pass`。该结论仍只证明单 Work Item、人工 Git、同机双隔离
副本的机械接力；不证明真实跨机器、真实模型继续执行或日常价值。Gate 3B 与 Gate 3C
继续冻结。

## 2026-08-15 Supervisor Agent Gate 3B：仓库内 Workspace 预检阻断

本条只追加 SAG3B-01 在模型启动前暴露的控制器缺陷，不覆盖 Gate 3A 或 Gate 3B 协议中的
历史记录。候选 Agent run 为 `20260815-180117-agent`。

- 固定 Plan 创建并批准成功，状态进入 `ready`；
- `agent run` 在创建 child 前以 `Workspace 已漂移` fail-closed；
- 没有模型 turn、active child、tracked Diff、自动重试、commit、push、release 或外部副作用；
- 根因是 Vega workspace 与目标仓库相同时，自有 `runs/` 中新写入的 Task Brief、
  Checkpoint、State 和 Trace 被纳入 ignored 指纹；
- 该 run 判定为 `invalid-preflight / no-model-turn`，不占用机器 A 的正式 attempt。

修复提交 `3e636e4` 只排除当前 workspace 自有 `runs/` 和受控 verification 临时根；其他
ignored 路径变化继续 fail-closed。正反向回归、既有漂移节点、静态门禁和完整
`1250` 节点收集通过，workflow `31879544491` 的 9 项 CI 全部成功。固定控制器已从该提交
重新导出；机器 A 仍未正式启动，需等待协议文档提交的 CI 后重新预检。

## 2026-08-15 Supervisor Agent Gate 3B：SAG3B-01 机器 A 正式 Attempt

本条只追加 SAG3B-01 的正式机器 A 结果，不覆盖上方预检阻断记录，也不把环境故障外推为
目标代码或模型通用能力结论。

- 正式启动 HEAD：`c08d46ab469f1a98421b3cabc73a2c5cd18ceb50`；
- 固定控制源码提交：`3e636e40537bfda5213d13a407ae51b6be0fbbd8`；
- Agent run：`20260815-184052-agent`；
- child：`20260815-184120-147051-bug-loop`；
- operation：`02125d80693b4fe7ae548fd527814bb5`；
- Worker：`gpt-5.6-sol / xhigh`。

Codex Worker 从 `18:41:25 +08:00` 运行至 `18:46:49 +08:00`，进程返回码为 `0`，并返回
结构化 `blocked` Claim。返回码只表示 Runner 成功收集到 Claim，不代表任务完成。Worker
尝试使用 PowerShell、Git、Ripgrep 和 Node REPL 读取工作区，但全部在工具启动前遇到
`windows sandbox: helper_unknown_error: setup refresh had errors`。Worker 因此没有读取或
修改目标文件，没有运行自检，也没有产生 tracked Diff。

观察进程没有看到“允许路径 Diff + active Writer”的同时窗口，按冻结协议没有发送
`agent stop`。Worker 退出后，Core 以 `assist continue` 对账当前工作区：

- Workspace 与三个 Scope Gate 均因无 Diff 保持 `skipped`，没有越界文件；
- Ruff 验证通过；
- pytest 受控执行在写入自身 `execution.json` 时遇到 Windows `WinError 5`，Verification
  记为 `failed`，不能作为测试通过证据；
- Risk Gate 与独立 Reviewer 均未运行；
- child Finish 为 `needs_human / no_diff`；
- Supervisor 机器 Observation 记录 `work_item_completed=false`、
  `worker_alive=false`、`external_side_effects=none`；
- Supervisor 确定性选择 `replan`，最终 phase 为 `planning`，Checkpoint 为
  `blocked`，没有启动第二 Writer。

运行结束后 HEAD 未改变，工作区没有 tracked Diff，Writer、pytest 和 Vega owner 进程均已
退出；没有自动 commit、push、release、删除文件或写入长期 Memory。

SAG3B-01 判定为
`insufficient-handoff-opportunity / environment-blocked`：机器 A 没有形成能够安全停止和
发布的 partial WIP，因此没有 Handoff Task Card、Handoff 提交或机器 B 恢复。该 Case 不重跑，
不更换任务、模型、预算或成功条件。Gate 3B 未通过，Gate 3C 继续冻结。

这条证据只证明当前 Supervisor 在真实 Worker 工具环境阻断且 Core Verification 失败时保持
fail-closed，不会把 Runner 返回码 `0` 或 Worker 自述误当完成，也不会自动重试或制造跨机器
成功叙事；它不证明跨机器接力、任务修复成功率或 Windows Codex 沙箱稳定性。

## 2026-08-16 Supervisor Agent Gate 3B：SAG3B-02 机器 A Handoff

本条只记录 SAG3B-02 的机器 A 本地阶段结果，不覆盖 SAG3B-01，也不提前宣称真实跨机器
Gate 通过。控制候选基于 `main@d2c28103d352f251f1bf20d89758e666dba086ed`，使用只含
tracked `src/` 的固定控制快照；裸 Codex workspace-write 与 Vega-owned Codex Runner
预检均通过。脱敏后的 Codex 默认配置为 `gpt-5.6-sol / xhigh`。

- Agent run：`20260816-121500-agent`；
- child：`20260816-121529-270617-bug-loop`；
- operation / execution：`e44ed6747d70430d8388b58d82aa5d0d`；
- 目标分支：`codex/sag3b-02-wip`；
- 目标 HEAD：`d2c28103d352f251f1bf20d89758e666dba086ed`。

真实 Worker 只修改 `src/vega/execution_control.py` 和
`tests/test_execution_control_safety.py`，没有未跟踪文件。实现保留同目录临时文件与
`os.replace` 原子语义，把替换等待改为 `1.0` 秒有界截止，并增加真实 Windows 共享锁回归。
Worker 自检得到 `4 passed / 70 deselected`、Ruff 通过、`git diff --check` 通过。

控制端在 Worker 启动后约 `192.221` 秒首次观测到允许路径 Diff；约 `53.089` 秒后，在
State 仍绑定相同 child/operation 且 execution 仍为 `running` 时发送 identity-bound stop。
Worker 最终为 `stopped`，`termination_unconfirmed=false`，owner/child PID 均退出；
Verification、Risk、Reviewer 和 Finish 均保持 historical `not_run`，没有第二 Writer、
自动重试或人工补丁。

原 `agent run` 返回后，控制端再次执行 stop 固化静止 Checkpoint，再用两个 run-local 审计
Artifact 核对 Worker 事件、进程身份、Workspace 和工具范围。没有观察到外部工具调用，sandbox
网络与额外 writable roots 关闭，目标仅有两个批准文件，因此外部副作用裁决为 `none`。

随后生成：

- safe Checkpoint：`checkpoint-004`；
- Handoff Checkpoint：`checkpoint-005`；
- `handoff_status=handoff_ready`；
- Task Card：`.vega/tasks/2026-08/2026-08-16-sag3b-02-handoff.md`；
- Task Card SHA-256：
  `5e3a7d55c25d4927f672894383a3d103add93f3b05634e6c464c325908b8661d`；
- Handoff Workspace Digest：
  `72d07b2bfade0d0cfad7c25462d73163968019b3ed8d6edcb96b3aa245f13ec9`；
- 机器 A WIP patch SHA-256：
  `d1a8602d838c579fc9be819c128dd49b7e80d5a197673c20b24f7312faf81c03`。

机器 A 当时的阶段判定为 `machine-a-handoff-ready / machine-b-pending`。它证明真实 Worker
partial WIP 能够被安全停止、对账并转成可移植 Task Card；不证明 WIP 最终正确、物理跨机器
恢复或 Gate 3B 通过。

提交前最终审阅又发现：机器 A 使用的 `control-runtime-local-r3` 来自未提交工作树，而后续
控制提交 `5d252d4b366e7a1bed1eb8370a4c599401055a21` 为通过 architecture growth 门禁，
对 `agent_codex_adapter.py`、`agent_codex_evidence.py` 和 `loop_runtime.py` 做了行为等价的
整理，三者不再字节一致。正式协议已预注册机器 A/B 必须来自同一 `control_source_commit`，
不能在看到结果后放宽。

因此本 Case 的最终判定收紧为
`machine-a-handoff-ready / formal-gate-nonconforming / machine-b-not-run`。SAG3B-02
不再继续正式机器 B，也不计入 Gate 3B 通过证据。控制修复通过 PR CI 并合入主线后，必须以
同一主线提交预注册新 Case，再执行完整机器 A/B 接力。

## 2026-08-17 Supervisor Agent Gate 3B：SAG3B-07 Git-only 恢复后 Worker 超时

本条只追加 SAG3B-07 的实际结果，不覆盖 SAG3B-01～06，也不把独立 clone 模拟表述为物理
换机。完整冻结协议和中间失败记录见
[`../docs/SUPERVISOR-AGENT-GATE-3B-PLAN.md`](../docs/SUPERVISOR-AGENT-GATE-3B-PLAN.md)。

machine A 从固定控制提交
`e35cffcb3c0bc3669a5be401cfb8c84beaaa2487` 启动真实 Worker。MCP 隔离复查通过，首次出现
允许路径 Diff 后按 child/operation 身份停止，只保留
`src/vega/agent_runtime.py` 与 `tests/test_agent_runtime.py`。人工核对 owned 进程、
Codex 工具事件和 Workspace 后，将外部副作用裁决为 `none`。Task Card SHA-256 为
`5c0cdfd4f3096346dfbbc5aa6ebc9e6aae35797bce714200ae8168d019f49389`，Handoff 提交为
`976fc359de306153837c8d809b05ed6bdd8513e5`。

machine B 从该提交建立第二个独立 fresh clone，并显式选择 Task Card 恢复为：

- Agent run：`20260817-141631-agent-resume`；
- child：`20260817-141708-799856-bug-loop`；
- operation：`bb646171185747e685e4f25fda8ea761`；
- 固定 Worker 预算：`900` 秒。

新 run 重新形成 Goal、批准 Plan、Work Item、Handoff 基线和 Workspace 约束，没有复用
machine A 的 State、Trace、SQLite、运行目录或聊天。Writer 启动命令包含 5 个 MCP 禁用
覆盖；进程树只出现 Codex、命令执行器、PowerShell 与 Python。Codex JSONL 没有 MCP、
Web、浏览器、网络或 Git 写入事件。

Worker 在当前 Python 3.14.3 / pytest 9.0.2 环境中调查 pytest 进程不退出的问题，没有生成
最终 `agent_message`，最终被 Vega 记为 `timed_out / termination_unconfirmed=false`。
machine B 工作树没有新增漂移，但 Verification、Risk、Reviewer 与 Finish 均未运行。
Supervisor 选择 `human` 并进入 `needs_human`，没有第二 Writer、自动重试、repair、自动
Git 或长期 Memory 写入。

因此 SAG3B-07 判定为
`machine-a-handoff-pass / machine-b-git-resume-pass / machine-b-worker-timeout /
gate-not-passed`。该 Case 不重跑。另一个隔离开发环境中的 4 个定向测试节点得到
`4 passed`，只用于后续代码审查，不能替代冻结 machine B 的完整 Gate 证据。

## 2026-08-17 Supervisor Agent Gate 3B：SAG3B-08 machine A 已知副作用

本条追加 SAG3B-08 的实际结果，不覆盖 SAG3B-07，也不把允许范围内的正确 WIP 等同于
Handoff 成功。完整预注册协议见
[`../docs/SUPERVISOR-AGENT-GATE-3B-PLAN.md`](../docs/SUPERVISOR-AGENT-GATE-3B-PLAN.md)。

两份控制 archive 均来自预注册提交
`a816be2385766003c4351fd4a7674f24fbb5c523`，SHA-256 一致。machine A 在独立 Python
`3.12.10`、pytest `8.4.2` 环境中连续三次通过冻结测试，且预检前后没有 Workspace 或
进程漂移。

- Agent run：`20260817-235358-agent`；
- child：`20260817-235421-789385-bug-loop`；
- operation：`c7e74fd678f9410f8378ae881bc90cf6`；
- Worker 只修改 `src/vega/agent_codex_preparation.py` 和
  `tests/test_agent_codex_adapter.py`。

首次出现允许路径 tracked Diff 后，控制端发送身份绑定 stop。Worker 最终为 `stopped`，
`termination_unconfirmed=false`；目标 HEAD 未改变，没有越界文件、第二 Writer、网络、
MCP、浏览器或外部服务调用。

但 Worker 没有遵守“自检不得额外留下文件”的 Prompt 约束。它把 pytest `--basetemp`
放到系统 `%TEMP%`，留下包含临时 Git 仓库和测试 Workspace 的
`vega-worker-sag3b08-*` 目录。首次测试还因继承的 `VEGA_GIT_SAFE_DIRECTORY` 与 fixture
仓库不一致而失败；移除变量后的重跑在 stop 生效前没有形成完成事件。

静止 stop 后，人工将该仓库外文件写入裁决为 `known`。Vega 追加
`checkpoint-004 / needs_human / blocked`，保持 `handoff_status=none`，并拒绝发布 ready
Handoff。没有生成 Task Card、Handoff commit 或 machine B clone；Verification、Risk、
Reviewer 与 Finish 均未运行。

SAG3B-08 最终判定为：

`stable-environment-preflight-pass / machine-a-partial-diff-pass /
known-repository-external-temp-write / handoff-not-published /
machine-b-not-started / gate-not-passed`。

这条证据说明稳定依赖版本并不足以保证可接力：Worker Prompt 不能确定性约束自检临时文件。
按预注册停止线不自动追加 SAG3B-09，也不把清理临时目录追溯解释为副作用从未发生。

## 2026-08-18 Supervisor Agent v0.2.0 发布验收：Echo Vault 设置页并发竞态

本条是单独批准的 v0.2.0 发布验收，不命名为 SAG3B-09，也不覆盖 SAG3B-01～08 的历史
判定。目标是验证当前产品合同能否在真实代码任务中完成：

```text
partial WIP
→ identity-bound stop
→ Workspace 与副作用对账
→ Git Task Card
→ 独立 fresh clone 恢复
→ 新 Worker
→ Verification / Risk / 独立 Reviewer / Finish
→ 人工 PR
```

目标任务是修复设置页用户名修改与密码修改可并发提交的竞态。批准范围只有
`frontend/src/ui/pages/SettingsPage.tsx` 和
`frontend/src/ui/pages/SettingsPage.test.tsx`；明确禁止修改后端 API、数据库、权限、登录
流程、密码规则、依赖和页面视觉体系。

前序机器 A 已形成两个允许文件的 WIP，经身份绑定 stop、进程与 Workspace 对账、人工副作用
裁决后生成 Git Task Card。后续验收 clone 只从任务分支读取 committed WIP 与 Task Card，
没有复制旧 `runs/`、Trace、LangGraph SQLite、虚拟环境、临时目录或聊天。

### Provider 429 与恢复状态缺口

恢复 run `20260818-221144-agent-resume` 启动 child
`20260818-221159-167783-bug-loop` 后，Provider 返回 429，外部 runner 退出码为 `1`，没有
Worker Claim。Vega 记录：

- `work_item_completed=false`；
- `worker_alive=false`；
- `external_side_effects=unknown`；
- Verification、Risk、Reviewer 和 Finish 均为 `not_run`；
- Supervisor 确定性选择 `human`，最终保持 `needs_human`，没有自动重试或第二 Writer。

人工完成静止现场对账时又发现一个真实控制缺口：新 run 从 Task Card 恢复后仍继承
`handoff_status=handoff_ready`，导致已消费的旧交接状态阻断本次新的副作用裁决。修复提交
`aa096c014fd00807aeb1a0c6cb088341a264b280` 让恢复 run 从 `handoff_status=none` 开始，并增加
“恢复后出现新的 unknown 副作用仍可裁决”的回归。该修复不降低 unknown 副作用门禁，也不
把 429 attempt 改写为成功。

### Fresh clone 重新执行

最终验收使用从远端 `release/v0.2.0@aa096c0` 重建的非 editable Vega 控制环境，以及从远端
任务分支重建的独立目标 clone。恢复 run 为 `20260818-231923-agent-resume`，初始
`handoff_status=none`。

第一条 child `20260818-231952-765911-bug-loop` 正常退出，四项前端门禁通过，但独立
Reviewer 返回 `needs_human`：

1. 原测试先等待 busy 状态渲染，再尝试重复提交，不能证明 React 状态提交前的同一事件批次
   竞态，也不能证明同步 ref 锁确实必要；
2. 目标仓库 `AGENTS.md` 要求提交前提供后端测试证据，当前 Plan 没有包含该命令。

Supervisor 采用 Machine Observation
`observation-d6021f324102`，选择 `human`，没有用 Worker 的 `completed` Claim 覆盖
Reviewer finding。人工随后批准 Plan revision 2，只增加同批次竞态测试要求与后端验证，
没有扩大产品文件范围。

第二条 child `20260818-233820-287105-bug-loop` 绑定 operation
`899b517a30514f8891ff3148c2fcbc9f`。Worker 只修改批准的测试文件，在同一个 React `act`
批次内连续派发首次、重复和交叉提交。Worker 还报告了一次负向突变检查：临时移除同步 ref
守卫后，两条新测试均观察到 API 被调用 `3` 次；该自述只作为 Claim 保存，不替代后续门禁。

当前 Workspace 的确定性验证为：

- 后端完整测试：`361 passed`；
- 设置页定向测试：`7 passed`；
- 前端完整测试：`14` 个测试文件、`180 passed`；
- `tsconfig.app.json` 与 `tsconfig.node.json` 类型检查通过；
- Vite 输出到受控 verification 临时目录，构建通过；
- `git diff --check` 通过；
- 测试前后 ignored Workspace 指纹一致，没有新增未知文件。

Risk Gate 将两个设置页文件识别为前端并发与异步风险，独立 Reviewer 复核同批次重复提交、
交叉提交、成功恢复和失败恢复后返回 `approve`，没有 finding。Machine Observation
`observation-12c501123134` 记录 `work_item_completed=true`、
`worker_alive=false`、`external_side_effects=none`；Supervisor 选择 `finalize`。

最终父状态为：

```text
agent_run = 20260818-231923-agent-resume
plan_revision = 2
checkpoint = checkpoint-006
phase = completed
verification = passed
risk = passed
reviewer = approve
terminal_status = ready_to_commit
finish_sha256 = 979b1cee1fc342b6086953f93d217dc0ffbea9b7ec2b901b4a6220c7c3a0b977
```

人工随后归档目标仓库任务记录、删除临时 `.vega.yaml` 和 Task Card，重新执行相同的后端、
前端、类型检查、隔离构建与 Diff 门禁，并通过 PR `#1` Squash Merge 到目标主线提交
`593007bc9aa9667f37e74658a1085b1c0e37ac87`。Vega 没有执行 commit、push、PR 或 merge。

本 Case 判定为：

`git-only-resume-pass / provider-failure-fail-closed / reviewer-rejection-pass /
human-replan-pass / full-core-pass / target-pr-merged / release-acceptance-pass`。

这项结果证明单 Work Item 的真实 WIP 可以只经 Git Task Card 在独立 clone 中恢复，并允许
Reviewer 推翻 Worker Claim、要求人工修订 Plan 后重新完成 Core Gate。它不证明多 Work Item
自治、物理机安全隔离、通用 Provider 稳定性、Claude Code 原生 Writer 或无人值守长时间运行。

## 2026-08-19 v0.2.0 发布门禁与精确 Tag 复核

v0.2.0 发布候选 PR `#73` 的精确 HEAD `8a9950576c0e0e45013d00e95d789a3925ea204f`
通过 workflow `32207704764` 的全部 9 项检查。Squash Merge 后，`main@2fb1bd856df55907a4d3ef1039ea62658b30b2b4`
再次通过 workflow `32208196425` 的全部 9 项检查：

- 静态检查与分片覆盖；
- Python 3.11 兼容性；
- Python 3.12 四个测试分片；
- Windows 专项与 wheel smoke；
- POSIX 临时目录专项；
- 构建并安装 wheel。

annotated Tag `v0.2.0` 不可移动，解除引用后指向
`2fb1bd856df55907a4d3ef1039ea62658b30b2b4`。从该精确 Tag 重新构建的 wheel 和 sdist
均通过 Twine 检查；源码树外的 base wheel、`agent` extra wheel 和 sdist 安装均通过
版本、`vega list-loops`、`vega agent capabilities` 与依赖检查。此次精确 Tag 本机复核的
制品摘要为：

```text
wheel sha256 = 2AB3C17F13DD985C78C4303D315B04868F627F65E84E04509B31CFDFBCDDC840
sdist sha256 = 415DFCDBFB559ECA4DDB220942CC5B4EB106E50121B950806EC6792A714A9D1F
```

GitHub Release `Vega v0.2.0` 已公开发布。发布后删除实现分支
`release/v0.2.0`；SAG3B-03 的未完成 WIP 仅由不可移动 Tag
`archive/sag3b-03-wip-20260816` 保留，旧远端实验分支已删除。文档候选状态在本次发布
完成后更新为已发布，不改变前述历史实验结果。

## 2026-08-24 Supervisor Agent：Echo Vault 验证环境修复后复用原 Worker

本条记录验证专用恢复的真实任务，不覆盖前述 Case。目标仓库从固定提交
`65627d99c550e6615549baa9ce2d56d2ae16b21c` 建立 detached worktree，任务是继续收紧
Responses 流 `response.completed` 的成功状态判断，并补流式解析与失败持久化回归。

- Agent run：`20260824-134628-agent`；
- child：`20260824-134704-130098-bug-loop`；
- 原 Worker operation：`fdb70ead797e40b3b0ed9f28ab9a581d`；
- 验证恢复 operation：`719b3c90893c427b8e918b329ced934a`。

真实 Coding Worker 只修改三个批准文件：

- `.trellis/spec/backend/error-handling.md`；
- `backend/app/ai_client.py`；
- `backend/tests/test_chat_usage_and_export.py`。

第一轮后端定向测试、后端完整测试和 `git diff --check` 通过；前端 worktree 尚未安装
`node_modules`，因此固定版本的 pnpm 测试和构建分别因找不到 `vitest`、`tsc` 失败。
Risk Gate 仍成功，独立 Reviewer 返回 `request_changes`，Supervisor 选择 `replan`，没有把
Worker 的完成 Claim 或后端测试结果升级为成功。

人工只补齐前端依赖环境，没有修改 tracked Diff。Plan revision 2 保持目标、范围、风险和
验证命令不变，经重新批准后运行 `vega agent retry-verification`。Vega 重新校验原 Worker
execution、原审查快照、HEAD、tracked Diff、未跟踪文件和 Git 控制状态，以当前 ignored
环境建立 Core 基线，在同一个 child 追加 iteration 2；没有启动第二个 Coding Worker。

第二轮五条验证命令全部通过，Risk Gate 为 low，独立 Reviewer 覆盖三个实际变更文件并返回
`approve`。父 Agent 最终为：

```text
phase = completed
plan_revision = 2
checkpoint = checkpoint-006
verification = passed
risk = passed
reviewer = approve
terminal_status = ready_to_commit
finish_sha256 = 9908ede760876e71abe3f6c3fcbaeb9b9f0f00d996c176af298322f2f18f8759
```

Vega 没有对目标仓库执行 commit、push、PR 或 merge。该 Case 证明验证环境可以在不改源码、
不覆盖旧失败 iteration、也不启动第二个 Coding Worker 的前提下恢复；它不证明未知外部
副作用可安全重放，也不把 ignored 环境变化当作 tracked 代码未变化之外的安全结论。

## 2026-08-25 源码治理复验：Echo Vault Reviewer 打回

本条使用第三轮源码治理后的本地工作树，在固定提交
`65627d99c550e6615549baa9ce2d56d2ae16b21c` 上复跑前一条同等级任务。目标不是重复证明
Echo Vault 修复正确，而是检查共用 post-worker 链和集中 operation 身份后，Supervisor
仍能保留 Worker、Verification、Risk、Reviewer 与恢复边界。

第一次准备 run `20260825-130616-agent` 时，隔离 worktree 通过 Junction 复用依赖目录。
Workspace inventory 无法完整读取 ignored Junction，child
`20260825-130633-263834-bug-loop` 在 Worker 启动前停于
`workspace_baseline_unavailable`。没有绑定 Worker，也没有产生 tracked Diff。随后改用
worktree 内独立依赖目录，重新捕获完整基线。

正式复验使用：

- Agent run：`20260825-131206-agent`；
- child：`20260825-131315-571682-bug-loop`；
- Worker operation：`834a2195d5b64c748c7847c57664e551`；
- 验证恢复 operation：`45da552e642f4fb18b00b50a80aed93f`。

真实 Worker 修改三个批准文件：

- `.trellis/spec/backend/error-handling.md`；
- `backend/app/ai_client.py`；
- `backend/tests/test_chat_usage_and_export.py`。

第一轮后端定向测试、后端完整测试和 `git diff --check` 通过。直接复制的 pnpm 依赖目录没有
保留正确链接，前端测试和构建失败；Reviewer 返回 `needs_human`，父 Agent 保持
`needs_human`。人工只在 ignored 目录重新执行固定版本 pnpm 安装，没有修改 tracked Diff；
前端测试 180 项和构建随后单独通过。

Plan revision 2 保持任务目标、允许路径、风险和五条验证命令不变。运行
`vega agent retry-verification` 后，Vega 复用原 Worker execution 和三文件 Diff，在同一 child
追加 iteration 2。五条验证全部通过，Risk Gate 为 low。独立 Reviewer 没有批准：它指出
`response.completed` 的 payload 即使带非空 `error`，当前补丁仍会接受
`status=completed`，与项目错误处理规范冲突，并要求补解析及失败持久化回归。

父 Agent 最终保留为：

```text
phase = planning
plan_revision = 2
checkpoint = checkpoint-005
verification = passed
risk = passed
reviewer = request_changes
finish_status = needs_human
finish_sha256 = 6e96dfd12c135460db876ca1f98eda508eaaccae2ef4410e58af19c4a569b6f8
```

该复验没有得到 `ready_to_commit`，也不应包装成成功案例。它证明第三轮修改后的真实链路能够
写入两类不可变 operation Artifact、复用原 Worker 完成验证恢复，并在全部命令通过后继续接受
Reviewer 的具体打回，而不是把验证通过等同于代码正确。目标仓库仍保留未提交 Diff；Vega
没有执行 commit、push、PR 或 merge。

## 2026-08-28 v0.3.0 持久交互式 Agent Dogfood

本条使用一个可丢弃的两 Work Item Python 仓库验证 v0.3.0 候选。任务是让标签解析按首次
出现顺序去重，再增加复用解析器的摘要格式化函数。批准合同允许修改 `tag_tools/**`、
`tests/**` 和 `README.md`，禁止修改项目策略与 Agent 规则；人工只执行 `start`、`approve`、
`run`，并在首个 Worker Turn 运行期间发送一次 Steer，没有在 Worker 与 Reviewer 之间转贴
消息。

正式成功前保留了三次失败现场：

1. run `20260828-193143-agent` 暴露 Windows 子进程可能把 MCP 启动参数写入终端标题。
   人工立即中断；Vega 转为 `needs_human`，没有形成 Diff。
2. run `20260828-193700-agent` 证明 App Server Turn 完成后，长期 MCP 子进程仍可能占用
   owned process tree，导致外层 Worker 无法及时收尾。人工中断后保留两文件 WIP。
3. run `20260828-194712-agent` 发现 Contract 的最终验证被过早下发到 WI-01，引用了
   WI-02 才会创建的测试文件。确定性 Verification 拒绝通过；Repair Worker 没有产生新 Diff，
   Supervisor 因无法归因新的修复证据转 `needs_human`。

对应修复没有放宽门禁：Windows App Server 改为隐藏子窗口、丢弃可能包含敏感参数的原始
stderr，并在 Turn 结束后终止 App Server 进程树；Work Item 只运行当前项已经可执行的局部
验证，Contract 的 `required_verification` 与 Plan 的 `additional_checks` 延后到最后一项。
中间项没有局部验证时仍保守回退到合同验证。

修复后的 run `20260828-195732-agent` 首次完成整条链路。随后使用最终待提交源码重新执行
run `20260828-214004-agent`，得到相同终态，并作为本条最终验收依据：

- Worker 在 WI-01 与 WI-02 复用同一个 Provider Thread，共两个 Turn；
- Steer 在安全事件边界送达，状态记录为 `delivered`；
- WI-01、WI-02 各使用独立只读 Reviewer，累计 Candidate 另有一次独立集成审查；
- WI-01 的局部 pytest 与 `git diff --check` 通过；
- WI-02 的两组定向 pytest、完整 pytest 和 `git diff --check` 全部通过；
- 最终 Candidate 修改 5 个文件，`33 insertions(+), 2 deletions(-)`；
- Verification、Risk、Work Item Reviewer 与集成 Reviewer 均通过；
- `agent-final-report.json` 由已有 Git、Gate 和 Reviewer Artifact 确定性生成；
- 父状态为 `completed / ready_to_commit`，Vega 没有执行 commit、push、PR 或 merge。

本 Case 判定为：

`terminal-leak-found-and-fixed / process-tree-cleanup-pass /
work-item-verification-boundary-pass / persistent-worker-thread-pass /
isolated-reviewers-pass / interactive-steer-pass / multi-item-agent-pass /
deterministic-final-report-pass`。

它证明当前 Codex App Server 路径可以完成两个顺序 Work Item、持久 Worker、隔离审查和主会话
干预。它不证明长时间压缩后的语义保持、Claude Code Provider、未知外部副作用重放、高风险
生产变更或无人值守跨天运行。

## 2026-08-30 v0.3.1 最终候选真实 Agent smoke

本条使用提交 `5ed4d2165a68023ee3ab8a4ee40a12c0731a2a6a` 构建的 wheel，在一个无远端、
可丢弃的 Python 仓库中运行真实 Codex App Server ChangeRun。基线包含两个失败测试：
名称两端空格未清理，纯空白名称没有回退到 `world`。批准合同只允许修改
`src/hello.py`，禁止修改测试和项目策略，自动 Repair、Replan 和验证重试预算均为 0。

- Agent run：`20260830-135650-agent`；
- child：`20260830-135801-892730-bug-loop`；
- 基线：`5571bb107dc2847ea7b6b6881f33818dbc96166b`；
- Accepted Candidate：`9e18943e2aab8b138742e676fe5919780be4bfaa`；
- Worker 与 Reviewer 分别使用独立 Provider Thread，Thread ID 不同；
- `watch --follow` 显示 Worker、Verification、Reviewer、Supervisor 和最终终态事件；
- Worker 只修改 `src/hello.py`，共 `2 insertions(+), 1 deletion(-)`；
- `python -m pytest -q` 为 `2 passed`，`git diff --check` 通过；
- Scope Gate、Risk Gate、Reviewer 和 Artifact integrity 均通过；
- Reviewer 完整覆盖唯一变更文件，返回 `approve`；
- 父状态为 `completed / ready_to_commit`，owned process 没有残留。

运行过程中没有在 Worker 与 Reviewer 之间转贴消息，也没有对目标仓库执行 push、PR、merge、
部署或外部写入。源仓库仍停在原基线，候选只存在于 Vega 管理的本地任务分支。

这项 smoke 只证明 v0.3.1 最终候选的真实
Worker → Candidate → Verification → Reviewer → Finish 基础链路仍可用。多 Work Item、
Steer 和 Worker Thread 复用由 2026-08-28 Dogfood 覆盖；LF/CRLF、Git mode、重复 Resume
Claim 和内容漂移由 `VALID-02` 自动化回归覆盖。本条不证明通用成功率、生产安全、
Claude Code Provider 或未知外部副作用可安全重放。

## 2026-08-31 AUTONOMY-01 真实只读 Planning Proposal

本条使用一个可丢弃的三文件 Python 仓库验证自然语言 Planning 入口。固定基线
`1a057d53a05af9ead0c194be3392bbcfb7dee83d` 中，`calculate_total` 只累加 `price`，
现有测试要求 `price * quantity`。输入只描述 Bug 现象，并明确本轮只调查、不修改代码。

真实 run `20260831-194722-3b0e9a983243-agent` 保留了实施中暴露的三类问题：

1. Planning 持有父 run mutation lock 时，App Server 无法写 Provider Session；实现改为
   调查 Turn 前后分别锁定状态，外部进程运行期间释放生命周期锁。
2. 第一份结构化输出在 `file`、`test` 引用中同时给出 symbol；Schema 原先把这种有效定位
   误判为非法。
3. Planner 把原始任务中的“只调查”从建议合同目标中剥离；原先要求两个目标逐字相等，
   导致有效 Proposal 被拒绝。

上述失败都停在 `planning`，没有启动 Worker，也没有修改目标 Worktree。修复后的同一
Provider Thread 生成 `planning-proposal.json` 与 `planning-proposal.md`，包含 8 条观察事实、
2 条根因假设、4 个未决问题、允许和禁止路径、验证建议及 2 个 Work Item。再次执行同一 run
只复核已发布 Artifact，不新增 Turn。

另一个真实 App Server smoke 在同一 Thread 上先确认 `read-only / approvalPolicy=never`，
再恢复为 `workspace-write / approvalPolicy=on-request`；两次返回的 Thread ID 一致，实际
sandbox 与审批策略均由 App Server 响应核对，目标仓库保持干净。

本 Case 只证明自然语言目标可以进入只读调查并形成待编译 Proposal，也证明 Codex App Server
的同 Thread 权限切换在当前版本可观察。它不证明 Proposal 已经成为 Approved Contract，不启动
Worker，也不覆盖 Contract Compiler、有界自动批准、Claude Code Provider 或生产任务成功率。

## 2026-09-01 AUTONOMY-01 停止恢复修复后复验

本条在 AUTONOMY-01 停止、恢复和跨机交接修复后的工作树上，使用可丢弃 Python 仓库
`da63f3f9422af6588bdef792cede54aadc35e7bb` 复验真实 Codex App Server。自然语言只描述
“商品数量大于 1 时总价偏低”，并要求本轮只读调查。

run `20260901-012425-9d38ad49f5b9-agent` 的第一次 Turn 因模型改写 `user_goal` 被确定性校验
拒绝，状态停在 `planning`，没有发布 Proposal，也没有修改目标仓库。通过 Steer 明确要求逐字
保留原始目标后，同一 Thread 的第二次 Turn 生成完整 Proposal：8 条观察事实、2 条假设、
4 个未决问题和 2 个 Work Item。目标仓库的 HEAD 与工作区保持不变。

随后复用该 Thread 执行权限切换 smoke。App Server 返回并由 Vega 核对：

- Thread ID 与只读调查阶段一致；
- sandbox 从 `read-only` 切换为 `workspace-write`；
- approval policy 从 `never` 切换为 `on-request`；
- `permissions_verified = true`；
- 第三个 Turn 结束后目标仓库仍然干净。

本次复验证明无效 Proposal 会 fail-closed，同一 Planning Thread 可以重试，并能在服务端明确
确认权限后切换到受控写入。它仍不等于批准合同，也没有启动 Worker、创建 Candidate、提交、
Push 或合并。

## 2026-09-01 AUTONOMY-05 有界自主执行真实验收

本条使用三个无远端、可丢弃的 Python 仓库和一个中断夹具，验证从自然语言 Planning 到
Bounded/Human 批准、自动 Repair、高风险人工门禁、原生 Provider 压缩、中断和换目录恢复。
完整预注册与逐项结果见 [`autonomy-05-real-agent.md`](autonomy-05-real-agent.md)。

真实运行先暴露四个控制面问题：Planner 会改写任务身份或伪造验证命令；首次批准前无法 revision；
最终 Reviewer 会把只读环境不能重跑 pytest 误判为证据不足；多 Work Item 的路径投影无法可靠
归因 Repair WIP。实现分别收紧 Planning 合同、允许未批准 revision、向最终 Reviewer 传递
Candidate 绑定的机器 Gate 状态，并把 Work Item 路径缩小到自身 `likely_files`。这些修复没有
放宽 Verification、Risk 或 Reviewer 门禁。

最终验收结果：

- Bounded run `20260901-181253-ccd9aff16132-agent` 无需人工 `approve`，只修改两个允许文件，
  终态为 `completed / ready_to_commit`；
- Human run `20260901-200144-4b371e8e9ac3-agent` 的 Reviewer 真实返回
  `request_changes`，Supervisor 自动生成 Fix Packet 并在同一 Worker Thread 的下一 Turn
  修复，最终 Candidate 为 `532ebe7048775ba17e1365f614de5a9256033516`；
- 高风险 run `20260901-201536-35b9796e7519-agent` 被 Bounded Policy 拒绝自动批准，人工批准
  后仍因 Migration 必审风险停在 `needs_human`；
- Task Card 经本地任务分支和本地裸仓库带到另一个目录，`vega resume --repo .` 恢复
  Contract revision 1、Plan revision 3 和 `WI-01`，但不会复用历史 Gate 作为当前通过证据；
- A05-02 的真实 Worker Thread 完成一次 App Server 原生压缩，下一 Turn 仍绑定正确 run、
  Contract、Plan、Work Item 和 Accepted Checkpoint；
- 中断 run `20260901-203654-543fded1271a-agent` 在真实 Worker pytest 期间收到精确 stop
  request，保留 partial diff，转 `needs_human`，没有第二 Writer 或自动重放。

开发过程中的失败 run 和未满足强证据条件的“成功”也保留在专项记录中，没有纳入通过计数。当前
结果支持准备 `v0.4.0` 候选，但不证明 Claude Code Adapter、生产数据库写入、未知外部副作用
重放或通用任务成功率。完成事件仍须随同一提交通过完整基线、包安装 Smoke 和 PR CI 后进入
`main`。
