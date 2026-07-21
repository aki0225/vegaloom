# Assurance 阶段 0-B 接力说明

> 更新时间：2026-07-21
> 当前结论：代码与定向验证基本收口，但完整 `python -m pytest` 尚在后台运行，
> 当前不得宣称阶段完成或直接发布。

## 1. 本阶段目标

阶段 0-B 只修复基础成功语义，不实现数据库迁移、数据修改、并发 detector 或新
artifact schema：

- 零条验证命令不得自动成功。
- `--no-verify` 不得被 reviewer `approve` 提升为成功。
- 非结构化外部测试日志不得冒充结构化验证。
- Loop、Finish、Goal 都必须重新判断最新 iteration 是否存在受信、非空且全部通过的
  结构化验证。
- 前序 iteration 的通过结果不得被后续 `skipped` iteration 继承。

预注册内容位于 `eval/assurance-validation.md` 的
`AV-STAGE0B-001 · preregistration`。

## 2. 当前 Git 状态

- 分支：`main`
- 基线提交：`176ac381a0c006a69e19a83c78c86cf43125b650`
- 基线标签：`v0.1.1`
- 远端：`origin`
- 原始验证工作区仍位于 `main`，用于承载后台全量测试。
- 远端接力分支：`fix/verification-success-semantics`
- 接力分支是待验证 WIP，尚未合并 `main`、创建 PR、打标签或发布。

主要变更：

- `src/vega/loop_runtime.py`
  - reviewer approve 只有在最新验证可信通过时才能成功。
  - 验证缺失、跳过或零命令时进入 `needs_human/verification_unverified`。
  - success eval 会重新校验结构化验证 artifact。
- `src/vega/goal_evidence.py`
  - 新增统一的可信验证通过判定。
  - Goal 的 Loop/Finish evidence 不再只信顶层成功状态。
- `src/vega/finish_runtime.py`
  - Finish 重新计算 `verification_passed`。
  - 缺少可信验证时返回 `needs_human`，不进入 `ready_to_commit`。
- 测试
  - 新增 `tests/test_assurance_verification_semantics.py`。
  - 修正旧成功 fixture，使其真实执行确定性验证，而不是依赖 `verify=False`。

## 3. 已完成验证

以下均为本地真实执行，不是历史缓存：

| 范围 | 结果 | 日志 SHA-256 |
|---|---:|---|
| Assurance 最终定向集 | `8 passed` | `86B5D81FC7A6868AFFC315B5648FFDE16E8D80FD1094ABB016F2F11AE67E5624` |
| Success semantics | `29 passed` | `64FDB92BCA3A5571FE10093C5D0D365C8F5EC4838E024E1D80980CAEE7665690` |
| Evidence freshness | `19 passed` | `2F79B26969B61E63411FC3BB9B024B0B69BB1B8FD5749C150F757BE36C939ED9` |
| Finish artifact integrity | `18 passed` | `A3BAE8E560A15E526C9BBDEC4DC504FAC08A6D188E748A629606C76291A87E96` |
| Recovery chaos | `10 passed` | `C0C52D6BD9997B3C150CDE25DC7458D44754306B1FB95A389DC3C720F10DB041` |
| 最新轮次不得继承旧验证 | `1 passed` | `5E6E0450E9BE769C3ED5B2D781C2620C866BB2AE41E7DE94421025B8E8BB1A29` |
| Smoke Finish CLI 修复后复跑 | `1 passed` | `AC63193C76225441E3BB44127C164160A022632059D7DE3AAD1D8598F5180FB0` |
| P0 scope binding 修复后复跑 | `1 passed` | `E73EA939832512C72151281AC9FF74994338E361E08BA96C69E03B2D26FB83C6` |

最近一次静态检查曾通过：

```powershell
python -m compileall -q src
ruff check src tests
git diff --check
```

在最后两处测试 fixture 修改和本文档新增后，需要再执行一次，不应复用此前结论。

## 4. 正在运行的完整测试

完整测试已改为独立隐藏后台进程，避免对话中断导致 pytest 退出：

- 收集数量：`510`
- PID 记录：`.local-validation/stage0b-full-pytest-rerun.pid.txt`
- 实时日志：`.local-validation/stage0b-full-pytest-rerun.txt`
- 退出码：`.local-validation/stage0b-full-pytest-rerun.exit.txt`
- 日志哈希：`.local-validation/stage0b-full-pytest-rerun.sha256.txt`
- 本文档编写时进度：约 `13%`

