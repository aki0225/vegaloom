# AGENTS.md

给在本仓库工作的 AI 编码代理(及新加入的人)的项目约定。Vega 自己也消费目标仓库的 AGENTS.md 来编译执行上下文——这份文件同时是本仓库的示范样例。

## 项目一句话

Vega 是本地优先的 AI 编码工作流 harness：worker 改代码，reviewer 使用独立只读会话，
不继承 worker 的完整聊天记录，并结合 diff、测试证据和项目规则审查；任务能否结束由项目
自己的验证命令裁决，证据不足时 fail-closed 交还人工。

## 目录地图

- `src/vega/` — 核心实现(Python,包名 `vega`)
- `tests/` — pytest 测试
- `eval/` — 实验与运行证据(`cases.jsonl` 评测用例、`real-world-runs.md` 真实 Issue 运行记录)
- `scripts/` — 评测脚本(`dogfood_eval.py`)
- `examples/` — 任务样例
- `examples/evidence/` — 可公开复核的脱敏运行证据
- `docs/` — 产品契约、架构、使用闭环等文档
- `loops/` — 版本化的 LoopSpec/YAML 配置
- `runs/` — 本地正式运行产物(state/trace/报告)，默认不提交

## 验证命令(改完代码必须跑)

```powershell
python -m compileall src
python -m pytest
ruff check src tests
git diff --check
```

CI 在 Python 3.11 与 3.12 上跑同样集合。注意两个已知环境差异:测试断言 CLI 输出时须防 CI 注入的 ANSI 渲染(conftest 已有 autouse fixture 清理环境变量),POSIX 进程组探测与 Windows 路径不同——本地绿不等于 CI 绿。

## 代码约定

- 注释与文档使用简体中文;注释只写代码本身表达不了的约束
- 修改功能时删除旧实现,不保留兼容性死代码
- 测试超时上限 60s,避免挂死

## 红线(违反即错,无需讨论)

1. **`eval/` 是证据记录,只许追加不许改写**——`real-world-runs.md` 是预注册实验的产物文档,历史记录(包括 fail-closed 的失败记录)不得删除、润色或重新表述;新增运行只能以追加条目的方式进入,且如实记录结果。
2. **fail-closed 语义只许收紧,不许放松**——"验证失败时 reviewer 的 approve 不能让任务成功""证据缺失、过期或不一致时停止自动执行交还人工"是产品承诺。任何让"证据不足时通过"的改动永远是 bug,不是优化。
3. **写审会话边界不得打通**——reviewer 可以读取明确编译的任务、diff、测试证据、项目规则、
   风险门禁和可选 accepted memory，但不得为了"提升效果"接收 worker 的完整对话、自述或中间
   推理。read-only sandbox 是共享仓库的只读视图，不等于容器或操作系统级隔离。
4. **成功语义不掺水**——不得新增任何绕过确定性验证就把 run 标记为成功的路径;人工裁决关闭的 run 记录为人工裁决,不记录为验证成功。
5. Vega 不自动 commit、push、release、删除文件或写入长期 Memory——这是行为边界,不是待实现功能。
6. **公开内容不得携带本机隐私**——源码、文档、测试 fixture、日志和提交消息不得出现真实本机绝对路径、用户目录、主机名、私人邮箱或环境专属端点；统一使用 `<repo>`、`<public-worktree>`、`C:/fixtures/...`、`example.invalid` 等明显占位符，推送前必须扫描当前 tree 与分支可达历史。
