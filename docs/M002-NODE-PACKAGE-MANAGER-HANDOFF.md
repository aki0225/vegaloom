# M-002 Node 包管理器选择接力

> 日期：2026-07-22
>
> 分支：`fix/node-package-manager-selection`
>
> 当前裁决：`passed-local / requires-ci / do-not-merge`

## 一、先看结论

M-002 已完成本地实现和完整节点覆盖，但**不要合并 `main`，不要发布版本**。当前分支只适合
在另一台机器继续复核、创建 Draft PR 或等待跨平台 CI。

关键提交：

- 基线：`6b74c5be3e26d6e9d7e5849b1ec76dd9c5b5f2b3`
- 红灯预注册：`2fad84cdc16a67eacdc9579dfb229e726c002cf1`
- 实现提交：`c6b5325d025c64270c2bd1fa98fb3b7ae9dc8e2f`

## 二、已完成内容

`src/vega/project_profile.py` 现在只计算一次 Node 包管理器选择结果，并让项目画像、test
命令和 lint 命令共同消费：

1. 顶层 `package.json.packageManager` 明确声明 npm、pnpm 或 yarn 时优先。
2. 无显式声明时，单一 lockfile 决定包管理器。
3. 只有 `package.json` 时默认 npm。
4. 多个 lockfile 冲突时不猜测，不生成 Node test/lint 命令。
5. 显式声明存在但损坏或不受支持时同样停止猜测。
6. tracked profile 从固定 Git revision 读取 `packageManager`，不受未提交工作区修改污染。
7. `.vega.yaml` 显式 verification 命令继续保持最高优先级。

## 三、本地验证

预注册的 9 个 M-002 节点：

```text
9 passed in 3.18s
```

完整收集与分层全量验证：

```text
538 collected
537 passed, 1 skipped
```

唯一跳过节点只覆盖 POSIX shell 变量展开语义，在 Windows 本地按测试合同跳过；Linux CI
必须真实通过对应 POSIX job。

静态门禁全部通过：

- `python -m compileall -q src scripts/check_repository_hygiene.py`
- `ruff check src tests scripts/check_repository_hygiene.py --no-cache`
- `python scripts/check_repository_hygiene.py --base-ref origin/main`
- `git diff --check`

结构化摘要位于：

- `examples/evidence/m002-node-package-manager-local-summary.json`
- `eval/assurance-validation.md` 尾部的 `AV-M002-001` 记录

首次全量分片尝试因显式 basetemp 的父目录未创建而在 fixture setup 阶段失败；该尝试不计入
产品裁决。创建受控父目录后，所有 538 个节点已完整重跑并得到上述结果。

## 四、在另一台机器继续

首次检出该分支：

```powershell
git fetch origin
git switch --track origin/fix/node-package-manager-selection
```

如果本地已经存在该分支：

```powershell
git switch fix/node-package-manager-selection
git pull --ff-only
```

确认接力点：

```powershell
git status -sb
git log --oneline -3
python -m pytest -q `
  tests/test_context_boundaries.py::test_project_profile_selects_one_node_package_manager `
  tests/test_context_boundaries.py::test_project_profile_fails_closed_for_conflicting_node_lockfiles `
  tests/test_context_boundaries.py::test_tracked_project_profile_reads_package_manager_from_fixed_revision `
  tests/test_context_boundaries.py::test_explicit_verification_commands_remain_above_node_auto_detection
```

## 五、下一步

建议按以下顺序继续：

1. 人工通读 `origin/main...HEAD`，重点看选择优先级和 tracked revision 读取。
2. 如需远端验证，只创建 Draft PR，不提前合并。
3. 等待静态检查、Python 3.11、Python 3.12 五分片、Windows、POSIX 和构建安装 job 全部
   通过。
4. 将最新 PR head 与 workflow 结果以追加方式写入 `eval/assurance-validation.md`。
5. CI 与人工 diff 都无阻塞发现后，再单独讨论是否合并；本接力点不授权自动合并或发版。

## 六、剩余边界

- 本轮不判断 `scripts.test` / `scripts.lint` 是否存在。
- 不处理嵌套 workspace、bun/deno 或 Corepack 自动安装。
- 冲突与未知声明目前以“不选择、不执行”fail-closed，尚未新增面向用户的版本化诊断字段。
- Windows Python 3.14.3 的本地结果不能替代 CI 的 Python 3.11/3.12 和 POSIX 证据。