查看状态：

```powershell
$pidValue = [int](Get-Content .local-validation\stage0b-full-pytest-rerun.pid.txt)
Get-Process -Id $pidValue -ErrorAction SilentlyContinue
Get-Content .local-validation\stage0b-full-pytest-rerun.txt -Tail 50
```

完成后检查：

```powershell
Get-Content .local-validation\stage0b-full-pytest-rerun.exit.txt
Get-Content .local-validation\stage0b-full-pytest-rerun.sha256.txt
Get-Content .local-validation\stage0b-full-pytest-rerun.txt -Tail 80
```

只有退出码为 `0`，且日志明确显示全部测试通过，才可把全量测试登记为绿色。

## 5. 晚上继续时的执行顺序

### 5.1 等待并判定全量测试

1. 检查后台 PID 和退出码文件。
2. 若仍在运行，只观察，不要启动第二套全量 pytest。
3. 若失败，先从日志定位首个失败：
   - 判断是否为本次成功语义导致的旧 fixture 漂移；
   - 不得为了转绿放松 fail-closed；
   - 不得把环境失败记录成代码通过。

### 5.2 完成最终静态验证

```powershell
python -m compileall src
python -m pytest
ruff check src tests
git diff --check
```

如果后台全量 pytest 已完整通过，不必为了形式再启动第二次全量 pytest；保留其日志、
退出码和 SHA-256 即可。

### 5.3 追加正式实验结果

只有全部结论明确后，向 `eval/assurance-validation.md` 末尾追加：

```text
AV-STAGE0B-001 · result
```

必须记录：

- 基线提交和最终 diff 范围。
- 注册案例逐项结果。
- 全量 pytest 的总数、结果、耗时、日志路径和 SHA-256。
- 静态检查结果。
- 仍未验证的范围：数据库迁移、数据修改、并发、adapter、Node 包管理器和 Finish 性能。

`eval/` 只允许追加，不得修改或润色已有记录。

## 6. 分支、合并与版本建议

### 结论

不要直接把当前脏工作区推送到 `main`。建议：

1. 全量测试完成并处理红灯。
2. 创建独立修复分支。
3. 提交并推送该分支。
4. 通过 PR 合并到 `main`。
5. 合并后再准备 `v0.1.2` 补丁版本。

推荐分支名：

```text
fix/verification-success-semantics
```

原因：

- 本次修改触及 Loop、Finish、Goal 三个成功裁决边界，不是纯文档修订。
- 旧行为会从“错误成功”变为 `needs_human`，需要在 PR 中明确审阅行为变化。
- 当前完整测试尚未结束。
- `main` 当前正位于 `v0.1.1` 基线，独立分支便于形成可复核 diff 和回滚点。

在另一台电脑继续：

```powershell
git fetch origin
git switch --track origin/fix/verification-success-semantics
```

继续修改并完成验证后，再执行：

```powershell
git status -sb
git diff --check
git add <本次继续修改的文件>
git commit -m "测试：完成 Assurance 阶段 0-B 收口"
git push
```

不要提交 `.local-validation/`、`.tmp/` 或 `runs/`。后台全量测试日志只存在于原始电脑；
其最终退出码和 SHA-256 需要在完成后追加到 `eval/assurance-validation.md`。

## 7. `v0.1.2` 候选条件

本次适合作为补丁版本候选，因为它修复了已有 fail-closed 产品承诺的实现偏差，而不是新增
不兼容产品能力。但必须满足：

- 完整 pytest 通过。
- CI 的 Python 3.11 和 3.12 均通过。
- PR 中明确说明：
  - 零验证命令、`--no-verify`、验证 artifact 缺失或损坏不再自动成功；
  - reviewer approve 不能覆盖验证未知；
  - 人工裁决仍不会记录为验证成功。
- 新增 `docs/RELEASE-NOTES-0.1.2.md` 或等价发布说明。
- 合并后再由人工打 `v0.1.2` 标签并发布。

## 8. 剩余风险

- 完整测试仍在运行，当前不能给出全绿结论。
- 尚未在 CI 的 Python 3.11/3.12 上验证。
- 尚未实施数据库迁移、Backfill、并发和重试威胁闭环。
- Loop eval 现在复用共享 artifact integrity 判定，虽然定向回归已通过，仍应关注完整测试中的
  性能和层次依赖问题。
- 本地 Python 为 3.14.3，与 CI 版本不同。
