# 日用改造验收记录（2026-09-17）

本记录整理当前开发候选，不代表已提交、发布或本批 PR CI 通过；DAILY-02 本地验收完成，完成事件与实现同 PR 提交，须经 CI 通过并合并后才成为主线事实。
DAILY-03 的实现与回归不等于真实高风险任务验收，不能由低风险任务推断。

| 范围 | 已核验事实 | 未覆盖或限制 |
| --- | --- | --- |
| 补验与审查输入 | 定向补验整链确认 Candidate 不变、Worker 仍1次、独立 WI Reviewer 总2次、旧 Review 保留；报告与原集成审查触发条件共10个不同参数最终通过 | 使用确定性测试，不是所有项目的真实工具验收 |
| Review Queue 分类 | `tests/core/test_review_queue.py::test_queue_preserves_only_complete_acceptance_blockers`：6 passed，0.40秒；混合人工阻断及覆盖不完整不授予补验资格 | 不自动分类历史未分类 Review |
| 正常主会话日用 | 附件上传期间停止生成任务：Worker 1次、Reviewer 1次，approve；固定组件测试5项、定向测试11项、build、diffcheck均通过；批准后额外用户操作0次 | 可控组件验收，不是真实付费聊天或生产验收 |
| BOM 真实任务 | 从基线 `8d9aa246` 创建独立任务，Candidate `0e34a1ef3ed63be88000ff2c11f24fe14533a322`；仅 start 读取器与对应测试，Worker 1次、独立只读 Reviewer 1次，无 Repair，completed/ready_to_commit | 控制器使用当时未提交开发源码；不是此整批 WIP 的提交或 CI 证据 |
| BOM 固定验证 | 20 passed/29.71秒；两文件 Ruff、diffcheck、架构、卫生均 exit0；任务创建至终态约5分9秒，批准后额外人工操作0次 | 旧候选20参数证据保留，不冒充后续精简测试结果 |
| 手工整合及状态显示 | BOM 整合为8个明确参数；状态过渡11个轻量参数通过，JSON门禁与允许动作不变；最终 Ruff、架构、卫生、diffcheck通过 | 首次新渲染fixture缺目标身份失败，修正后通过；包含诊断的pytest累计33.25秒，未跑全量或跨平台 |
| 高风险范围内返修 | 当前合同有显式默认保守授权及相应测试；最终人工风险门禁不解除 | 本记录不声明真实高风险 request_changes→repair→approve→human 已实测；DAILY-03 正常日用及本批 CI 仍待验收 |

公开可复查入口：`tests/supervisor/test_agent_reviewer_timeout_retry.py`、
`tests/supervisor/test_agent_change_run.py`、`tests/supervisor/test_agent_live_child_status.py`、
`tests/core/test_review_queue.py`。本地原始材料保留在
`.local-validation/acceptance-resubmit-0916/`、`.local-validation/daily-review-streamline-0916/`、
`.local-validation/daily-upload-stop-retry-0916/`、`.local-validation/daily-small-fixes-0917/`；
BOM 原始 Run 位于独立运行 workspace 的 `runs/20260916-233832-e3673d0ef979-agent/`。
这些本机材料不随 Git 提交，也不把 Worker 自述升级为控制器验证结果。

DAILY-02 完成事件与实现同 PR 提交，把 `pr-ci` 列为合并门禁，
不声称已运行；通过 CI 并合并后才成为主线事实。DAILY-03 的实现仍进行中、真实高风险
日用尚未验收；机器 started 事件与 DAILY-02 完成事件一同提交，保留进行中。

本轮审查修复：补验审查后缺失/篡改拒绝、40/64位 Git OID、Candidate 父绑定、Reviewer timeout 传递的17个定向参数最终通过；直接相关辅助职责抽取后架构增长门禁通过；抽取影响的5项回归34.06秒通过。尚未运行本批PR CI，不能当作已合并结论。
