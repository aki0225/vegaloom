# Node Project Profile S/M 探针预注册

> 冻结日期：2026-07-28
> Candidate：`MA2B-NODE-PROFILE-V1`
> 类型：`pre-pilot_worker_capability_probe`
> 正式 MA-2B Pilot：否

## 一、实验问题

在相同模型、reasoning、初始代码和行为 verifier 下，比较：

```text
S：一个隔离 Worker 单次完成全部两个 slice
M：两个隔离 Worker 并行，各完成一个互斥 slice，之后确定性集成
```

本次只观察真实产品任务上的通过率、墙钟和 Token。它不调用 Planner 或 Reviewer，也不改变
正式 12-case task-pack、ground truth、哈希或 readiness。

## 二、固定执行条件

- Worker 模型：`gpt-5.6-sol`
- reasoning：`medium`
- Provider 接口：本机 `codex exec`
- 最大新增 Worker 调用：`3`
- S：`1` 次调用
- M：`2` 次调用
- Hooks、Memory、Goals、Multi-Agent、Apps、Plugins 和额外 MCP：禁用
- 会话：ephemeral，S 与 M Worker 不共享会话
- 不失败重试，不追加调用预算
- 不 commit、push、release 或写长期 Memory
- 所有运行 workspace 与原始产物只写入仓库 `.tmp/`

## 三、冻结接口

只新增一个最小字段：

```text
ProjectProfile.profile_issues: list[str]
```

只允许以下两个稳定问题码：

```text
node_lockfile_conflict
node_package_manager_invalid
```

缺少 `test` 或 `lint` script 时只省略对应命令，不新增问题码，不发展为通用诊断框架。

## 四、冻结切片

### Slice A：Node 画像检测

允许写：

```text
src/vega/project_profile.py
```

职责：

- 从 `package.json.scripts` 只识别非空的 `test` 与 `lint` script；
- manager 与 scripts 在 `tracked_only=True` 时读取同一固定 revision；
- 区分 lockfile 冲突与非法 `packageManager`；
- 向 `ProjectProfile` 写入冻结的问题码。

### Slice B：最小合同与上下文呈现

允许写：

```text
src/vega/models.py
src/vega/project_context.py
```

职责：

- 定义 `ProjectProfile.profile_issues`；
- 在项目上下文中呈现具体问题码；
- 不增加新的 issue 模型、ledger、receipt 或通用诊断层。

两个 slice 的写路径互斥。M 模式下 Worker 只获得自己的 slice，但两者都获得本文件冻结的
字段名和问题码。

## 五、固定行为

1. 只有 `build` script 时，不推荐 Node test/lint 命令；
2. 只有 `test` 或只有 `lint` 时，只推荐真实存在的命令；
3. lockfile 冲突与非法声明产生稳定且可区分的问题码；
4. 有效显式 `packageManager` 可以消歧多个陈旧 lockfile；
5. `.vega.yaml` 显式 verification 保持最高优先级；
6. `tracked_only=True` 时 manager 和 scripts 来自同一 revision；
7. 项目上下文能呈现 `profile_issues`；
8. verifier 只检查公开行为，不读取 AST、不计源码文本、不匹配参考补丁。

## 六、调用前门禁

只有同时满足以下条件才允许消耗三次调用：

1. verifier 在冻结初始 workspace 上为红，且失败只对应本任务缺口；
2. 独立临时 reference workspace 能用合理最小实现变绿；
3. reference patch 不提交，也不进入 Worker prompt；
4. 两个 slice 预估工作量不出现明显超过约 `2:1` 的失衡；
5. 预注册文件与哈希已先提交，调用时 checkout 基线不再变化。

任一门禁失败则停止 Provider 调用，并如实记录为输入不合格。

## 七、结果解释

- S/M 均通过：允许比较墙钟与 Token，但单 case 不外推为通用经济性结论；
- 任一路失败：保留原始结果，不重试，不人工修补；
- scope、集成或 verifier 控制面异常：fail-closed，停止剩余未开始调用；
- Provider 不返回可计价费用时，只报告调用数、Token 和墙钟，不补算美元成本。
