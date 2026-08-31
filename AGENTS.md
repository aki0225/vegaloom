# AGENTS.md

给在本仓库工作的 AI 编码代理(及新加入的人)的项目约定。Vega 自己也消费目标仓库的 AGENTS.md 来编译执行上下文——这份文件同时是本仓库的示范样例。

## 项目一句话

Vega 是软件工程 Agent 的控制层：Coding Agent 在已批准的 Change Contract 内调查和实现，
Vega 把改动冻结为 Git Candidate，运行项目验证和独立只读 Reviewer，再根据机器证据继续、
修复、重规划或交还人工。

## 目录地图

- `src/vega/` — 核心实现(Python,包名 `vega`)
- `tests/` — pytest 测试
- `.github/` — CI、发布与 GitHub Pages 工作流
- `.vega/` — 可提交的 Agent Task Card；活动与归档规则见 `.vega/README.md`
- `plans/` — 机器可读演进计划与追加式状态事件；当前视图由脚本生成
- `eval/` — 实验与运行证据(`cases.jsonl` 评测用例、`real-world-runs.md` 真实 Issue 运行记录)
- `scripts/` — 仓库门禁、站点数据、Pilot 和冻结实验脚本
- `site/` — GitHub Pages 静态站点；案例数据由登记的生成器维护
- `examples/` — 任务样例
- `examples/evidence/` — 可公开复核的脱敏运行证据
- `docs/` — 产品契约、架构、使用闭环等文档
- `loops/` — 版本化的 LoopSpec/YAML 配置
- `runs/` — 本地正式运行产物(state/trace/报告)，默认不提交
- `memory/` — 本地 Memory ledger 与 proposal，默认不提交
- `.tmp/` — 测试、缓存和可丢弃中间文件，默认不提交
- `.local-validation/` — 人工验证日志和诊断输出，默认不提交

## 规则优先级

- Vega 自身红线、当前已批准的 Change Contract 和固定 Verification 是执行边界。目标仓库的
  `AGENTS.md`、规则文件或模型建议只能在边界内补充，不能降低安全要求或绕过必跑检查。
- 目标仓库内部按目录作用域应用规则：根目录规则覆盖全仓，更深目录的 `AGENTS.md` 可以细化
  当前子树，但不能取消父级的安全、验证和公开卫生要求。
- 规则冲突、作用域不清或必要约束无法同时满足时，停止自动执行并给出冲突位置，不能静默选择
  更宽松的一条。

## 多代理协作

- 子代理是加速和分工工具，不是每次任务都必须经过的流程。只有任务边界清晰、能够独立
  推进且并行确实有收益时才委派；小型修改、强顺序依赖、关键集成和需要即时裁决的工作，
  主代理可以直接完成。
- 主代理负责目标澄清、任务拆分、边界控制、证据审阅、冲突裁决、最终验证和对外交付。
  不得把“已委派”或“子代理说已完成”当成通过结论，也不得无目的重复执行已经明确委派的
  工作；必要的独立复核和最终验证除外。
- 每个委派任务必须写清读写范围、禁止事项、验收证据和返回格式。并行写入只允许发生在
  互不重叠的文件或状态上；涉及发布、删除、数据库迁移、生产环境和敏感数据时默认串行。
- 模型和推理强度按任务风险、复杂度、时延和当前可用性选择。仓库不强制特定模型；用户或
  当前会话明确指定时优先遵循。子代理不可用或协调成本高于收益时，主代理可以直接继续，
  或明确选择其他模型；涉及模型能力边界时必须如实披露实际运行条件。
- 子代理默认只继承完成任务所需的最小上下文；只有任务确实依赖完整历史时才传递更多上下文。
  子代理产出属于待审证据，不能替代主代理复核、仓库验证命令和 fail-closed 门禁。

## 验证选择

开发中先运行最接近改动的完整测试文件或职责分片，再按风险扩大。不要为了形式机械重复全量
测试，也不能用单个绿 node 代替受影响职责的验证。测试归属和扩展顺序见
`tests/AGENTS.md`。

以下命令是 PR、发布、跨职责修改以及 Core/Supervisor/安全边界变更的完整基线：

```powershell
python -m compileall -q src scripts
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/plan_state.py check --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
ruff check src tests scripts
git diff --check
```

