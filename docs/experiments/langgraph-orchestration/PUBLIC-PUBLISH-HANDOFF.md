# LangGraph 公开实验分支接力说明

> 更新时间：`2026-07-22`
>
> 目标仓库：`aki0225/vegaloom`
>
> 公开归档分支：`experiment/langgraph-comparison`
>
> 公开验证分支：`fix/langgraph-archive-ci`
>
> 身份脱敏修复分支：`fix/langgraph-public-sanitization-closure`
>
> 历史策略：从公开 `v0.1.0` 重建单一脱敏归档提交
>
> 最终分类：`partial`
>
> 默认产品路径：`linear + single reviewer`

## 0. 2026-07-22 二次身份脱敏闭环

初次公开归档虽然已完成路径、Provider 和提交历史压缩，但二次对象库对照发现：

- `36` 个完整源实验 commit/tag object 身份，共 `102` 处引用；
- `18` 个源实验 commit 缩写，共 `106` 处引用；
- Gate 7 R2 的文档和机器结果中有 `2` 处真实 Windows 主机名。

这些内容不包含 API key、Authorization token、私钥或 Provider 凭证，但违反公开归档
“不保留私有实验中间 commit 身份和本机环境指纹”的边界。

修复分支已将提交身份替换为稳定语义标签，将主机名替换为
`<windows-sandbox-host>`。对 `eval/gate-7/result-*.json` 的修改只涉及身份字段和主机名，
没有改变 status、metric、failure、token、retry、checkpoint 或 `partial` 结论。

新分支将从 `v0.1.0` 重建单一提交，而不是以旧归档提交为父提交。这样新分支的可达历史
不再包含残留身份。既有 `experiment/langgraph-comparison` 和
`fix/langgraph-archive-ci` 本轮都不更新、不 force-push；它们在 owner 做发布治理决定前
仍保留旧归档内容。

机器门禁：

```powershell
python scripts/check_langgraph_public_archive.py --history-base v0.1.0
```

该门禁检查单提交拓扑、未知 Git 身份、真实主机名、本机根路径、私有 remote 标识、
非示例邮箱和提交作者邮箱。完整范围见
[`PUBLIC-SANITIZATION-CLOSURE.md`](PUBLIC-SANITIZATION-CLOSURE.md)。

## 1. 当前结论

LangGraph 实验已经以独立公开分支归档，公开修复分支的全部 `25` 个 GitHub Actions job
也已经通过。当前可以准确表述为：

> Vega 已经实现并验证 LangGraph 编排、checkpoint 恢复、HITL 和隔离 reviewer 等实验
> 能力，但证据只支持把 LangGraph 保留为可选实验控制面，不支持替换默认 Linear Runtime。

公开分支从标签 `v0.1.0`（`5c492d2`）建立共同父历史，没有直接公开源实验仓的中间
commit 历史。长期公开历史压缩为一个脱敏归档提交，提交身份使用 GitHub noreply 邮箱。

归档内容包括：

- 冻结实验 Runtime、测试、预注册合同、评测数据和结论文档；
- LangGraph optional extra；
- 实验分支专用 CI；
- README 分支边界与 `partial` 结论；
- [`PUBLIC-ARCHIVE.md`](PUBLIC-ARCHIVE.md)；
- 公开仓截至归档时已经发布的治理材料、发布说明和品牌资源。

冻结源码、测试、实验文档、评测数据和脚本共核对 `207` 个文件。除生成的
`src/vegaloom.egg-info/`、一处 EOF 空白以及第 5 节列出的环境身份脱敏外，功能与源实验
冻结快照保持一致。

## 2. 公开 CI 收口

公开修复过程没有整体 cherry-pick 源实验历史，而是按已经确认的文件边界逐项移植。历史
重建后不再保留这些临时 commit ID，但以下修复语义全部进入单一脱敏归档提交：

