# Worker 执行权限改进计划

用户已批准本有限实施计划；按 EXEC-01、EXEC-02 分段实施并由主控独立验收，不表示全部能力已完成。
机器事项顺序：`DAILY-01 → EXEC-01 → EXEC-02`。沿用现有 Agent 和 Codex/Claude 适配，
不换底座、Runtime，不新建权限引擎或审批 Agent。PR #119 不属于本计划动作范围。

## 实施前核对的缺口与复用边界

- `src/vega/codex_app_server.py:85` 在 `thread/start`、`thread/resume` 共用路径中，
  固定非只读 Worker 为 `on-request`；`codex_app_server_permissions.py:29` 同样固定校验值。
- `src/vega/agent_cli_interaction.py` 只凭脱敏摘要不能证明知情授权，返回 `attention`；
  `agent_change_execution.py` 随即请求停止。不能简单删除停止而无限等待或凭摘要批准。
- `src/vega/provider_session.py::respond_to_interaction` 已在 mutation lock 内校验
  pending、owner、权限验证及 Thread/Turn 绑定；底层已有显式响应路径，优先复用。
- `src/vega/claude_code_runner.py` 已限定原生工具与权限模式并核验初始化；保留兼容边界。

## EXEC-01：尊重已选择的有效执行权限

| 实施范围 | 可执行验收条件 |
|---|---|
| 权限来源与选择 | 优先获取可验证的当前主会话有效 sandbox、approval、reviewer 及必要实际范围；完整继承，不把用户 config 当主会话事实。无可信接口时改为用户一次显式选择执行配置，明确不是自动继承；来源缺失不得猜 Full Access。 |
| Codex 原生语义 | 去掉非只读 Worker 固定 `on-request`；auto_review、Full Access、人工询问各保留原生语义，尊重组织限制。不吞不支持项或静默降级；请求值与实际生效值不一致则停止并说明。 |
| 启动与恢复 | 两条路径均应用配置并验证实际权限及必要范围；复用已有会话所有权与恢复绑定，禁止另一端同时控制活动 Worker。旧 Run 显式选择权限仅适用于当前仍允许执行的协议版本；缺可信权限来源时先显式选择并重新核验，不默认已验证。已退役协议的只读/停止/可信交接限制保持不变，不因选择权限恢复执行。 |
| 角色与授权 | Reviewer 独立只读、Planning 只读调查不变。执行权限不扩大 Change Contract、任务批准、提交或发布授权；单 Writer、固定验证对应 Candidate、写审隔离及成功语义不变。 |
| Claude 兼容 | 只映射可验证的原生支持，未知组合明确拒绝；不宣称与 Codex 的 auto_review 等机制等价，本轮仅做兼容回归。 |

Full Access 下的 Worktree 不是 OS 隔离；事后 Diff 检查不能阻止外部副作用。
权限变化只调整直接受影响的传递、核验与绑定，不顺手重写全部状态、恢复协议或指纹。
实现起点是现有 Provider 配置/调用链、App Server 线程权限校验与 Session 绑定；具体代码
写范围在用户过目后按调用链确认，不以本计划授权全仓修改。

## EXEC-02：接通有限的原生审批响应

| 实施范围 | 可执行验收条件 |
|---|---|
| 支持类型 | 仅 Codex 确需用户处理的 command/file approval；有效请求等待不是失败，不得仅因出现 pending 就停止 attempt，不自动接受。 |
| 知情展示 | 本机可查看实际 command/cwd 或文件变更及请求绑定；临时授权展示与脱敏持久审计分开，不为方便把密钥、原始 payload 写入 Git 或持久审计。只有脱敏摘要不能批准。 |
| 请求生命周期 | 复用现有请求-响应、进度/steer接口与锁内校验；一次响应绑定当前角色、owner、Thread/Turn 和请求，重复、过期或换会话请求不能发送；不自动转送旧授权。 |
| 拒绝与不支持 | 拒绝保留原生拒绝语义，不升级权限。信息缺失，以及权限、网络、MCP 等本轮不支持请求明确返回边界，不自动接受。 |
| 停止与输出 | stop/中断结束等待并确认退出；TTY 才进行知情交互。非TTY/JSON 不读 stdin、不泄露原始 payload、不无限等待用户，复用既有安全停止与结构化边界。 |

