# Vega v0.6.0 发布说明

发布状态、制品和对应提交以 [GitHub Release](https://github.com/aki0225/vegaloom/releases/tag/v0.6.0)
为准。本文件与源码版本号不代表制品已经发布；发布前继续使用
[v0.5.1 及对应文档](https://github.com/aki0225/vegaloom/blob/v0.5.1/README.md)。

v0.6.0 统一了日常任务的继续入口，把环境准备放到 Worker 开始之前，并补齐状态与报告中的
缺失信息。本版本包含命令和执行协议变更，不是 v0.5.1 的兼容补丁。

## 主要变化

- `vega change` 负责新建、继续任务，以及符合资格的验证恢复；公开 `run`、`retry` 命令移除。
- `verification.prepare_commands` 从已提交的项目配置编入合同，人工批准后由控制器执行，
  Worker 不再负责临时安装依赖。同一批准摘要已有执行记录时不会自动再跑。
- `status`、`explain` 和主路径共用安全动作提示。活动 Worker 的正常改动不会单凭 dirty
  状态被误报为人工处理；执行 lease 损坏时，不会把 Workspace 变化当作正常写入忽略。
- Reviewer 可提供可选 `change_impacts`，说明功能影响和关键位置。报告只展示通过 Candidate
  路径、行号校验的定位；这些仍是审查意见，不是实际变更清单或验证结论。
- 包版本、安装示例和当前文档统一到 v0.6.0；拆阶段示例使用 `change --json`，明确停在批准前。

## 升级前先看

### 未完成的旧 Run

新建任务和可信 Task Card 恢复使用 `execution_protocol=2`。旧记录缺少该字段时按协议 1
读取，只保留查看、停止和可信交接能力，不能用新版 `change` 原地继续。

升级前优先用原版本完成任务，或先停止旧执行并生成可信交接材料。升级后从通过核验的
Task Card 建立新 Run；旧 Run 和失败现场保留。交接材料不足时先人工处理，不能补写协议号、
改历史状态或启动第二个 Writer 来绕过检查。具体操作见 [使用说明](USAGE-WALKTHROUGH.md)。

### 脚本与宿主 Skill

| 原调用或字段 | v0.6.0 的处理 |
|---|---|
| `vega run --run <run_id>` | 对协议 2 的当前任务使用 `vega change --run <run_id>` |
| `vega retry --run <run_id>` | 先查看 `explain`；完成必要的修订和批准后，由 `change` 检查验证恢复资格 |
| Agent 状态 JSON 的 `next_steps` | 使用 `explanation.safe_actions`；这是 Agent/ChangeRun 状态变化，Core 内部报告仍可能保留旧字段 |
| 分开调查与批准 | 使用 `vega change --run <run_id> --json`；普通 TTY 调用会请求批准，确认后可以继续执行 |

验证恢复必须保留可信的原 Worker、Candidate、Workspace 和门禁证据。资格不符时停止，
不会回退为重新运行 Worker；不能把所有旧 `retry` 命令机械替换成 `change`。

使用仓库级 Codex Skill 的项目，升级后先核对定制内容，再按需运行
`vega adapters init codex --repo . --force`。该操作覆盖已有同名 Skill，不应跳过核对。

### 环境准备

`verification.prepare_commands` 是可选项。命令必须逐字进入人工批准的合同；配置变更后要重新
核对合同，不能让模型临时猜测安装命令。有准备命令的任务不能使用 bounded 自动批准。
准备失败、停止或退出未确认时保留现场，不自动重复安装。准备成功也不能替代验证通过。

## 保留的限制

- Verification、Risk、独立 Reviewer 与 Core Finish 仍决定任务能否完成；高风险修改需要人工确认。
- 写审会话隔离不等于容器沙箱，Reviewer 不读取 Worker 的完整对话和中间推理。
- Provider 超时、未知外部副作用或损坏证据仍可能要求人工处理；本版不是通用自动恢复。
- 用户当前分支保持不动；Vega 不自动 push、merge、release、部署、删除用户文件或接受长期 Memory。
- 历史真实运行中的失败和人工接手保持原结论，不因本版本发布而改记为自动成功。

## 发布核对

发布时将以下结果绑定到最终提交，记录在对应 PR、CI 与 GitHub Release 中：

1. PR CI 和合并后的主线 CI；不引用旧版本的绿勾作为本次结果。
2. 从干净提交构建 wheel/sdist，检查包内内容，在独立环境安装并核对版本、CLI 与内置 Skill。
3. 路径和敏感文件名门禁，以及密钥内容扫描；测试夹具命中与真实秘密分开说明。
4. 发布 Tag、制品摘要及可访问的下载地址。制品上传前不宣称安装链接已经验证。

本次发布材料整理不新增真实 Provider 实验；本地检查、CI 和历史真实任务分别记录。
`RELEASE-CHECKLIST.md` 仅为 v0.5.0 的历史验收，保持原样。
