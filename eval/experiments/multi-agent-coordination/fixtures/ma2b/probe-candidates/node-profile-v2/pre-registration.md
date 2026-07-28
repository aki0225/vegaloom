# Node Project Profile S/M 探针 V2 预注册

> 冻结日期：2026-07-28
> Candidate：`MA2B-NODE-PROFILE-V2`
> 类型：`pre-pilot_worker_capability_probe`
> 当前授权：只允许离线资格验证，Provider 调用数为 `0`

## 实验问题

V1 的三个真实 Worker 都在 480 秒内未完成，事件显示主要时间消耗在重复读取完整生产文件和
重新探索接口。V2 不放大时限，也不增加 Planner、Reviewer 或重试，只验证以下最小修正是否
让下一次 S/M 对照具备执行资格：

1. 运行目录固定在仓库内短路径 `.tmp/m2n/<run_id>`；
2. execution 的 `run_id` 必须等于物理 run root 名称；
3. 并行 Worker workspace 使用 `w1`、`w2`，slice_id 不进入物理路径；
4. 每个 Worker 只接收自己切片的窄上下文包，S 才接收两个包；
5. 初始 workspace 与外部行为验证器保持 V1 不变。

## 冻结 Treatment

```text
S：一个隔离 Worker 单次处理两个 slice。
M：两个隔离 Worker 并行，各处理一个互斥 slice，之后确定性集成。
```

如果 Owner 后续单独授权真实调用，最多仍为 `3` 次 Worker 调用：S 一次、M 两次；不允许
Planner、Reviewer、失败重试或额外补救调用。模型、客户端版本和执行窗口必须在调用前另行
绑定，不能把本文件视为 Provider 授权。

## 冻结源码与排除项

源码继续来自真实生产提交 `e4bacfc7c24020489db7bb2675aee4bab14c10d4`。准备独立 workspace
时物理排除：

```text
AGENTS.md
CLAUDE.md
eval/
```

保留真实 `src/vega/`、`tests/`、`pyproject.toml` 与项目依赖，不退化成玩具 fixture。

## 上下文边界

- Slice A 只接收 `context/node-profile-detection.md`；
- Slice B 只接收 `context/profile-issue-context.md`；
- S 按 plan 顺序接收两个上下文包；
- prompt 不包含外部评测实现、答案补丁、历史运行结果或另一个未分配切片的上下文包；
- Worker 不执行整仓 `rg`，只允许按上下文包列出的窄路径补充读取；
- 上下文包不改变允许写路径，也不替代最终行为验证。

## 固定写范围

### Slice A

```text
src/vega/project_profile.py
```

### Slice B

```text
src/vega/models.py
src/vega/project_context.py
```

两个切片的写路径互斥，集成顺序固定为 plan 中的顺序。

## 离线资格门禁

真实调用前必须同时满足：

1. V1 外部行为验证器在冻结初始 workspace 上为 `11 failed`；
2. 临时最小答案 workspace 为 `11 passed`，且答案补丁不提交、不进入 prompt；
3. V2 manifest 中 task、plan、ground truth、上下文包和外部验证器引用哈希一致；
4. prompt 隔离测试证明 M 的任一 Worker 不含另一个切片上下文；
5. Windows fake process-tree timeout 后 execution 可被标准恢复检查读取，不出现 run_id 身份冲突；
6. 最坏切片名不再进入 Worker workspace 路径。

任一门禁失败，V2 继续保持 `offline_not_qualified`，不得消耗 Provider 调用。

## 结果解释

V2 离线门禁通过只表示“输入和控制面具备再次测试资格”，不表示 Multi-Worker 有收益，也不表示
真实 Worker 能在 480 秒完成。只有未来获得新授权并完成 S/M 三次调用后，才能比较完成率、
墙钟与 Token。
