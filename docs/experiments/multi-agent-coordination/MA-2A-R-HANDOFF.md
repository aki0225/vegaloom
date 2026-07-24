# MA-2A-R 运行时事实绑定修复交接

> 日期：2026-07-24<br>
> 分支：`experiment/ma2a-runtime-binding-repair`<br>
> 冻结基线：`c83aa0520339c0e42964b1060c478c0b1d07b428`<br>
> 最终实现证据：`9b4c34336911abcf6bb27af7baa9e131d79791e7`<br>
> Gate 结论：`accept`<br>
> 下一阶段：`MA-2B pre-registration authorized / implementation not authorized`

## 一、当前真实状态

`MA-2A-R` 已关闭前序独立复审发现的事实绑定缺口：

- Context 从当前 Git workspace、受跟踪 `.vega.yaml` 和 run-owned task、policy、input
  artifacts 编译，不再接受调用方自报的完整权威事实；
- Worker prompt 由冻结 Plan 与 Slice 确定性编译并形成哈希引用；
- Plan、Context、readiness、prompt、source artifacts 和 workspace 在 Worker 前后复核；
- scope / verification artifact 必须绑定 Plan、Slice、Context、前后 snapshot、policy、
  命令、shell 与 oracle；
- Attempt validator 会进行跨 artifact 语义校验；
- staged 新文件计入 `max_new_files`；
- reviewer delegation summary 五项全有或全无；
- run control directory 与目标 repo 重叠时 fail-closed；
- Worker 启动前、scope probe 后和 verification probe 后均复核 workspace；
- run policy 不能放宽项目配置预算。

详细裁决见：

```text
eval/experiments/multi-agent-coordination/MA-2A-R-decision.md
```

## 二、远端与本地证据

最终远端 CI：

```text
Actions run：30067501824
Commit：9b4c34336911abcf6bb27af7baa9e131d79791e7
Result：11/11 jobs success
```

运行详情：
`https://github.com/aki0225/vegaloom/actions/runs/30067501824`

远端覆盖 Python 3.11 全量、Python 3.12 全部分片、独立 `delegation-binding`、Windows、
POSIX、wheel 与静态检查，收集数为 `673`。

本地已确认：

```text
delegation contract：49 passed
execution control：15 passed
context boundaries：36 passed
新增四项 hardening：全部通过
```

逐节点执行九个冻结节点和四个新增加固节点：

```text
12 passed
1 timed out
0 assertion failures
```

本地唯一超时是：

```text
tests/test_delegation_runtime_binding_repair.py::
test_live_compiled_context_records_bound_attempt_and_prompt
```

不要把当前机器的本地结果写成全绿。该节点在远端 `delegation-binding` 分片通过；完整远端 CI
是 Gate 的权威回归证据。本地 `.local-validation/` 只保留诊断，不提交。

## 三、仍未实现

当前仍然只有：

```text
单 Slice
+ 注入式 fake Worker
+ 注入式 scope / verification probe
+ 实验 API
```

尚未实现或证明：

- 真实 Planner、Worker、Provider 或模型档位；
- budget Worker 的质量、成本、延迟或人工步骤收益；
- 多 Slice、多 Worker、并发合并或 A2A；
- retry、failure classification、plan revision 或自动 replan；
- provider-native 子 Agent 对照；
- 默认 CLI、Loop、Finish、Goal 或产品成功路径接入；
- LangGraph 的新增编排收益；
- 目标仓库 OS 级原子锁或 probe 权限层只读隔离。

## 四、另一台机器恢复

在一个新的独立目录中恢复实验分支，不切换正在工作的 `main` worktree：

```powershell
git clone https://github.com/aki0225/vegaloom.git <worktree-path>
Set-Location <worktree-path>
git fetch origin
git switch --track origin/experiment/ma2a-runtime-binding-repair
git status --short --branch
git rev-parse HEAD
```

应先确认：

```text
分支：experiment/ma2a-runtime-binding-repair
工作树：clean
HEAD：至少包含 9b4c34336911abcf6bb27af7baa9e131d79791e7
```

若远端分支已有后续纯文档收口提交，`HEAD` 会晚于 `9b4c343`；不得因此回退实现提交。

## 五、下一步只做 MA-2B 预注册

建议从本 Gate 的最终接受提交新建独立分支，例如：

```text
experiment/ma2b-planner-worker-pilot
```

第一提交只允许新增：

```text
eval/experiments/multi-agent-coordination/MA-2B-pre-registration.md
```

预注册至少冻结：

1. `baseline_commit`、研究合同与 MA-2A-R 决策哈希；
2. 12 个 case 的 task-pack 或更小的明确 pilot 样本，以及每个 case 的选择理由；
3. 可复核 acceptance、确定性 verifier、人工 ground truth 与争议处理规则；
4. Provider、模型版本、Planner / Worker / Reviewer 档位和采样参数；
5. `A = 无显式 PlanContract + premium worker`；
6. `B = premium planner + premium worker`；
7. `C = premium planner + budget worker`；
8. 同 task 事实、初始 snapshot、总预算、验证命令和 single reviewer 控制条件；
9. 每个 treatment 的独立 worktree、随机执行顺序、token / 费用 / wall-clock 记录；
10. scope violation、stale evidence、无效 verification、跨角色泄漏等硬停止线；
11. Pilot 只估计可执行性和方差，不直接形成产品合并结论；
12. 在任何真实 Provider 调用前完成 owner 或独立复审。

预注册接受前禁止：

- 读取或使用 API key；
- 启动真实 Planner、Worker 或 Reviewer；
- 为了跑通 Pilot 修改冻结评分规则；
- 同时加入 retry、replan、多 Worker、原生子 Agent 或 A2A；
- 把当前实验 runtime 接入默认产品路径。

## 六、分支与合并边界

- 不修改、合并或 rebase `main`；
- 不修改既有 `MA-2A-R-pre-registration.md` 和冻结红灯历史；
- `eval/` 只追加新记录；
- 通用能力若未来要进入产品，应从当时最新 `main` 新建小分支重新提取，不能整体合并本实验；
- 当前 Draft PR 只用于 CI，Gate 收口后应关闭，不合并；
- 临时远端 CI 分支可删除，Actions run 仍会保留；
- 不提交 `.local-validation/`、`.tmp/`、凭据、本机绝对路径或本地生成物。

## 七、建议恢复后的首轮检查

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
python -m pytest tests/test_delegation_runtime_binding_repair.py --collect-only -q
ruff check src tests scripts/check_repository_hygiene.py
git diff --check
git status --short --branch
```

当前 Windows 环境运行第一个绑定成功节点可能超过 `58s`。若需要重跑行为测试，应按完整 node
id 分片、使用互不相同的 `.tmp/pytest/runs/<name>`，并如实记录 timeout；不得把超时计为
passed。
