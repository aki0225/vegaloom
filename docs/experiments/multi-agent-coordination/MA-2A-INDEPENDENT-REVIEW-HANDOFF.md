# MA-2A 独立复审接力

> 日期：2026-07-24<br>
> 分支：`experiment/ma2-runtime-bridge`<br>
> 被复审提交：`dcef05ffe4cb5649ebcbc81c2f8b8368b94992cf`<br>
> Gate 结论：`inconclusive`<br>
> 下一阶段：`MA-2A-R candidate / MA-2B not authorized`

## 一、当前真实结论

MA-2A 的实现、原 11 个冻结测试和远端 CI 都是全绿，但独立复审发现测试没有绑定真实运行时
事实，因此不能形成 `accept`。

已确认的阻断项：

- Plan 与注入 Context 可以共同携带过期的 HEAD 和 workspace fingerprint，Worker 仍启动；
- Context 没有从实时 task、policy、scope、verification 和 workspace 编译；
- 只有 `status=passed` 的未绑定 probe artifact 会被接受；
- Worker 可以在执行期间改写 plan/readiness，之后由 Attempt 重新哈希并接受；
- staged 新文件可以绕过 `max_new_files`；
- 残缺 delegation summary 会进入 reviewer context；
- Worker prompt 没有冻结引用。

完整论证见：

```text
eval/experiments/multi-agent-coordination/MA-2A-decision.md
```

## 二、复现命令

```powershell
python eval/experiments/multi-agent-coordination/ma2a_independent_review_probe.py
```

期望结果：

```text
result = current_gate_gaps_reproduced
```

该命令只运行本地临时 Git fixture 和注入式 fake Worker，不调用真实 Provider。

## 三、为什么没有直接修复

冻结成功测试使用虚构的 HEAD 与 workspace fingerprint，也没有提供可编译的权威 task、
policy、scope 和 input artifact。

若在现有实现中加入严格 live binding，该冻结成功测试会失败；若改写测试，则改变了当前
Gate 的冻结输入。两种做法都不能让 `MA-2A` 合法变成 `accept`。

因此本轮只追加复审证据并关闭 Gate，没有：

- 修改 `main`；
- rebase 当前实验；
- 修改既有 `MA-2A-pre-registration.md`；
- 调用真实 Planner、Worker 或 Provider；
- 接入 CLI、Loop、Finish、Goal 或产品成功路径；
- 实现多 Worker、A2A、retry 或自动 replan。

## 四、下一步

下一位执行者应先预注册 `MA-2A-R`，再开始代码修复。推荐顺序：

1. 冻结真实 fixture repo、task artifact、input artifact 和 `.vega.yaml`；
2. 新增独立红灯测试文件，不复用虚构 snapshot 的旧成功 fixture；
3. 实现权威 Context compiler；
4. 加入控制面 pre/post hash；
5. 定义 bound scope / verification artifact schema；
6. 修复 staged 新文件预算和全有或全无 reviewer summary；
7. 执行专项、全量回归、静态检查与远端 CI；
8. 形成新的 `accept / reject / inconclusive` 决策。

在 `MA-2A-R accept` 前，不进入 MA-2B，不启动真实模型对照实验。
