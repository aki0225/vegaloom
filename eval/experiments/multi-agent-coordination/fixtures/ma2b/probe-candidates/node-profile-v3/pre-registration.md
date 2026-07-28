# Node Project Profile S/M 探针 V3 预注册

> 冻结日期：2026-07-28
> Candidate：`MA2B-NODE-PROFILE-V3`
> 类型：`pre-pilot_worker_capability_probe`
> 当前授权：S 一次、M 两次，共 `3` 次 Worker Provider 调用

## 实验问题

V2 的窄上下文让 S Worker 更早进入写入阶段，但 Windows owned process tree 在超时后未能当场
确认终止，导致 M 按 fail-closed 规则没有执行。V3 不改变任务难度、上下文、源码、验证器、
模型、推理档位或 480 秒窗口，只回答：

> 在修复 Codex Runner 的 Windows 终止控制后，冻结的 S/M 对照能否完整执行，并产生可比较的
> 完成率、墙钟与 Token 证据？

## 与 V2 相同的实验输入

以下输入与 V2 保持一致：

- `task.md` 与两个 context packet 按字节保持一致；
- 两个 slice、允许写路径与确定性集成顺序保持一致；
- 冻结 source workspace、初始红基线与外部行为验证器保持一致；
- 模型继续使用 `gpt-5.6-sol`，reasoning effort 为 `medium`；
- 每个 Worker timeout 继续为 `480` 秒；
- Planner、Reviewer、Retry 均为 `0`。

V3 只更新 candidate id、Owner 调用授权和下述控制面绑定，不把 V2 的失败改写成新结果。

## 唯一控制面变化

1. Windows 上优先直接执行原生 `codex.exe`，不再经过 `cmd.exe + codex.cmd`；
2. 启动 `codex exec` 时传入 `--ignore-user-config`；
3. 保留 `--ignore-rules`、显式禁用功能、JSONL 输出、短路径与窄上下文；
4. Windows 强制 `taskkill /T /F` 使用固定 30 秒确认窗口；
5. manifest 同时绑定 `probe.py`、`probe_harness.py`、`execution_control.py` 与
   `runner.py` 的 SHA-256。

不得把这些控制面修正解释为 Multi-Worker 收益；它们只用于恢复实验可执行性。

## 冻结 Treatment 与调用预算

```text
S：一个隔离 Worker 单次处理两个 slice，调用 1 次。
M：两个隔离 Worker 并行，各处理一个互斥 slice，调用 2 次。
Planner：0 次。
Reviewer：0 次。
Retry：0 次。
```

Owner 已明确授权本候选最多执行上述 `3` 次 Worker Provider 调用。不得补跑、追加预算或把
未使用调用转给其他 candidate。

## 冻结源码与排除项

source workspace 继续使用 V2 已准备的独立 Git workspace，冻结 tree 为
`61efd1dc116be8101000f464739b817b0eb33f16`。准备时排除：

```text
AGENTS.md
CLAUDE.md
eval/
```

保留真实 `src/vega/`、`tests/`、`pyproject.toml` 与项目依赖，不退化成玩具 fixture。

## 上下文与写入边界

- Slice A 只接收 `context/node-profile-detection.md`，只写
  `src/vega/project_profile.py`；
- Slice B 只接收 `context/profile-issue-context.md`，只写
  `src/vega/models.py` 与 `src/vega/project_context.py`；
- S 按 plan 顺序接收两个上下文包；
- prompt 不包含外部评测实现、答案补丁、历史运行结果或未分配上下文包；
- Worker 不执行整仓搜索，不 commit、push、联网、调用子代理或写长期 Memory。

## 调用前门禁

预注册提交并推送后，真实调用前必须同时满足：

1. 当前提交等于 Driver 绑定的冻结提交，Tracked 工作区 clean；
2. source workspace clean，HEAD 为 `a4ab2dbaf6e8ed6676bdc207bf42a384bf42a2ef`，
   tree 为 `61efd1dc116be8101000f464739b817b0eb33f16`；
3. V1 外部行为验证器在 source workspace 上仍为 `11 failed`；
4. task、plan、ground truth、context packet、harness 与 verifier 哈希一致；
5. M 的两个 prompt 继续保持 context packet 互斥；
6. Driver 解析到原生 `codex.exe`，并实际包含 `--ignore-user-config`；
7. S、M 与控制目录均不存在，不覆盖 V1/V2 或既有现场。

任一门禁失败，不启动 Provider 调用。

## 停止规则

- S 成功、普通失败或已确认终止的 timeout 后，可以继续执行 M，以保留完成能力对照；
- 若出现 `termination_unconfirmed`、无效 plan、scope violation、输入哈希错误、
  source/verifier 控制面错误，则立即阻断 M；
- M 两个 Worker 启动后只等待本轮完成，不重试、不补救；
- Provider 调用结束后只运行冻结 verifier，不以 Worker 自述替代验证。

## 结果裁决

本轮只允许得出以下一种结论：

1. S 与 M 至少形成可验证结果，能力与经济性均可比较；
2. 能力对照可执行，但 M 没有墙钟或 Token 收益；
3. 任务完成能力仍为负面；
4. 控制面再次阻断，结果继续为 `inconclusive`，并停止扩展 Node candidate。

无论结果如何，V3 结束后不继续建设 SDK、Planner、Reviewer 或新的证据框架，而是回到
MA-2B “单 Worker 与双 Worker 是否有真实价值”的原始实验问题。