- Runtime 与基础 CI 回归修复；
- 无法解码的进程状态 fail-closed；
- CI pytest 失败摘要；
- 公开 CI fixture 稳定化；
- checkpoint pending-marker 优先校验；
- Goal Handoff 文件绑定加固；
- 分支强制更新后旧 `before SHA` 不可达时，空白检查退化为校验当前提交自身。

实现收口阶段的三轮 CI 证据：

| Actions run | 通过 | 失败 | 结论 |
|---|---:|---:|---|
| `29848257643` | 24 | 1 | 只剩 Python 3.12 `recovery-core` |
| `29885334545` | 25 | 0 | checkpoint pending-marker 根因已关闭 |
| `29886597737` | 25 | 0 | Goal/Handoff 安全加固后仍保持全绿 |

最后一次运行在 Python 3.11、Python 3.12 和 Windows 关键边界上共执行 `25` 个 job：

```text
passed = 25
failed = 0
cancelled = 0
other = 0
```

因此，旧文档中的“剩余 `596` 个本地节点尚未完成最终重放”不再是公开归档阻塞项。它描述
的是推送前单机验证的中断位置；远端 CI 后续已经按 workflow 分片完整重放全部 `847`
个节点。

## 3. 本地验证证据

验证使用仓库内短路径 worktree：

```text
<short-validation-worktree>
```

首次在长路径 worktree 执行 abrupt-exit 测试时，生成路径达到 `261` 个字符并触发 Windows
传统路径限制。该结果不计为业务失败或通过。将同一 worktree 移到仓库内短路径后，同一
节点通过，没有通过修改业务代码掩盖环境问题。

### 3.1 Checkpoint 提交顺序修复

```text
目标失败节点 = 1 passed
test_checkpoint_resume.py = 20 passed
test_crash_windows.py = 31 passed
recovery-core 合计 = 51 passed
```

修复后的校验顺序会在 manifest 缺失前优先检查合法的
`graph/checkpoint-pending.json`，从而把“本次 Graph 提交未完成”与普通 manifest
缺失区分开，并保持恢复 fail-closed、worker 不重放。

### 3.2 Goal/Handoff 安全加固

最终归档只保留下列已经验证的边界保护，没有引入更大的 Goal Runtime 集成分支：

- 逐项绑定 handoff 中重复的 `scope_profile`、`non_goals` 和
  `success_conditions`；
- 对 checkpoint evidence、report、persisted handoff 和 authoritative artifact
  逐级检查原始路径；
- 拒绝 symlink、junction、reparse point、非普通文件和 hardlink；
- policy 扫描不跟随目录 alias，显式 policy 文件仍按 fail-closed 规则校验。

专项回归结果：

| 文件 | 节点数 | 结果 |
|---|---:|---|
| `test_checkpoint_handoff.py` | 19 | passed |
| `test_goal_cross_session.py` | 3 | passed |
| `test_decision_binding.py` | 15 | passed |
| `test_hitl_cli.py` | 1 | passed |
| `test_interrupt_resume.py` | 5 | passed |
| `test_legacy_run_compatibility.py` | 34 | passed |
| **合计** | **77** | **passed** |

组合运行超过 60 秒的结果没有被计为最终证据；对应 decision 和 interrupt 节点随后使用
独立 basetemp、cache 和完整 node id 重新执行，并取得明确的 passed 计数。

### 3.3 静态与冻结合同

```text
pytest --require-langgraph --collect-only -q = 847 collected
python -m compileall src = passed
ruff check src tests = passed
git diff --check = passed
```

当前本机验证解释器为 Python 3.14；Python 3.11 与 3.12 的兼容性结论来自上述公开
GitHub Actions，不用本机结果替代 CI 证据。

## 4. 仍然保持的产品边界

全绿不改变实验结论：

