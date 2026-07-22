# M-002 Node 包管理器选择接力

> 日期：2026-07-22
>
> 分支：`fix/node-package-manager-selection`
>
> PR：`#5`
>
> 当前裁决：`passed-pr-ci / final-docs-ci-required / do-not-merge`

## 一、先看结论

M-002 已完成实现、本地验证、独立审阅修正和代码 head 的跨平台 CI。当前还不能立即合并：
本接力文档、最终 post-CI 证据和 Roadmap 更新会形成一个纯文档 head，该最新 head 仍须通过
PR CI，之后才能转为 Ready 并合并。

关键提交：

- 基线：`6b74c5be3e26d6e9d7e5849b1ec76dd9c5b5f2b3`
- 红灯预注册：`2fad84cdc16a67eacdc9579dfb229e726c002cf1`
- 实现提交：`c6b5325d025c64270c2bd1fa98fb3b7ae9dc8e2f`
- 审阅修正：`9e649ded05ebfa8f272f7e2bd1b134ac9207170f`

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

## 四、独立审阅与 PR CI

两份独立审阅中，实现审阅未发现阻塞性代码问题；测试与证据审阅发现“损坏或不受支持的
`packageManager` 声明 fail-closed”缺少直接回归。该发现已在 `9e649de` 修正：

- 增加非字符串声明回归。
- 增加不受支持的 `bun` 声明不能回退到陈旧 lockfile 的回归。
- 相关选择链路：`11 passed`。
- 完整收集合同：`540` 个节点。

PR `#5` 已形成两次成功 workflow：

```text
29923884827  1b8d2cc  10/10 success  首轮 538 节点
29924503421  9e649de  10/10 success  审阅修正后 540 节点
```

第二次 workflow 覆盖静态检查、Python 3.11 全量、Python 3.12 分片、Windows、POSIX 和
wheel 构建安装。它证明代码 head `9e649de` 满足 M-002 的跨平台退出条件。

## 五、在另一台机器继续

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
  tests/test_context_boundaries.py::test_project_profile_fails_closed_for_invalid_package_manager_declaration `
  tests/test_context_boundaries.py::test_tracked_project_profile_reads_package_manager_from_fixed_revision `
  tests/test_context_boundaries.py::test_explicit_verification_commands_remain_above_node_auto_detection
```

## 六、下一步

1. 推送本次纯文档提交，等待 PR `#5` 最新 head 的 workflow 全部通过。
2. 最新 head CI 全绿后，把 PR 从 Draft 转为 Ready。
3. 再次核对 mergeable、head SHA 和 checks，使用 squash merge 合入 `main`。
4. 等待合并后的 `main` CI 全绿，再开始独立的 M-003；不在 M-002 分支顺带实现。
5. 不打新标签，不发布 GitHub Release 或 PyPI。

## 七、剩余边界

- 本轮不判断 `scripts.test` / `scripts.lint` 是否存在。
- 不处理嵌套 workspace、bun/deno 或 Corepack 自动安装。
- 冲突与未知声明目前以“不选择、不执行”fail-closed，尚未新增面向用户的版本化诊断字段。
- Windows Python 3.14.3 的本地结果不能替代 CI 的 Python 3.11/3.12 和 POSIX 证据。