只修改这条路径直接受影响的行为，不重做控制台或通用恢复。无法提供安全临时展示或
不能证明响应绑定时保留明确边界，不用“减少打断”取消现有保护。

## 实现阶段的定向验证白名单

以下是已读取的现有节点，表内路径均相对仓库根；执行形式为
`python -m pytest <文件>::<函数> ...`。按实际受影响行为选择，不机械全部重跑。
旧拒绝节点保留缺信息场景；新知情响应不能通过弱化旧安全断言实现。

| 文件 | 现有函数（可复用或扩展） | 保护行为 |
|---|---|---|
| `tests/supervisor/test_codex_app_server.py` | `test_app_server_rejects_unverified_read_only_permission` | 实际权限不符拒绝，Reviewer只读边界 |
| 同上 | `test_provider_session_resets_on_contract_revision_and_has_one_owner` | 恢复与会话所有权边界；不等于已覆盖权限恢复 |
| 同上 | `test_app_server_waits_for_explicit_approval_response` | fake AppServer 显式响应，仅一次正确发送 |
| `tests/supervisor/test_agent_cli_interaction.py` | `test_interaction_pump_never_approves_from_redacted_friendly_summary` | 缺少知情上下文拒绝、持久状态脱敏 |
| 同上 | `test_interaction_pump_routes_unsafe_requests_to_advanced_path` | 不支持请求明确边界 |
| 同上 | `test_interaction_pump_rejects_stale_turn_binding` | 过期请求拒绝 |
| 同上 | `test_respond_to_interaction_rejects_unowned_handle_without_expected_provider` | 人工接管后不得代答 |
| 同上 | `test_interaction_pump_never_reads_non_tty_or_json_input` | 非交互边界；需补入口不读 stdin 的实际断言 |
| `tests/supervisor/test_agent_change_driver.py` | `test_change_stops_for_codex_interaction_that_requires_full_context` | 无完整上下文仍安全停止，不泄露敏感提示 |
| 同上 | `test_stop_request_closes_pending_provider_interactions` | stop 关闭 pending |
| 同上 | `test_change_json_never_reads_stdin_without_active_run` | JSON 入口错误输出脱敏；不替代活动审批验收 |
| `tests/supervisor/test_claude_code_provider.py` | `test_claude_init_must_match_fixed_permissions_and_tools` | 原生权限与工具校验兼容 |
| 同上 | `test_claude_reviewer_uses_independent_read_only_session` | 独立 Reviewer 会话兼容 |

尚缺的验收行为（不是声称存在的新 node）：

- EXEC-01：可信来源/显式选择的代表正常路径；来源缺失、未知组合、组织限制或实际值不符拒绝；
  启动/恢复权限绑定与必要范围核验；Reviewer 和 Planning 不继承 Worker 写权限。
- EXEC-02：fake AppServer 的完整 command/file 展示→一次响应→继续；原生拒绝不升级；
  展示后请求过期/重复不得发送；等待中 stop/中断确认退出；活动审批在非TTY/JSON下
  不读 stdin、不泄露、不无限等用户。优先扩展上述参数化或夹具，必要时才新增最小节点。
- 不写纯字段转发/helper 测试，不建全组合矩阵，不复制已绿测试；实现报告列准确最终 node。

## 预算、真实验收与交付