- 默认 engine 仍为 `linear`；
- 默认 reviewer topology 仍为 `single`；
- LangGraph 仍是 optional experimental recovery / HITL control plane；
- Goal / Checkpoint / Handoff 仍为 engine-neutral；
- 最终分类仍为 `partial`；
- 没有调用真实 provider 重新包装实验结论；
- 没有修改或删除 `eval/` 中的状态、指标和负面结论；二次闭环只替换身份字段和主机名。

截至 `2026-07-22`：

```text
公开 main = da1ac290addd0042f8782476cdb5ece4e53f2aa8
公开实验分支 = 单一脱敏归档提交
公开验证分支 = 与公开实验分支相同的单一脱敏归档提交
身份脱敏修复分支 = 从 v0.1.0 重建的单提交候选
```

公开 `main` 不包含验证分支，也没有被 merge、rebase 或 force-push。历史重建只使用精确
`--force-with-lease` 更新两个 LangGraph 分支，没有创建公开冻结标签。

## 5. 公开审计边界

发布前扫描没有发现真实 API key、Authorization token、私钥或带凭证 URL。长期公开 tree
进一步完成以下脱敏：

- 文档中的本机绝对路径改为语义占位符；
- 测试中的可执行文件路径改为明显的 `C:/fixtures/...`；
- Provider/profile、model、域名和端口改为合成标识与保留域名；
- 会暴露跨仓冻结终点或临时公开修复链路的 commit SHA 改为语义标签；
- Gate 7 case SHA-256 按脱敏后的公开内容重新计算，plan SHA-256 保持不变；
- 旧实验分支和验证分支从 `v0.1.0` 重建为单一提交，旧作者显示名和中间 commit 不再属于
  分支可达历史。

历史重写不能保证 GitHub 的旧 commit URL、Actions 日志或缓存立即不可访问；如果未来发现
真实凭证，仍必须轮换凭证，不能把历史重写当作凭证处置手段。

## 6. 异地复核入口

需要复核最终全绿实现时，应检出公开验证分支，而不是旧归档 head：

```powershell
git clone https://github.com/aki0225/vegaloom.git <public-checkout>
Set-Location <public-checkout>
git switch fix/langgraph-archive-ci

python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev,langgraph]" pytest-timeout
.\.venv\Scripts\python -m compileall src
.\.venv\Scripts\ruff check src tests
.\.venv\Scripts\python -m pytest --require-langgraph --collect-only -q
```

pytest 分片必须：

- 使用独立 `.tmp/pytest/runs/<name>`；
- 使用独立 `.tmp/pytest/cache/<name>`；
- 明确看到 `passed / skipped / failed` 计数；
- 不把 timeout、中断或残留进程视为通过。

分片定义以 [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) 为唯一准绳。

## 7. 下一步由 owner 决定

当前工程、脱敏和历史收口已经完成。后续不再是“继续修测试”，而是发布治理决策：

1. 核对两个 LangGraph 分支的最新 Actions 均通过；
2. 核对 `fix/langgraph-public-sanitization-closure` 的 Actions 与单提交历史门禁；
3. 决定是否让现有归档分支采用新脱敏 tree，或保留旧分支并明确标记为 superseded；
4. 决定是否保留 `fix/langgraph-archive-ci` 作为旧内容验证别名；
5. 或创建一个记录 `partial` 结论、关闭但不合并的 decision-record PR；
6. 只有接受公开审计边界后，才考虑创建 annotated 冻结标签；
7. 未经 owner 明确授权，不把实验分支或验证分支合并进 `main`。

## 8. 禁止事项

- 不把该分支合并到 `main`；
- 不把 `partial` 改写成“Vega 已采用 LangGraph”；
- 不提交本地 `runs/`、`.tmp/`、`.local-validation/`、`.venv/` 或凭证；
- 不改写 `eval/` 中的实验状态、指标和失败结论；隐私身份替换必须单独记录并机器复核；
- 不把单机路径限制或 timeout 写成测试通过；
- 不自动创建公开冻结标签；
- 未经 owner 再次明确授权，不继续 force-push 或改写已发布历史。
