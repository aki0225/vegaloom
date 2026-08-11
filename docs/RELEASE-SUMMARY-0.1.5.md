# Vega v0.1.5 发布摘要

> 版本：v0.1.5

这份摘要用于 GitHub Release 文案。详细变更见
[`RELEASE-NOTES-0.1.5.md`](RELEASE-NOTES-0.1.5.md)，发布步骤见
[`RELEASE-CHECKLIST.md`](RELEASE-CHECKLIST.md)。

## 一句话

Vega v0.1.5 把日常 AI 编码流程中的调查、验证、独立评审和中断恢复整理成可核验路径，并为
无成果的 Worker 中断增加人工显式、fail-closed 的重跑方式。

## 本版本重点

- 模糊任务先调查并形成区分事实与假设的 Plan，经人工确认后再修改。
- Finish 第一屏确定性展示改动、验证、风险、Reviewer 结论、证据限制和下一步。
- Reviewer 覆盖全部变更文件，高风险人工确认不会抹掉已通过的验证证据。
- Worker 自检不再允许 Python 缓存等运行产物污染目标仓库。
- 单 checkpoint Goal 与 `--rerun-worker` 保持实验、人工触发；baseline V2、授权因果链、
  最终工作区复查和可恢复事务共同防止覆盖 partial work 或启动两个 Worker。
- GitHub Pages 展示站与 CRWP、Phase 4、Reviewer Context、Goal P1 证据同步公开；未达到门槛
  的实验没有接入默认 Runtime。

## 不变边界

- 不增加 Planner Agent、Multi-Worker、daemon、数据库、自动重试或新的默认 Runtime。
- Vega 不自动 commit、push、release、删除目标文件或写入长期 Memory。
- reviewer 与 worker 保持会话上下文隔离，但不宣称系统级安全沙箱。
- 显式 Worker 重跑只证明受控恢复路径，不证明无人值守长时间自治。

## 发布依据

- PR #55 最终提交与合并后主线均为 9/9 GitHub checks 通过。
- recovery chaos、workspace snapshot、P0、CLI recovery、Windows、POSIX 和 package smoke
  由 CI 与分片证据共同覆盖。
- Goal P1 r6 提供真实 Codex 显式重跑证据；发布候选只修改版本、CI 版本断言和发布文档。
