# Vega 日用价值配对实验 V2

> 2026-07-29 supersession：V2 可执行 Harness 与配套测试已因过度设计删除。
> 本文只保留为未执行的历史预注册，原脚本命令已从正文移除。后续若继续日用价值验证，
> 只能重新冻结一个最小 case，并直接复用 task、允许路径和固定 verifier，不恢复本 Harness。

状态：`historical_design / harness_retired / no_treatment_started`。

V2 不是给 Vega 增加功能，而是修复 V1 暴露出的实验误差。DV-B02 与 DV-B04 的 V1 结果
保持封存，不重跑、不改写，也不用于证明 Vega 已有日用收益。

## 1. V2 只修四件事

1. Native 与 Vega 必须使用同一个 Python 测试环境，并绑定同一个
   `environment_fingerprint`。
2. Provider 调用前必须通过 `pip check`、目标 pytest 收集和本地控制命令延迟门禁。
3. Codex JSONL 事件在被本地驱动器读取时立即补充 `received_at`，不再只保存运行结束后的
   无时间戳输出。
4. 结果分别记录 Runtime verification 与 post-seal verifier，并把 Owner 人工操作和脚本
   自动操作拆开。

不增加 SDK、队列、Web UI、Memory、Multi-Worker、A2A，也不修改 Vega 核心 Runtime。

## 2. 历史共用环境资格门

历史设计要求正式运行前生成一份本地环境资格结果，并同时满足：

- 明确指定一个已存在的 Python executable；
- `pip check` 通过；
- 冻结的目标 pytest 切片能够 `--collect-only`，且至少收集一个节点；
- Python 启动与 `git status --untracked-files=no` 均未超过预注册延迟上限；
- 当前没有另一个正式 treatment；
- Owner 没有观察到会影响比较的重负载工作；
- 第二个 treatment 的 `environment_fingerprint` 与第一个完全一致。

`environment_fingerprint` 只绑定 Python 实现、版本、ABI、平台和已安装分发包的名称与版本，
不绑定本机绝对路径。资格检查不调用 Provider；任一门禁失败都应返回 `status=blocked`。

延迟上限属于 case 合同，不能看到运行结果后再放宽。

对应可执行 Harness 已删除，本文不再提供命令，也不授权恢复同等入口。

## 3. 历史 Worker 事件设计

已退役的 Worker 驱动原计划只负责一次正式 Worker：

- 要求命令显式包含 `--json`；
- 拒绝 `--ignore-user-config` 和绕过 sandbox 的危险参数；
- 要求 preflight 为 `ready`；
- 发现既有 event、stderr 或 result 产物时拒绝隐藏重跑；
- 给每行 JSONL 增加单调 `sequence` 和 UTC `received_at`；
- 非法 JSON 行只记录内容 hash，并增加 `invalid_event_count`；
- 超时只终止当前驱动器明确创建的进程树。

`received_at` 是本地驱动器收到事件的时间，不是模型内部思考时间。它只能用来判断长时间空窗
发生在事件之间，不能精确拆分 Provider、CLI、Shell 或 Harness 的内部耗时。

该驱动器已经删除，不能把本节当作可执行入口，也不应为保留时间戳字段重新实现一套 Harness。

## 4. V2 结果字段

V2 使用独立结果文件，`schema_version=2` 且 `experiment_version=V2`。V1 的
`results.jsonl` 不修改。

关键字段：

- `runtime_verification_status`：Vega Runtime 内部验证；Native 固定为
  `not_applicable`。
- `post_seal_verification_status`：Worker 结束或被停止后，对封存 workspace 运行的共同
  verifier。
- `owner_manual_actions`：正式 treatment 启动后，Owner 为推进、搬运、重启或恢复而执行的
  明确人工动作。阅读最终报告不计入。
- `automation_actions`：驱动脚本自动执行的资格检查、封存和验证步骤，只作追溯，不与
  `owner_manual_actions` 混算。
- `environment_fingerprint`：两组必须一致。
- `event_timing`：事件数、非法事件数以及首尾 `received_at`。
- `preflight_ref`、`event_log_ref`：仓库相对或证据根目录相对引用，不得写本机绝对路径。

`success` 必须同时满足：

1. `run_status=completed`；
2. `post_seal_verification_status=passed`；
3. `reviewer_verdict=approve`。

Vega 的 Runtime verification 即使通过，也不能覆盖 post-seal 失败；Native 的封存
verifier 通过，也不能把超时 Worker 改写为成功。

## 5. 正式启动条件

只有满足以下条件才允许登记第一个 V2 case：

1. 选择新的 case 或明确登记新的实验版本，不能把 V1 隐藏重跑伪装成原结果；
2. 两组共用环境已安装完整测试依赖；
3. 两组 preflight 均为 `ready` 且 fingerprint 相同；
4. case 合同已固定模型、reasoning、timeout、允许路径、测试切片和延迟上限；
5. Worker 与 Reviewer 均有独立上下文合同；
6. `owner_manual_actions` 的计数人和计数起点已写入运行记录。

至少获得一个“两个 Worker 正常返回、共同 verifier 均执行、两个 Reviewer 均完成”的干净
pair 前，不扩建 Harness，不形成日用价值结论。

## 6. 当前证据边界

2026-07-29 的本地诊断确认：pytest 收集本身正常，800 个节点约 2.32 秒完成；有界执行卡在
风险证据重算期间的 Git 子进程，同机存在多个 Codex、Python 和 Node 进程。该结果支持增加
环境与控制延迟门禁，不支持放宽 Runtime 超时或把 V1 timeout 归因给 Vega。

历史定向测试只证明当时的 V2 Harness 能检查这些输入条件，不代表当前仍有可执行基础设施，
也不增加 Vega 产品能力。V2 从未运行正式 pair，因此结论保持 `insufficient_evidence`。
