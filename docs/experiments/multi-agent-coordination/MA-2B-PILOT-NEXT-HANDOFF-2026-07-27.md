# MA-2B Pilot Next 跨电脑交接

> 交接日期：2026-07-27
> 建议继续日期：2026-07-28
> 工作分支：`experiment/ma2b-pilot-next`
> 交接前代码提交：`d59565e80d0ea2603bf0e5e00cefc83928d92889`
> 主线基线：`origin/main@7805bba18f7d91594cba0c6cb95251493503362c`
> 当前阶段：
> `pre_pilot_worker_capability_signal_positive / multi_worker_economic_signal_not_observed / formal_ma2b_pilot_readiness_blocked`

本文是当前跨电脑继续工作的权威交接。2026-07-26 的
`MA-2B-PILOT-NEXT-HANDOFF-2026-07-26.md` 只保留历史状态，不再控制下一步。

## 一、本轮完成了什么

远端原有提交 `a18945f` 之后，本地按顺序完成了以下五个提交：

1. `887f370`：回退 ATG 三拓扑试跑交接，停止把外部仓库、Reviewer 和多种拓扑混入当前
   Worker 能力验证。
2. `8a35eea`：删除重复的 task-pack 与 ground truth 权威来源，正式输入只保留一个读取根。
3. `3224cdd`：删除未证明实际能力的 delegation 路由与证据栈，以及对应 635 个测试节点。
4. `94f28e8`：加入最小 MA-2B 顺序/并行能力探针，只实现冻结计划、隔离 workspace、互斥写
   路径、确定性集成、统一 verifier 和基本失败语义。
5. `d59565e`：修正 S/M treatment 定义，并固化 C07、C08、C05 的能力与经济性结论。

正式 12-case task-pack、ground truth、哈希和 readiness 成功条件没有被改写。当前也没有
Planner、Reviewer、MA-3 或多 Worker 产品化实现。

## 二、已经证明到什么程度

探针使用相同 Worker 模型、reasoning、初始 workspace、冻结任务和 verifier：

```text
S：单 Worker、单调用完成全部 slice
M：两个隔离 Worker 各完成一个互斥 slice，集成后统一验证
```

### C07：低耦合双文件

- S/M 均通过 verifier。
- M 墙钟比 S 减少 `27.95%`。
- M 总 Token 比 S 增加 `24.31%`。
- 结论：机械并行能力成立，但使用更多 Token 换取延迟下降。

### C08：无效 verifier

- S 通过依赖测试导向冗余；M 的最小语义实现被 AST/文本条件拒绝。
- 分类为 `verifier_task_contract_mismatch / no_valid_s_m_pair`。
- 该结果不能算作 Multi-Worker 失败，也不能进入经济性比较。
- 正式 C08 输入和哈希继续保留，但其 pre-pilot 正向样本资格已被 supersede。

### C05：不均衡接口协调

- S/M 均为 `3 passed`，无 scope violation、集成冲突或人工代码修补。
- M 墙钟只减少 `0.13%`。
- M 总 Token 增加 `83.73%`。
- pricing Worker 用时约 206 秒，binding Worker 用时约 75 秒；整体时间被大 slice 支配。

当前结论固定为：

```text
pre_pilot_worker_capability_signal_positive
multi_worker_economic_signal_not_observed
formal_ma2b_pilot_readiness_blocked
```

这证明两个隔离 Worker 能在低耦合双文件和冻结接口依赖任务中完成可验证集成。它没有证明
默认并行更便宜，也没有证明正式 MA-2B Pilot 已具备执行资格。

## 三、当前代码与验证状态

当前 MA-2B 探针实现集中在：

```text
src/vega/experimental/ma2b/probe.py
tests/test_ma2b_probe.py
```

本轮已记录的验证结果：

- 五个 MA-2B 定向测试文件：`65 passed, 1 skipped`。
- `tests/test_architecture_growth.py` 与 `tests/test_context_boundaries.py`：
  `75 passed, 3 skipped`。
- `python -m compileall src scripts/check_repository_hygiene.py`：通过。
- `ruff check src tests scripts/check_repository_hygiene.py`：通过。
- `python scripts/check_repository_hygiene.py --base-ref origin/main`：通过。
- `git diff --check`：通过。
- 单次全量 `python -m pytest` 在 60 秒预算内未完成，不能记为全量通过。

推送后的远端 CI 才是跨平台结果，不能用以上本地记录替代。

## 四、下一候选：Vega 自举 Node 任务

下一步不直接进入半小时或一小时任务。优先冻结一个预计单 Worker 用时 `15-25` 分钟的真实
Vega 产品缺口：

```text
Node 项目画像只推荐 package.json 中真实存在的 test/lint script，
并为 lockfile 冲突和非法 packageManager 输出结构化诊断。
```

当前真实缺口：

