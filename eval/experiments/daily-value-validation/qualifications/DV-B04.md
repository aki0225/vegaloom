# DV-B04 资格确认记录

- 日期：2026-07-29
- Case：`DV-B04`
- 结论：`runnable`
- Provider 调用：未调用
- 核心 Runtime 修改：无

## 1. 候选筛选

本轮先筛选了 Owner 自有 GitHub 仓库中的真实修复提交。最窄的候选虽然代码面合适，但其
baseline 已包含会被 Worker 读取的任务设计文档，文档直接写出常量和计算公式，无法形成
盲测；另有私有仓库候选存在公开复核与隐私边界歧义。因此没有为了凑样本强行登记。

最终选择公开上游 `pallets/click #2836`。该任务是日常开发中常见的窄行为 Bug，复现不依赖
浏览器、数据库、外部服务或秘密，且同一 verifier 可在亚秒级给出确定性结果。

## 2. 上游事实

- 上游仓库：`pallets/click`
- Issue：`#2836`
- Issue 创建时间：2025-01-06
- Issue 关闭时间：2026-04-16
- baseline commit：`8c95c73bd5ef89eac638f85f1904a104ba4b1a32`
- oracle commit：`76552ff1e8c85837f911fc34037e702ae4327eda`

baseline 是修复合并前的 `stable` tree，且是 oracle merge commit 的第一父提交。两个 tree
之间只涉及五个文件：两个源码文件、两个测试文件和 changelog；正式 Worker 允许修改前四个
行为相关文件，不要求修改 changelog。

Issue 正文只描述错误行为和期望输出，没有给出最终 patch。Issue 评论包含关联 PR 链接，
因此正式 Worker 合同不包含 Issue URL，且 workspace 不保留完整 Git 历史，也不允许外部
检索。Native 与 Vega 两组应用完全相同的盲测边界。

## 3. 固定任务与 verifier

冻结任务合同：

```text
docs/experiments/daily-value-validation/tasks/DV-B04.md
```

独立 verifier：

```text
eval/experiments/daily-value-validation/verifiers/DV-B04.py
```

verifier SHA-256：

```text
3d549fc0f033cd6fe126946eb63f59536439ed9fac0a6c1c7384cd6cc4367bf0
```

固定检查四种语义：

1. 非空字符串标签替代真实默认值。
2. 真实默认值为 `None` 时仍显示字符串标签。
3. 空字符串隐藏真实默认值。
4. `show_default=True` 继续显示真实默认值。

正式运行时将 verifier 复制到 treatment workspace 的同级父目录，在 workspace 中执行：

```powershell
python ../verifier.py .
```

## 4. Windows 红绿复现

- 操作系统：Windows 10
- Python：`3.14.3`
- 本地隔离目录：`.local-validation/daily-value-v1/qualification/DV-B04/`
- 每个 ref 使用独立 detached worktree

同一 verifier 分别连续运行三次：

| Ref | 三次退出码 | 三次输出是否一致 | 代表性输出 SHA-256 |
|---|---|---|---|
| baseline | `1 / 1 / 1` | 是 | `38948737ebc494e7c32ebcf1497ebfb510ef056b07b17926815ee864cf94ca97` |
| oracle | `0 / 0 / 0` | 是 | `5fdfc1a19fd967b759004a903c713d14114097c391142fbbcb3f5dbd77502ee9` |

baseline 的前三个新语义检查稳定失败，布尔兼容检查通过；oracle 的四个检查全部通过。单次
运行约 `0.3`～`0.5` 秒，没有观察到时序或环境抖动。

## 5. 依赖安装

两个 ref 均在独立 venv 中从源码安装成功：

- 安装包版本：`click 8.3.2`
- Windows 运行依赖：`colorama 0.4.6`
- `python -m pip check`：两个环境均输出 `No broken requirements found.`
- 安装后再次运行 verifier：baseline 红、oracle 绿

本地 worktree、venv 和原始日志只保留在 `.local-validation/`，不提交。

## 6. 冻结执行合同

- treatment 顺序：Native → Vega
- 模型：`gpt-5.6-sol`
- reasoning effort：`medium`
- timeout：`600` 秒
- 允许修改：
  - `src/click/core.py`
  - `src/click/termui.py`
  - `tests/test_options.py`
  - `tests/test_termui.py`
- 固定验证：`python ../verifier.py .`

正式 workspace 必须由 baseline tree 导出，不保留 `.git` 历史；任务输入不得包含 Issue URL、
关联 PR、oracle ref、上游 diff 或另一 treatment 的结果。

## 7. Baseline-only workspace 预演

资格确认还使用 `git archive` 从 baseline 导出了一份全新的源码树，并按正式目录结构放置
verifier：

```text
<treatment-root>/
├── verifier.py
└── workspace/
```

预演结果：

- `workspace/.git` 不存在。
- 精确检索 Issue URL、`#2836`、oracle SHA 和修复提交标题均无命中。
- `python ../verifier.py .` 稳定返回退出码 `1`，证明导出过程没有改变红基线。
- verifier 运行前后 SHA-256 均为
  `3d549fc0f033cd6fe126946eb63f59536439ed9fac0a6c1c7384cd6cc4367bf0`。

正式 treatment 仍须分别创建全新目录，不能复用本次资格预演 workspace。

## 8. 资格门裁决

| 资格门 | 结果 | 证据 |
|---|---|---|
| 固定 baseline | passed | 40 位 commit，且是 oracle 第一父提交 |
| baseline verifier 为红 | passed | 连续三次退出码 1，输出 hash 一致 |
| 固定上游绿态 oracle | passed | 连续三次退出码 0，四项检查全绿 |
| 固定任务、路径与验证 | passed | 冻结任务合同与独立 verifier 已提交 |
| 固定模型与 timeout | passed | `gpt-5.6-sol` / `medium` / 600 秒 |
| Windows 依赖安装 | passed | 两个独立 venv 安装成功并通过 `pip check` |
| Worker 不直接获得 patch | passed | 任务脱敏，baseline-only，无 Issue URL、PR 或 Git 历史 |

DV-B04 可以追加 `revision=2, status=runnable`。该结论只允许开始一次 Native 与一次 Vega
正式 treatment，不代表 Vega 已经证明有日用价值。