| 阶段 | 命令/范围与停止条件 |
|---|---|
| 本轮仅计划 | `python scripts/plan_state.py render`；随后仅 `python scripts/plan_state.py check --base-ref origin/main`、`python scripts/check_repository_hygiene.py --base-ref origin/main`、`git diff --check`。卫生不覆盖未跟踪文档时另做只读路径/密钥/UTF-8检查，不 git add。 |
| 实现本地回归 | 每条回归命令外层预算10分钟，整轮定向验证目标30分钟；到预算停止报告，不自动加时或改跑全量。单例仍遵守 tests 规则（原则60秒），不把全部用例 timeout 改成10分钟。 |
| 故障处理 | 超时或环境故障报告具体命令与原因，最多一次定位后的定向重试，计入总预算，不循环自我排障。 |
| 广覆盖 | 普通本地不跑全量 pytest、完整职责分片、全量打包、历史实验或 Echo 全套；PR CI负责广覆盖。按实际代码范围补受影响 Ruff、架构、卫生和计划门禁，不扩大测试矩阵。 |
| 唯一真实验收 | 只用一个现有受控 Codex 小任务，核验选定权限实际生效→Worker→对应 Candidate 的固定验证→独立 Reviewer→最终报告。单次 Provider timeout 900秒，总预算30分钟；多角色到预算即停报未完成。 |

auto_review 不可用则明确未验证，不悄改 Full Access。额外审批故障用 fake AppServer
确定性复现，不要求真实模型随机打回。Claude 本轮仅兼容回归，不声称真实验收通过。
定向测试与真实调用分别向用户报告范围、结果和未验证项，历史验证不能替代本次证据。

Writer 实施；主控只读验收 diff 和必要证据，不机械重跑已给精确证据的测试，只补缺口。
Worker 返回文件列表、完成验收项、实际命令/计数/退出码/耗时与未做项。
计划外一般问题只登记一句不修；错误授权、写审隔离破坏、数据损坏或证据失效放行，
立即停止报告，由用户决定是否扩范围，不顺手做安全大改造。未明确授权不提交、推送或合并。

## 明确不做与待决点

不做 Candidate→旧 assist/Core 桥接重构、删 AgentPlan/合并多层循环、自动 Replan、
多 Worker、新 Provider、权限配置平台、Memory、目录搬迁、UI 平台、自动提交/push/发布。
此前候选留待独立决定，不混入本轮。

未证实存在可信的宿主有效权限读取接口，已采用一次显式选择并绑定 Run，不宣传自动继承。
EXEC-01、EXEC-02 本地候选及唯一真实验收均已由主控独立审阅；当前仍是本地候选，
尚未进行本次实现的 PR CI、提交、push 或合并。准备 PR 时再按现有规则随实现加入完成事件，
本轮不提前生成事件，也不改历史事项或计划脚本。

## 本地验收摘要

- 权限为调用者显式选择的 `ask / auto-review / full-access`，不是自动继承宿主会话；
  两步本地候选已审阅，执行权限不扩大任务合同或交付授权。
- 定向测试累计外层耗时 **368.07 秒**。EXEC-01 首组18 failed / 8 passed，定位修正后
  仅重试失败项18 passed，补验5 passed（含1个重复节点），共30个不同参数结果通过；保留首败记录。
- EXEC-02 三组分别28、7、8次通过，均exit 0；含源码变化后重复运行的6次完整响应场景，
  不把43次通过当作43个不同场景。
- 唯一真实任务使用 Codex CLI **0.154.0**、显式 `full-access`，从验收开始到调用返回
  **287.91 秒、exit 0**；Worker实际为 `danger-full-access / never`，权限核验通过。
- Reviewer使用不同的只读Thread，实际 `read-only / never`，审查通过；本次Core固定
  **5 tests OK**及`git diff --check`均exit 0，最终`completed / ready_to_commit`。
- Candidate `2690aa13ca5fd9749fed9bb87f0c10e1682399d9`仅改`labels.py`（+10/-1）；
  源main保持原HEAD且干净，受管worktree干净，记录的调用器与Worker/Reviewer进程均已退出。
- 不宣称真实auto_review、TTY审批、Claude或跨平台验证通过；本地候选不等于PR CI或最终交付。
- 原始日志、计时、结果及运行证据保留在`.local-validation/exec-permissions/`，不提交这些产物。
