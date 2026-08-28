# Vega v0.3.0

v0.3.0 将 Vega 整理为一条可交互的 ChangeRun：

```text
调查与合同
  -> 人工批准
  -> 持久 Worker
  -> Git Candidate
  -> 验证与独立 Reviewer
  -> Repair / Replan / Human
  -> 最终报告
```

本版本的重点：

- Codex App Server Worker Thread 复用；
- Work Item 级独立只读 Reviewer Thread；
- 状态卡、`watch`、Steer、审批响应和原生会话接管；
- 条件式最终集成审查；
- 顶层公共 CLI；
- 确定性 Agent Final Report；
- Git Task Card 跨会话和跨机器恢复。

真实两 Work Item Dogfood 已验证 Worker Thread 复用、主会话 Steer、Work Item 级独立
Reviewer、累计集成审查和确定性最终报告。Dogfood 暴露的 Windows 子进程泄漏、进程树收尾
以及中间 Work Item 验证边界问题均在候选中修复，失败记录原样保留。

Vega 仍把测试、风险规则和真实 Git Candidate 放在模型结论之前。它不会自动 push、merge、
release 或执行生产动作。

> 当前文件描述发布候选；正式版本以 Git Tag 和 GitHub Release 为准。
