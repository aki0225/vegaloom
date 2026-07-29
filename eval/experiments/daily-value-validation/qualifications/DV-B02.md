# DV-B02 资格确认记录

- 日期：2026-07-29
- Case：`DV-B02`
- 结论：`runnable`
- Provider 调用：未调用
- 核心 Runtime 修改：无

## 1. 上游任务

- 上游仓库：`python-attrs/attrs`
- Issue：`#1348`
- baseline commit：`ee0f19b696c60064c58cdc08b3265aef56d49ff8`
- oracle commit：`e21793e90a25c7ea47a9c0369150067cc8322de0`

Issue 正文给出 `optional(pipe(str, int))` 的真实回归和版本对照，没有给出最终实现。oracle 是
修复合并提交，baseline 是其第一父提交。两个 tree 之间只涉及：

- `src/attr/converters.py`
- `tests/test_converters.py`
- `changelog.d/1372.change.md`

正式 Worker 允许修改前两个行为相关文件，不要求修改 changelog。

## 2. 固定任务与 verifier

冻结任务合同：

```text
docs/experiments/daily-value-validation/tasks/DV-B02.md
```

独立 verifier：

```text
eval/experiments/daily-value-validation/verifiers/DV-B02.py
```

verifier SHA-256：

```text
36111049980d30ad126ddbd1fc0247c870f068eeb3db01def559312f099b1b47
```

固定检查四种语义：

1. 非 `None` 输入可以通过 `optional(pipe(...))` 完成转换。
2. `None` 继续绕过内部 pipeline。
3. pipeline 中需要实例与字段的 converter 能收到上下文。
4. `optional` 包装普通 callable 的既有行为保持不变。

## 3. Windows 红绿复现

- 操作系统：Windows 10
- Python：`3.14.3`
- 本地隔离目录：`.local-validation/daily-value-v1/qualification/DV-B02/`
- 每个 ref 使用独立 detached worktree

同一 verifier 分别连续运行三次：

| Ref | 三次退出码 | 三次输出是否一致 | 代表性输出 SHA-256 |
|---|---|---|---|
| baseline | `1 / 1 / 1` | 是 | `105f943da8f39426f1f244ea4fe0516dd398a1264526f02bb5f641c6aa99ef57` |
| oracle | `0 / 0 / 0` | 是 | `654a573fc679eb0206ca3c20cd1f196430ab1562048915e7db1cf39edd3dd07b` |

baseline 的非 `None` pipeline 和上下文传播检查失败，`None` 与普通 callable 兼容检查通过；
oracle 的四项检查全部通过。

## 4. 依赖安装

两个 ref 均在独立 venv 中从源码构建并安装成功：

- baseline 包版本：`attrs 24.2.1.dev36`
- oracle 包版本：`attrs 24.2.1.dev37`
- `python -m pip check`：两个环境均输出 `No broken requirements found.`
- 安装后 verifier：baseline 红、oracle 绿

两个资格 worktree 在安装和验证后均保持 clean。

## 5. Baseline-only workspace 预演

使用 `git archive` 从 baseline 导出全新源码树，并把 verifier 放在 workspace 同级父目录。
预演结果：

- workspace 共 123 个文件，不包含 `.git`。
- 精确检索 Issue URL、`#1348`、oracle SHA、关联 PR 编号和修复标题均无命中。
- `python ../verifier.py .` 返回退出码 `1`。
- verifier 前后 workspace 文件 hash 集合一致。
- verifier 输出 hash 与 worktree baseline 的代表性红态 hash 一致。

正式 treatment 必须重新创建目录，不得复用资格预演 workspace。

## 6. Codex 执行配置预检

按照 `CODEX-EXECUTION-PROFILE.md` 运行离线预检：

- 冻结模型存在于当前模型目录。
- 当前 Provider profile 使用 `responses` 协议。
- 生成参数不包含 `--ignore-user-config`。
- hooks、Memory、Goal、多 Agent、插件和浏览能力均显式关闭。
- 未调用 Provider。

profile fingerprint 只保存在 `.local-validation/`，Native 与 Vega 必须在各自调用前核对一致。

## 7. 资格门裁决

| 资格门 | 结果 | 证据 |
|---|---|---|
| 固定 baseline | passed | 40 位 commit，且是 oracle 第一父提交 |
| baseline verifier 为红 | passed | 连续三次退出码 1，输出 hash 一致 |
| 固定上游绿态 oracle | passed | 连续三次退出码 0，四项检查全绿 |
| 固定任务、路径与验证 | passed | 冻结任务合同与独立 verifier |
| 固定模型与 timeout | passed | `gpt-5.6-sol` / `medium` / 600 秒 |
| Windows 依赖安装 | passed | 两个独立 venv 安装成功并通过 `pip check` |
| Worker 不直接获得 patch | passed | 任务脱敏，baseline-only，无 Issue URL、PR 或 Git 历史 |
| Provider 路由离线预检 | passed | 路由保留，实验外 feature 显式关闭 |

DV-B02 可以追加 `revision=2, status=runnable`。该结论只允许按 Vega → Native 顺序各开始
一次正式 treatment，不代表 Vega 已证明有日用价值。
