# MA-2B Pilot Next 阶段交接

> 交接日期：2026-07-26
> 下次继续日期：2026-07-27
> 工作分支：`experiment/ma2b-pilot-next`
> 主线基线：`origin/main@7805bba18f7d91594cba0c6cb95251493503362c`
> 当前阶段：`candidate_inputs_complete / readiness_blocked / provider_not_authorized`

## 一、今天完成了什么

本分支已经从修复后的主线基线重新建立，并按顺序完成 5 个能力迁移提交：

1. `13b62c2`：迁移 MA-2B 最小合同、task-pack/readiness/pricing/execution-binding
   模块、fake fixture、预注册与定向测试。
2. `68c3806`：冻结 `C01/C05/C09-C12` 的候选 workspace。
3. `9f749b9`：冻结首批四个 task-pack、ground truth 和输入资格协议。
4. `e569d3e`：冻结 `C02-C04/C06-C08` 的候选 workspace。
5. `6bc443e`：补齐其余 task-pack、ground truth、12-case 哈希和定向测试。

当前已冻结 `MA2B-C01`～`MA2B-C12`：

- `C01-C04`：小修复；
- `C05-C08`：跨文件；
- `C09-C10`：人工决策；
- `C11`：stale evidence；
- `C12`：invalid verifier。

所有 case 都已生成 task-pack、ground truth、固定 verifier、workspace 文件哈希和级联
case-manifest 哈希。真实 loader 可加载全部 12 个候选，固定 case-set 哈希为：

```text
33b2caa335b417b47ee45bb5de7051aef20682bbf938eddf5d2e4ad5d3d4f137
```

8 个代码 case 的 verifier 已分别在历史父提交和参考修复提交上验证为红/绿，可证明 verifier
能够区分对应历史缺陷，但不等同于 Worker 或 Provider 已解决这些 case。

Canary 结论保持不变：

```text
capability_signal_positive
economic_signal_not_observed
```

`worker_token_limit` 已统一解释为观测预算，不再声明为 Runtime 硬门禁。协议 supersession、
经济性边界和未来迁移顺序已写入
`MA-2B-PILOT-INPUT-QUALIFICATION-V1.md`。

## 二、当前真实状态

使用候选根目录运行 readiness 时，12 个 case 全部加载成功，但状态仍为：

```text
status = blocked
issue_codes = execution_binding_path_invalid, execution_authorization_path_invalid
```

这意味着 case 缺失问题已经解除，但正式 Pilot 还没有执行资格。候选输入仍位于隔离目录，
没有迁入 readiness 默认根，也没有调用真实 Provider。

本阶段没有修改通用 Runtime、Reviewer、CI、MA-3 或 multi-worker，没有新增证据层，也没有
放宽正式 12-case readiness gate。

## 三、验证记录

- 项目测试收集数：`820`。
- 单次 `python -m pytest` 受 60 秒上限影响超时，不能记为一次完整通过。
- 按测试文件和精确节点串行分片覆盖全部 820 个节点：`814 passed, 6 skipped`。
- MA-2B 定向测试：`24 passed, 1 skipped`。
- `python -m compileall src scripts/check_repository_hygiene.py`：通过。
- `ruff check src tests scripts/check_repository_hygiene.py`：通过。
- `python scripts/check_repository_hygiene.py --base-ref origin/main`：通过。
- `git diff --check`：通过。

本交接文档完成后已重新执行上述静态检查、测试收集和 MA-2B 定向测试。远端结果仍以本次
推送后的 CI 为准。

## 四、还没有完成什么

1. 候选 task-pack 与 ground truth 尚未迁入正式 readiness 默认根。
2. 尚未生成真实 pricing manifest。
3. 尚未生成正式 execution binding 和 owner authorization。
4. 尚未执行 `C11` 的 task artifact 漂移注入。
5. 尚未执行 `C12` 的 invalid verifier driver 阻断验证。
6. 尚未调用 Planner、Worker 或任何真实 Provider。
7. 尚未开始正式 `12 case × 3 treatment` Pilot，也没有 Reviewer、MA-3 或 multi-worker 结果。

CI 有一个已知范围外风险：当前 `.github/workflows/ci.yml` 固定检查 `712` 个测试节点，而本分支
实际收集 `820` 个节点。该工作流文件属于本阶段禁止修改范围；若远端 CI 仅因此失败，应记录为
基线合同漂移，不得通过改测试数量或放宽 readiness 绕过。

## 五、2026-07-27 从哪里继续

继续使用现有分支，不创建新的 MA 分支：

```text
worktree: $worktreePath
branch: experiment/ma2b-pilot-next
```

