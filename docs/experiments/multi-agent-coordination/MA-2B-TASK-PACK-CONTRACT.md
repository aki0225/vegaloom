# MA-2B task-pack 与 ground-truth 合同

> 日期：2026-07-24
> 分支：`experiment/ma2b-planner-worker-pilot`
> 实现提交：`e8243896afd4fc35fe1b950779721d95c4109833`
> 状态：`fixture_contract_implemented / pilot_cases_not_frozen`
> 真实 Provider：仍禁止
>
> 2026-07-26 状态更新：候选隔离根已按
> `MA-2B-PILOT-INPUT-QUALIFICATION-V1.md` 冻结 `MA2B-C01`～`MA2B-C12`。
> 本文的 `pilot_cases_not_frozen` 与“当前没有真实 Cxx”仅描述本合同原始 Slice 时点；
> 正式默认根、pricing、execution binding、authorization 和 Provider 执行仍未授权。

## 一、这次冻结的范围

本次新增的是 `MA-2B` 输入冻结层的严格数据合同，以及两个只供 fake driver 使用的静态
fixture。它不代表 12 个真实 Pilot case 已经选定，也不授权模型调用。

实现位置：

```text
src/vega/ma2b_task_pack.py
tests/test_ma2b_task_pack.py
eval/experiments/multi-agent-coordination/fixtures/ma2b/
```

案例编号故意区分为：

- `MA2B-Fxx`：`fake_driver_fixture`，只能用于本地 driver、隔离和故障注入测试；
- `MA2B-Cxx`：`pilot_case`，必须绑定 `git_snapshot`、来源仓库与固定 Git commit。

合同会拒绝把 `Fxx` 声明成 `pilot_case`，也会拒绝把 `Cxx` 声明成
`fake_driver_fixture`。因此两个现有 fixture 不能被误计入真实 Pilot 的 12 个 case。

## 二、artifact 结构

每个 fixture 使用与预注册一致的核心结构：

```text
task-pack/<case-id>/
  task.json
  initial-workspace.json
  project-policy.json
  verification-manifest.json
  case-manifest.json

ground-truth/<case-id>.json
```

`task.json` 只允许任务事实、验收事实、非目标、约束和一个可选未决 decision。未知字段会被
拒绝，因此不能混入：

- reference patch；
- Provider prompt；
- Worker / Planner 聊天；
- 运行结果或自评分数。

`case-manifest.json` 用原始文件 SHA-256 绑定四个输入 artifact。Ground truth 再绑定
case-manifest hash 和确定性 task-pack hash，避免循环引用。

## 三、确定性检查

加载一个 case 时必须同时通过：

1. case id、编号对应类别和 package role 一致；
2. artifact 路径为仓库内精确相对路径，不含盘符、`..`、glob 或敏感路径；
3. artifact 为常规文件，路径中不存在 symlink、junction 或 reparse point；
4. JSON 使用 UTF-8、无重复 key、字段严格且大小受限；
5. manifest 中的路径与固定文件名一致，原始字节 hash 一致；
6. initial workspace 文件集合、每个文件大小和 hash 与 tree hash 一致；
7. task、policy、verification、manifest 与 ground truth 的 case identity 一致；
8. task acceptance ids 与 ground truth 精确一致；
9. verification commands 与 ground truth 精确一致；
10. `code_change`、`human_required`、`stale_evidence`、`invalid_verifier` 的
    expected outcome、是否计分和是否允许 workspace change 一致；
11. human-required case 必须且只能带一个未决 decision；
12. ground truth 的 task-pack hash 与当前输入重新计算结果一致。

任一项失败都会抛出稳定 issue code，调用方必须在 Worker 前 fail-closed。

## 四、当前两个 fixture

### `MA2B-F01`

- 类别：`code_change`
- 目标：验证清晰的小范围文本归一化变更
- 允许写入：`src/textops.py`
- 验证：`python -m pytest -q tests/test_textops.py`
- 正确结果：`accepted_change`
- task-pack SHA-256：
  `a12f4bcbf7be3a8fbc39963cd4143efba7f9cfbbe36fda57f988ffa4196daf85`

### `MA2B-F11`

- 类别：`stale_evidence`
- 目标：验证证据漂移后必须在 Worker 前阻断
- 允许写入：`src/feature_flags.py`
- 冻结验证：`python -m pytest -q tests/test_feature_flags.py`
- 正确结果：`safe_block`
- 代码质量计分：否
- task-pack SHA-256：
  `78c1fbc05da6c98ae34dfa8a002233c987d43b87ab6935c33980d0d6cfd35642`

两个 fixture 都位于 `eval/.../fixtures/`，不会被项目 pytest 递归收集。

## 五、当前不能证明什么

本合同仍不能证明：

- `deterministic`、`network_access=prohibited` 等声明已经由 sandbox 强制执行；
- task-pack 对真实任务具有代表性；
- `A`、`B`、`C` 的质量、成本或耗时差异；
- Planner 输出一定忠实覆盖 task acceptance；
- reviewer 已完成 treatment、模型和成本盲化；
- worktree、run root、Provider session 与缓存已经完成三路隔离。

因此，在本合同原始 Slice 时点没有真实 `MA2B-C01`～`MA2B-C12`，也没有 pricing、
Provider 或 execution binding。后续候选输入状态由
`MA-2B-PILOT-INPUT-QUALIFICATION-V1.md` supersede。

## 六、下一步

下一步只进入 triplet fake driver：

```text
加载 MA2B-F01 / MA2B-F11
→ 为 A、B、C 分别复制独立初始树
→ 建立互不重叠的 Git repo 与 run control root
→ 固定 treatment 顺序
→ 注入 fake Planner / Worker / Reviewer
→ 验证 workspace、index、cache 和 artifact 不串线
→ 对 F11 在 Worker 前注入 stale evidence
```

完成 reviewer 盲化和故障注入矩阵前，仍不能创建真实 Provider backend。
