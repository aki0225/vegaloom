# Vega v0.3.1 发布摘要

> 状态：已发布。注解 Tag `v0.3.1` 绑定提交
> `5ee8328fa6c670c7feb788b5daa62cfe5615bb0f`。

详细变更见
[`RELEASE-NOTES-0.3.1.md`](https://github.com/aki0225/vegaloom/blob/main/docs/RELEASE-NOTES-0.3.1.md)，
发布步骤见
[`RELEASE-CHECKLIST.md`](https://github.com/aki0225/vegaloom/blob/main/docs/RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.3.1 收紧跨目录接手时的内容核对：Task Card 统一 LF 摘要，Workspace 摘要
绑定 Git mode 与 Blob，并让重复恢复和损坏现场继续 fail-closed。

## 本版本重点

- 统一 Task Card 文本摘要的换行语义，避免 LF/CRLF checkout 造成合法恢复误拒；
- `git-blob-v1` 同时绑定文件类型、权限和内容；
- ChangeRun 创建和恢复使用不易碰撞的任务身份，避免快速连续操作复用同一分支；
- 重复 Resume Claim 不留下没有证据的空 run，已有失败现场仍保留；
- Task Card 显示 Workspace 摘要类型，便于人工核对；
- `VALID-02` 追加了真实验收和回归证据，覆盖跨 checkout 恢复、内容漂移、mode 变化和
  必审风险的 fail-closed 行为；
- wheel 与 sdist 已通过干净环境 smoke；从候选 wheel 启动的真实 Codex ChangeRun 完成
  Worker、Candidate、Verification、独立 Reviewer 和 Finish，终态为 `ready_to_commit`。

## 不变边界

- 不增加新的公共命令、Provider 或成功路径；
- 现有 Verification、Risk、Reviewer 隔离和人工接管语义保持不变；
- Vega 不自动 push、merge、release、删除用户文件或接受长期 Memory。

> 发布事实以注解 Tag 和
> [GitHub Release](https://github.com/aki0225/vegaloom/releases/tag/v0.3.1) 为准。