开始时先执行：

```powershell
git fetch origin
git status --short --branch
git log -7 --oneline --decorate
```

如果 `main` 在夜间前进，先比较新提交是否影响 MA 专用模块，不要直接把其他 AI 的主线工作
覆盖到本分支。

明日建议的第一项工作是先确认本分支远端 CI 结果，然后为“正式执行资格”单独确认下一阶段
范围。推荐顺序为：

1. 决定候选输入迁入正式默认根的方式，并保持 12-case 哈希不变。
2. 冻结真实 pricing manifest。
3. 冻结 execution binding 与 owner authorization。
4. 在不修改通用 Runtime 的前提下，补 `C11/C12` 的 MA 专用 driver 故障注入测试。
5. readiness 全绿并获得明确 Provider 授权后，才开始正式 Pilot。

如果任一步需要修改通用 Runtime、CI、增加证据层、放宽 readiness，或提前进入 Reviewer、
MA-3、multi-worker，应停止并重新确认范围。

## 六、2026-07-27 P0 复核记录

本节是对 2026-07-27 候选差异的追加记录，不替换第三节的 2026-07-26 历史验证结果。

本次 P0 只处理实验代码边界和架构增长问题：

1. 将根命名空间中的 MA-2B 专用模块迁入
   `src/vega/experimental/ma2b/`，不保留旧根模块兼容层。
2. 将 delegation schema、路径与命令校验、task-pack schema、readiness authorization
   校验分离到同一实验包内的小模块；将 delegation、task-pack 和 readiness 的超长流程拆成
   有界函数。
3. 更新 MA-2B 测试 import 和 live-code 文档路径；冻结 workspace 中的历史 verifier 命令保持
   不变。
4. 没有修改 `eval/`、通用 Runtime、Reviewer、CI、readiness 成功条件或 Provider 边界；
   没有生成 pricing、execution binding、owner authorization，也没有调用真实 Provider。

当前候选差异的验证结果：

- MA-2B 五个定向测试文件：`109 passed`。
- MA-2B 实验包定向覆盖率：总计 `89%`，各模块均不低于 `80%`。
- `python -m compileall src scripts/check_repository_hygiene.py scripts/check_architecture_growth.py`：
  通过。
- `ruff check src tests scripts/check_repository_hygiene.py scripts/check_architecture_growth.py`：
  通过。
- 架构增长门禁：通过，结果为 `C901 46->46，Python 模块 55->65`；新增 MA-2B 模块均不超过
  500 行。
- 仓库路径与私密文件卫生检查：通过。
- 当前工作区差异与 `origin/main` 候选差异的 `git diff --check`：通过。
- 当前候选差异测试收集：`821` 个节点，CI 固定 `712` 节点的合同漂移未在本阶段处理。
- wheel 边界：`vega.experimental.ma2b.*` 已打包且可从 wheel 导入；旧
  `vega.delegation` 与 `vega.ma2b_*` 根模块未进入 wheel。

独立只读 reviewer 发现并修复了一处拆分漂移：execution binding 已成功加载、但随后文件哈希
读取失败时，拆分后的 helper 曾错误丢弃已加载 binding，造成 `execution_binding_loaded` 和
后续 pricing 诊断偏离基线。当前实现会保留已加载 binding、仅将哈希标记为不可验证，并已有
回归测试固定该 fail-closed 语义。

本次没有把全量测试记为通过。拆分初稿按测试文件设置单进程 60 秒上限后，22 个完整文件
覆盖 479 个节点并全部通过；另外 10 个文件在 60 秒内未完成，未产生断言失败。独立 reviewer
修复拆分漂移后，受影响的 MA-2B 定向集已按当前候选差异重跑为 `109 passed`，但没有把其余
不受影响的分片结果拼接成一次新的全量通过。

代表性 faulthandler 诊断显示长节点正在重复执行 workspace、risk gate 和 evidence 快照所需
的 Git 只读采集，而不是进入 MA-2B 模块调用链。对同一测试临时仓库逐条复放 `rev-parse`、
`status`、`diff` 和 `ls-files` 时，单条命令均能在一秒内返回；再次运行相同节点时，60 秒
超时位置已推进到测试函数后半段，因此没有证据证明某条 Git 命令稳定卡死，也没有依据通过
修改全局 pytest Git 配置解决问题。

该结果只能记为“定向验证通过、全量验证在当前 60 秒预算下未完成”。后续应把这些长节点
作为独立测试性能问题处理，或在资源稳定时按精确节点重跑；不能沿用 2026-07-26 的全量结果
冒充当前候选差异已完整通过。