- `project_profile.py` 只要发现 `package.json` 和可选 package manager，就会推荐
  `npm test`、`npm run lint` 等命令，没有检查对应 script 是否存在。
- lockfile 冲突与非法 `packageManager` 都被压缩为未选择 manager，项目上下文无法说明停止
  原因。
- `M002-NODE-PACKAGE-MANAGER-HANDOFF.md` 已明确把这两项列为未完成边界，因此任务不是为了
  实验临时制造。

建议冻结两个生产代码 slice：

```text
Slice A:
  src/vega/project_profile.py
  按同一 tracked revision 读取 package.json.scripts
  只生成真实存在的 test/lint 命令
  产生冻结的 Node profile issue code

Slice B:
  src/vega/models.py
  src/vega/project_context.py
  定义最小结构化诊断合同
  在项目上下文中呈现具体停止原因
```

两个 slice 的写路径互斥，但在生成 task-pack 前必须先冻结最小接口。S/M 必须使用同一个接口、
任务文本、初始 workspace 和 verifier。

固定 verifier 必须位于 Worker 允许写范围之外，只验证行为：

1. 只有 `build` script 时，不推荐 test 或 lint 命令。
2. 只有 `test` 或只有 `lint` 时，只推荐实际存在的命令。
3. lockfile 冲突与非法声明产生稳定且可区分的问题码。
4. `.vega.yaml` 显式 verification 继续保持最高优先级。
5. `tracked_only=True` 时，package manager 与 scripts 都读取同一固定 revision。
6. verifier 不使用 AST、源码文本计数或参考补丁匹配。

这个任务属于 Vega 用自身真实产品问题验证自身 Worker 能力。它适合作为内部实战样本，但单独
只能证明 Vega 仓库内这一类任务，不能外推到所有外部仓库。

## 五、明确排除的候选

- Click EOF 与 dogfood case 选择任务主要是“一个 Worker 写实现、另一个 Worker 写测试”，
  与固定 verifier 的实验边界冲突，不作为首选。
- `775e1b9` 的 Finish evidence snapshot 工作明显集中在一个大 slice，并重新进入证据验证层。
- `497513c` 涉及 Finish、Goal、Loop 成功语义和 fail-closed 证据边界，风险高于本轮中等任务。
- 不继续 C05、C07 或 C08 的 Provider 调用；这些 case 的当前结论已经足够。

## 六、下一次工作的严格顺序

下一次只做离线输入资格准备：

1. 从交接代码基线冻结 Node 候选的最小 workspace。
2. 冻结两个 slice 的接口、允许写路径和禁止写路径。
3. 编写独立行为 verifier，并确认 Worker 无权修改 verifier。
4. 在初始 workspace 上确认 verifier 为红。
5. 编写行为 ground truth，不包含 reference patch。
6. 生成 task-pack、workspace 文件哈希和 ground truth 哈希。
7. 运行现有 MA 定向测试，确认没有修改正式 12-case readiness。
8. 汇报离线资格结果，等待 Owner 决定是否授权一次完整的 `S 1 次 + M 2 次` Provider 对照。

本阶段不要直接修复 `project_profile.py`、`models.py` 或 `project_context.py`。这些文件只应在
后续隔离的 S/M candidate workspace 中由 Worker 修改，当前实验分支只准备固定输入与 verifier。

## 七、停止条件

出现以下任一情况立即停止：

- 需要修改通用 Runtime、Reviewer、CLI、CI 或正式 readiness；
- 需要增加 receipt、ledger、manifest、handoff packet 或新的证据层；
- 需要改变正式 12-case、ground truth 或既有哈希；
- verifier 需要 AST、源码文本计数或参考补丁匹配；
- 两个 slice 无法保持互斥写路径，或工作量明显超过 `2:1`；
- 单 Worker 预估超过 25 分钟；
- 需要调用真实 Provider，但尚未获得新的明确调用次数授权；
- 需要开始 Planner、Reviewer、MA-3 或多 Worker 产品化。

## 八、另一台电脑的启动命令

已有本地分支时：

```powershell
git fetch origin
git switch experiment/ma2b-pilot-next
git pull --ff-only origin experiment/ma2b-pilot-next
git status --short --branch
git log -8 --oneline --decorate
```

本地还没有该分支时：

```powershell
git fetch origin
git switch --track -c experiment/ma2b-pilot-next origin/experiment/ma2b-pilot-next
git status --short --branch
git log -8 --oneline --decorate
```

开始修改前依次阅读：

1. 本文；
2. `RESEARCH-AND-EXPERIMENT-PLAN.md` 第 12.1-12.5 节；
3. `MA-2B-PILOT-INPUT-QUALIFICATION-V1.md`；
4. `M002-NODE-PACKAGE-MANAGER-HANDOFF.md` 第七节。

不要切到 `main` 处理其他 AI 的主线任务，也不要再创建新的 MA 分支。
