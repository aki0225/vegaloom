# 测试规则

## 目录职责

- `core/`：内部 Workspace、验证、风险、Reviewer、Finish、进程和通用 Artifact 合同。
- `supervisor/`：公开 ChangeRun 的 Plan、单 Writer、持久会话、状态、恢复、交接和 Provider Adapter。
- `security/`：路径边界、证据完整性、脱敏、成功语义及 fail-closed 负向合同。
- `experimental/`：Goal、Memory、Assurance、Inspection、RCB、Showcase 与历史实验重放。

`tests/core/` 不得静态导入 `vega.experimental`。需要验证 Experimental 与 Core 的组合行为时，
测试归入 `experimental/`；Security 可以为边界负向合同读取 Experimental，但不能把实验成功
语义并入 Core。

冻结的 `test_crwp_v1_control.py` 例外保留在 `tests/` 根目录：CRWP manifest 绑定了它的历史
路径、Git blob 和摘要。它属于 Experimental，普通 PR 不执行，不得为目录整齐改写冻结合同。

每个测试文件只归一个职责目录。跨多个领域时，归入拥有最终裁决或最高风险合同的目录；不要复制同一
场景到多个目录，也不要从其他测试模块导入 helper。

Planning Proposal、Contract Compiler、批准来源和 Provider Thread 切换属于 `supervisor/`；
只有路径越界、规则降级、写审隔离或成功语义的负向合同进入 `security/`。新事项只保护公开
Schema、拒绝路径和恢复边界，不为字段转发、私有 helper 或上游 Provider 内部行为重复写测试。

## 测试选择

- 先运行直接覆盖改动分支的最小 node id 组合；共享 fixture、导入边界或公开合同变化时，再
  扩大到完整测试文件和受影响职责目录。
- 普通 PR 由 CI 覆盖相关 Core、Supervisor 和 Security 分片；本地开发不机械串行重跑三个
  目录。Experimental 只在对应代码或历史材料变化时定向运行，并在 main、release 或手工全量
  验证中复核。
- 一项行为通常保留正常、危险或损坏、恢复三个代表场景。优先参数化相同机制，不为每个字段组合复制
  集成测试；历史真实故障和跨平台边界除外。
- 修复必须有可复现证据，可以是已有测试、新增的最小回归断言或确定性验证命令。只有现有覆盖
  无法长期保护故障时才新增测试；不得为字段转发、私有 helper 或已经覆盖的同一分支重复造案例。
- 单个测试用例原则上不超过 60 秒。完整文件或职责分片可以更长；需要拆分时按文件或 node id
  使用独立 `--basetemp`，不要为了满足形式上的时长限制复制测试。

根 `conftest.py` 只放所有职责共享的环境隔离。某一目录专用 fixture 放在该目录自己的
`conftest.py`，不得让实验 fixture 进入产品测试。
