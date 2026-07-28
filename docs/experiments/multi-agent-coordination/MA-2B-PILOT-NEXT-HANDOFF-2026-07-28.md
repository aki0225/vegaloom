# MA-2B Pilot Next 认证阻断后交接

> 交接日期：2026-07-28
> 工作分支：`experiment/ma2b-pilot-next`
> 代码与实验结果基线：`de0265589fdde20a5c5c70f693fe6d91e208cacd`
> 当前阶段：
> `provider_auth_preflight_missing / multi_worker_capability_not_tested / formal_ma2b_pilot_readiness_blocked`

本文是当前跨电脑继续工作的权威交接，取代
`MA-2B-PILOT-NEXT-HANDOFF-2026-07-27.md` 对“下一步”的描述。旧交接继续作为历史记录，
不得改写或删除。

## 一、这次完成了什么

本轮在同一实验分支上完成并推送了三个阶段：

1. `e0f93a7`：提高 Codex Runner 的 Windows 终止可靠性；
   - 默认传入 `--ignore-user-config`；
   - Windows 优先直接执行原生 `codex.exe`；
   - 强制 `taskkill /T /F` 的固定确认窗口扩展到 30 秒；
   - 真实受控 timeout 验证得到
     `timed_out / termination_unconfirmed=false`。
2. `47c68f8`：冻结 Node Project Profile V3 对照；
   - task、两个 context packet、source workspace 和 verifier 与 V2 保持一致；
   - 只改变 Runner 控制面；
   - 冻结预算为 S 一次、M 两次，Planner、Reviewer、Retry 均为零。
3. `de02655`：记录 V3 真实运行结果；
   - 三次调用均启动；
   - 三次均在模型执行前被 Provider 认证拒绝；
   - 结果如实记录为 `inconclusive_provider_auth_blocked`。

本轮没有新增 SDK、Planner、Reviewer、产品化 Multi-Worker、长期 Memory 或新的通用证据层。

## 二、V3 的真实结果

实际执行：

```text
S：1 次
M / Node 检测：1 次
M / 合同与上下文：1 次
```

三个 Codex 进程都表现为：

1. 成功启动原生 `codex.exe`；
2. 成功创建 Provider thread；
3. WebSocket 请求超时并回退 HTTPS；
4. Provider 返回 `401 invalid_api_key`；
5. 进程以退出码 `1` 正常结束。

因此：

```text
Provider 调用：3/3 已消耗
turn.completed：0
Token usage：不可用
代码改动：无
scope 检查：未进入
最终 verifier：未运行
termination_unconfirmed：false
```

S 与 M 的 131.594 秒、137.079 秒只是认证重连耗时，不能用来评价单 Worker 与双 Worker
性能。

详细证据：

```text
eval/experiments/multi-agent-coordination/
  MA-2B-NODE-PROFILE-V3-PROBE-RESULT-2026-07-28.md

eval/experiments/multi-agent-coordination/results/
  MA2B-NODE-PROFILE-V3-2026-07-28.json
```

## 三、根因边界

本机 `codex exec --help` 明确说明：

```text
--ignore-user-config 不加载 CODEX_HOME/config.toml；
认证仍然使用 CODEX_HOME。
```

V3 证明了“忽略用户配置”不等于“隔离认证”。运行后仅检查变量是否存在，没有读取或提交值：

```text
OPENAI_API_KEY：不存在
CODEX_API_KEY：不存在
OPENAI_BASE_URL：不存在
CODEX_HOME：存在
```

结合三次一致的认证错误，当前最可信解释是：Codex CLI 仍使用 `CODEX_HOME` 中的认证状态，
而该状态在本轮不可用或已失效。

另一个已暴露问题是错误分类：

- 当前 Driver 把 Provider 认证失败归入普通 `worker_error`；
- 冻结规则允许普通失败后继续 M；
- 因而 S 已证明认证不可用后，M 仍消耗了剩余两次调用。

后续必须把认证错误视为 Provider 控制面错误，在 S 后直接阻断 M。

## 四、远端没有包含什么

以下内容只存在于本次电脑的忽略目录，不会从 Git 远端恢复：

```text
$repoRoot/.tmp/node_profile_probe_v3_driver.py
$repoRoot/.tmp/m2n/npv3ctl/
$repoRoot/.tmp/m2n/npv3s/
$repoRoot/.tmp/m2n/npv3m/
```

这是有意设计，不是遗漏。原始文件包含本机路径、PID、Provider thread id 和脱敏认证错误，
不应进入公开仓库。远端已保留足够的脱敏摘要、哈希、冻结输入和结论。

晚上从远端继续时，不需要恢复这些 `.tmp` 文件，也不要尝试在 V3 上补跑。

