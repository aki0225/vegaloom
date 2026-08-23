# Vega Task Card 目录

- `.vega/tasks/` 只保存当前仍可发现、可继续的 Git Task Card。
- `.vega/archive/tasks/` 保存已经结束的历史 Task Card，不参与默认发现；项目约定不将其作为恢复输入。
- 归档时保持卡片正文和机器载荷字节不变；生命周期由目录位置区分，不改写历史签名状态。

归档卡片仅用于审计旧任务。需要继续同类工作时，应基于当前仓库事实创建新的 Agent run 和
Task Card，不能把归档卡片重新移回活动目录。
