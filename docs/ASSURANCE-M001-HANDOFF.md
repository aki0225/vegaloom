# Assurance M-001 接力说明

> 更新时间：2026-07-22
> 任务：Adapter 真实路径边界
> 分支：`fix/adapter-realpath-boundary`
> PR：`#2`
> 基线：`main@1e9bb52`
> 实现提交：`f44dca1772517bfe1f531a0be28f6c073472c527`

## 当前结论

`M-001` 已完成实现和 Windows 本地验证，当前状态为：

```text
passed-local / requires-ci
```

代码已经推送到公开远端并创建 PR，但没有合并。必须等 PR 的静态检查、Python 3.11 全量、
Python 3.12 五个分片、Windows 专项、POSIX 专项和发布包安装验证全部通过后，才能提出合并
建议。

## 已完成内容

- adapter 写入前规范化目标仓库并预检整批真实目标路径。
- 任一目标越界或无法解析时 fail-closed，不产生同批次部分文件。
- 创建父目录后、写文件前再次解析逻辑目标。
- `--force` 和既有文件 skip 均不能绕过边界检查。
- 保留真实目标仍位于仓库内的 junction/symlink 用法。
- 新增真实 Windows junction / POSIX symlink 危险案例、安全双生案例和批次预检回归。
- CI 收集合同由历史 516 更新为当前 520，并把新测试加入 Linux 分片和 Windows 专项。
- `eval/assurance-validation.md` 已按只追加规则记录 preregistration、local result 和首次推送
  `GH007` 的 transport correction。

## 关键验证

旧实现复现：

```text
3 failed, 1 passed
```

当前候选：

```text
定向：5 passed
收集：520 个节点，520 个唯一 nodeid
本地完整覆盖：519 passed, 1 skipped
```

唯一跳过是仅适用于 POSIX shell 的既有测试：

```text
tests/test_runtime_safety_integration.py::test_posix_verification_temp_env_does_not_re_evaluate_path
```

它不能由 Windows 本地结果替代，PR POSIX job 必须真实通过。

本地证据摘要：

```text
.tmp/pytest/logs/m001-full-summary-v2.json
SHA-256:
F726417D3DB498970CF70E64F758852AC1DEA0E3B266A10CF0CAA3E85CC69345
```

`.tmp` 不提交；另一台电脑无法仅凭该哈希重建原始本地日志，因此跨机器接力以公开 PR CI 为
主要证据，本地摘要只保留为本机复核线索。

## 另一台电脑接手

在已有仓库中：

```powershell
git fetch origin
git worktree add F:\workspace\vegaloom-adapter-realpath `
  origin/fix/adapter-realpath-boundary
```

如果目标目录已存在，先检查：

```powershell
git -C F:\workspace\vegaloom-adapter-realpath status -sb
git -C F:\workspace\vegaloom-adapter-realpath log -3 --oneline
```

不要对存在未提交改动的目录执行 reset、clean 或强制切分支。

## PR CI 通过后的步骤

1. 核对 PR `#2` 的所有 job 都是 `success`，不能只看旧 commit status。
2. 记录 workflow ID、PR head SHA、各 job 结论和 Python 测试计数。
3. 只向 `eval/assurance-validation.md` 追加 `AV-M001-001 · post-CI result`，不得改写现有记录。
4. 复核 PR 相对 `main` 的完整 diff，重点检查：
   - 仓库外真实路径是否仍可能被写入。
   - `--force` 和 skip 是否可能绕过检查。
   - 安全的仓库内链接是否被无差别拒绝。
   - CI 的 520 节点和分片文件合同是否一致。
5. CI 与人工 diff 复核都通过后，再向用户提出合并建议；不要自动合并。
6. M-001 合并并完成 post-merge CI 前，不开始 M-002。

## 剩余风险

- `Path.resolve` 与最终写入之间仍存在 TOCTOU 窗口。
- hardlink、句柄级 no-follow、恶意并发目录替换和跨进程事务不在本轮范围内。
- 本地 Python 3.14.3 在高 I/O 负载下明显慢于 CI；本地耗时不能替代 CI 的 58 秒单节点预算。

## 明确不做

- 不自动合并 PR。
- 不打标签、不发布 GitHub Release 或 PyPI。
- 不顺带处理 Node 包管理器选择、Finish 快照复用或 Stage 1 Threat/Evidence 数据模型。
