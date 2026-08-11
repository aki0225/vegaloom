# Vega v0.1.5 发布说明

v0.1.5 是面向日常使用的可信完成与可恢复执行版本。它不增加默认 Agent 角色或通用调度
框架，而是把调查、修改、验证、评审和人工接管之间的边界做得更明确。

## 日常使用

- 固定 Plan-first 协议：模糊 Bug 先由主会话调查，区分观察事实、根因假设、建议范围、
  验证方式和未决问题；人工确认后再修改。
- Finish 第一屏从现有结构化 artifact 确定性汇总裁决、变更文件、Scope、验证、风险门禁、
  Reviewer 意见、证据上限和下一步，不新增总结模型或第二套成功判断。
- GitHub Pages 展示站公开说明写审分离、确定性验证、三种任务结局和脱敏真实案例。

## Reviewer 与证据

- Reviewer 必须覆盖全部变更文件，不能只声明少量重点文件后遗漏其余 diff。
- 高风险人工审查不会抹掉已经通过的确定性验证证据；风险披露、验证结果和最终人工确认保持
  分层。
- Worker 无副作用自检不再允许 Python 缓存等运行产物污染目标仓库。
- RCB-01、RCB-02 和 RCB-03 均按预注册停止条件完成，没有把未达到门槛的检索或提示实验接入
  默认 Reviewer。

## Goal P1 与显式 Worker 重跑

- 单 checkpoint Goal 控制器保持实验状态，不自动串联多个 checkpoint。
- Worker 中断且没有形成新成果时，普通 continue 无副作用拒绝；只有人工显式使用
  `--rerun-worker` 才会在同一 child 的下一 iteration 重跑。
- Worker baseline V2 使用摘要绑定 tracked、untracked、ignored、Git index 标记和有界
  ignored 后代清单，不在 artifact 中保存敏感原始路径。
- 来源 baseline、授权 state、trace、iteration 生命周期、最终工作区快照和
  `worker_started` 形成可验证因果链；证据缺失、重复、重排或不一致时 fail-closed。
- 重跑事务覆盖 baseline 准备、iteration claim 和 Worker 启动边界。事务文件因 Windows
  文件锁无法删除时，不调用可写 runner；锁释放后由 Recovery 明确交还人工。

## 公开证据

- CRWP-V1 三个 Case 均达到合同允许终态，不选择性重跑失败案例。
- Phase 4 覆盖 Codex assist、Claude Code assist、`vega do`、Reviewer 打回和
  fail-closed 五类真实场景。
- Goal P1 r6 真实 Codex dogfood 证明人工显式重跑可以在同一 child 中完成；该证据不外推为
  数小时或跨天无人值守自治。

## 不变边界

- Vega 不自动 commit、push、release、删除目标文件或写入长期 Memory。
- 不增加 daemon、数据库、多 Worker、自动重试、Web UI 或新的默认 Runtime。
- reviewer 与 worker 保持会话上下文隔离，但不宣称容器或操作系统级安全隔离。
- Goal、Memory、Adapter、Assurance 和 Reviewer Context 实验不扩大核心 loop 的成功条件。