## 五、当前验证状态

本轮已通过：

- V3 预注册定向验证：`14 passed`；
- V3 结果落盘后的定向验证：`7 passed`；
- `python -m compileall src scripts/check_repository_hygiene.py`；
- `ruff check src tests scripts/check_repository_hygiene.py`；
- `python scripts/check_repository_hygiene.py --base-ref origin/main`；
- `git diff --check`；
- source workspace 在运行后仍保持冻结 commit/tree 和 clean 状态。

全量 `python -m pytest` 在 360 秒内没有结束。诊断时本轮启动的 pytest 正等待一个
`git rev-parse --verify` 子进程，随后只停止了本轮自己的 pytest 进程。该结果不能记为全量
通过，也没有证据表明它与 V3 新增文档或测试存在断言失败。

## 六、晚上拉取后的启动命令

已有本地分支：

```powershell
git fetch origin --prune
git switch experiment/ma2b-pilot-next
git pull --ff-only origin experiment/ma2b-pilot-next
git status --short --branch
git log -8 --oneline --decorate
```

本地没有该分支：

```powershell
git fetch origin --prune
git switch --track -c experiment/ma2b-pilot-next origin/experiment/ma2b-pilot-next
git status --short --branch
git log -8 --oneline --decorate
```

确认远端一致：

```powershell
$local = git rev-parse HEAD
$remote = (git ls-remote --heads origin experiment/ma2b-pilot-next).Split()[0]
"LOCAL=$local"
"REMOTE=$remote"
```

开始前按顺序阅读：

1. 本文；
2. `MA-2B-NODE-PROFILE-V3-PROBE-RESULT-2026-07-28.md`；
3. `results/MA2B-NODE-PROFILE-V3-2026-07-28.json`；
4. `fixtures/ma2b/probe-candidates/node-profile-v3/pre-registration.md`；
5. `RESEARCH-AND-EXPERIMENT-PLAN.md` 第 12 节。

## 七、晚上首先要做的事

先不要修改 MA-2B 实现，也不要立即创建 V4。第一步只确认认证边界：

```powershell
codex --version
codex login status
codex exec --help
```

要求：

- 不打印、复制或提交任何 token、API key 或认证文件；
- 不把真实 `CODEX_HOME` 路径写进仓库；
- 不把“login status 有输出”直接当作 Provider 请求一定可用；
- 若要执行最小真实认证 canary，必须先明确它是否计入新的 Provider 预算。

需要先做出一个取舍：

### 方案 A：到此结束 Node candidate

接受 C07/C05 已得到的结论：

```text
Multi-Worker 机械能力成立；
经济收益不稳定；
当前不值得产品化。
```

Node V1-V3 作为“真实实验会被路径、终止和认证控制面阻断”的负面案例保留。该方案最符合
KISS，也足够用于面试讲清楚 harness、fail-closed 和实验边界。

### 方案 B：只允许最后一次 Node V4

如果仍需要一个中等真实任务作为最终证据，则 V4 必须先冻结：

1. Codex auth source 的使用边界；
2. 一个独立、明确计数的认证健康门；
3. `invalid_api_key`、认证缺失、endpoint 错误的稳定分类；
4. S 出现认证控制面错误后阻断 M；
5. 仍然只使用 S 一次、M 两次，不加 Planner、Reviewer 或 Retry。

建议把 V4 定义为 Node candidate 的最后一次尝试。若认证门不通过，则不消耗 S/M；若认证门
通过但 Worker 仍不能形成 verifier 结果，则停止该候选，不再继续 V5。

## 八、不可做的事情

- 不修改或润色 V1、V2、V3 已提交的实验结果；
- 不复用 V3 已耗尽的三次调用预算；
- 不把认证错误伪装成 Worker 能力失败；
- 不因为本轮失败转向建设 SDK、Web UI 或更复杂的多 Agent 平台；
- 不新增 Reviewer、Planner、重试或更多 Worker 拓扑；
- 不合并到 `main`，也不新开 MA 实验分支；
- 不读取、复制或提交真实认证文件；
- 不把本机绝对路径写入公开文档；
- 不把全量 pytest timeout 描述为全量通过。

## 九、原始实验目标

当前分支唯一需要回答的问题仍然是：

```text
S：一个 Worker 完成两个 slice
M：两个隔离 Worker 各完成一个互斥 slice
```

在相同任务、上下文、初始 workspace、模型和 verifier 下，比较：

- 是否完成；
- verifier 是否通过；
- 墙钟；
- Token；
- scope violation；
- 集成冲突；
- 人工负担。

V3 没有回答这个问题，因为模型没有执行。晚上继续时应先决定是否值得用最后一次 V4 获得
答案，而不是继续扩建与原问题无关的 harness。