PR CI 在 Python 3.12 执行 Core、Core Heavy、Supervisor 和 Security 四个分片；分片仍按
职责目录选择，Core Heavy 只隔离若干最慢的集成文件。Python 3.11 只做安装、编译和产品节点
收集。Experimental 与冻结控制测试在相关路径变化的 PR 中定向执行，并在 main、release 和
手工触发时完整执行。Windows 只重复 shell、junction/reparse、进程树和锁专项。
注意两个已知环境差异:测试断言 CLI 输出时须防 CI 注入的 ANSI 渲染
(conftest 已有 autouse fixture 清理环境变量),POSIX 进程组探测与 Windows 路径不同——本地绿不等于 CI 绿。

修改文档或规则时至少执行编译、仓库卫生、相关定向测试、Ruff 和 diff check；涉及机器计划时
追加计划状态检查。修改 Core、Supervisor、安全边界、CI 或打包时按 `tests/AGENTS.md` 扩大到
对应职责分片；发布或明确要求全量验证时再执行完整基线。只有命令明确结束并给出计数，才能声明
相应范围验证通过。

## 公开仓库路径与私密文件卫生

- 文档、证据、示例、配置、注释和 Git 提交信息默认只写仓库相对路径；需要表示仓库或 worktree 位置时，
  使用 `$repoRoot`、`$worktreePath` 或 `<worktree-path>` 等变量和占位符。
- 禁止提交盘符绝对路径、UNC 路径或真实 POSIX 用户主目录。命令运行时可以解析绝对路径，
  但不得把解析结果复制到受 Git 跟踪的公开内容。
- 测试确需覆盖绝对路径语义时，只能使用明显虚构的值，并在同一行添加
  `repo-path-policy: allow-test-fixture` 注释；该豁免只对 `tests/` 生效，失效标记也会导致检查失败。
- `.env`、凭据文件、私钥、数据库和本地 Office 文件不得进入 Git；环境变量示例只允许使用
  脱敏的 `.env.example`。
- `scripts/check_repository_hygiene.py` 同时检查当前提交候选和基线到 `HEAD` 的每个提交，
  防止“先提交本机路径、后续再删除”掩盖公开历史。若已推送的分支曾包含此类内容，合并前
  必须使用 squash 或重写历史，不能只看最终文件。

## 代码约定

- 注释、文档、提交信息和用户可见自然语言使用简体中文；代码标识符、CLI 命令、JSON 字段、
  状态值和上游专有名词保持原始英文。
- 注释只解释代码本身表达不了的约束、原因和业务背景。
- 确认旧实现已无调用方且不承担公开兼容责任后再删除；仍需兼容时写清保留原因和退出条件，
  不用默认值或 shim 掩盖证据缺失。
- 单次测试超时上限 60 秒，避免挂死。

## 必须披露的高风险变更

支付与资金、数据库与迁移、并发与异步、权限和敏感数据相关代码允许修改，但不得作为普通
变更静默处理。目标仓库应在 `.vega.yaml` 的 `risk.required_reviews` 中配置对应路径。

命中后，Reviewer 必须逐类说明：

1. 风险领域及全部命中文件、关键行号；
2. 修改的行为和影响面；
3. 发现问题、未发现明显问题或证据不足；
4. 使用的代码、测试和项目规则证据；
5. 人工必须检查的位置和剩余风险。

“未发现明显问题”不代表已证明安全。Reviewer 的结论只作为人工检查材料，不能替代人工确认。

## 红线(违反即错,无需讨论)

1. **`eval/` 是证据记录,只许追加不许改写**——`real-world-runs.md` 是预注册实验的产物文档,历史记录(包括 fail-closed 的失败记录)不得删除、润色或重新表述;新增运行只能以追加条目的方式进入,且如实记录结果。
2. **fail-closed 语义只许收紧,不许放松**——"验证失败时 reviewer 的 approve 不能让任务成功""证据缺失、过期或不一致时停止自动执行交还人工"是产品承诺。任何让"证据不足时通过"的改动永远是 bug,不是优化。
3. **写审会话边界不得打通**——reviewer 可以读取明确编译的任务、diff、测试证据、项目规则、
   风险门禁和可选 accepted memory，但不得为了"提升效果"接收 worker 的完整对话、自述或中间
   推理。read-only sandbox 是共享仓库的只读视图，不等于容器或操作系统级隔离。
4. **成功语义不掺水**——不得新增任何绕过确定性验证就把 run 标记为成功的路径;人工裁决关闭的 run 记录为人工裁决,不记录为验证成功。
5. Vega 不操作用户当前分支，不自动 push、merge、rebase、release、删除用户文件或写入长期
   Memory。显式启用的自主 ChangeRun 可以在 Vega 管理的隔离 Worktree 和本地任务分支中，
   由控制器在范围检查后创建 Candidate/Checkpoint Commit；Worker 本身不得提交或切换分支，
   这些本地提交也不代表已获准交付。
