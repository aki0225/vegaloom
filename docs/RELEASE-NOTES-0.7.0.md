# Vega v0.7.0 发布准备

源码包版本为 `0.7.0`，尚未打 Tag 或发布。正式稳定版仍为 `v0.6.0`；
安装与使用请查看 [v0.6.0 文档](https://github.com/aki0225/vegaloom/blob/v0.6.0/README.md)。
本说明依据 `v0.6.0..ded05b8` 的实现差异，不将准备状态表述为已发布。

## 主要变化

- 显式选择并绑定 Worker 权限；Codex 原生模式核验及有限同终端 command/file 审批。
  不宣称自动继承宿主权限；Reviewer 仍独立只读，Claude 不冒充 Codex 同等机制。
- 改进 pre-Core 恢复、Candidate 文件恢复、人工 Plan-only 修订与批准记录保留；
  人工待批不消耗自动 Replan 预算，不放宽证据与固定验证。
- 主会话自行推进与直接技术汇报；验收材料作为编译后的不可信辅助数据进入审查。
  明确 `acceptance_missing` 时可对原 Candidate 补验再审，不启动新 Worker；
  审查后材料缺失或篡改仍阻断，旧未分类人工阻断不自动迁移。
- 显式合同可授权高风险范围内返修，最终交付仍需人工确认；可选 suggestion 不强制返修。
- 环境准备进度、预算和准备策略错误更清楚；过期 steer 提前拒绝；状态区分受控
  Candidate 过渡和真正证据问题，损坏执行记录不能保留继续或提交建议。
- 显式 start 合同与计划兼容 UTF-8 BOM；报告优先展示最新验证、保留历史失败。
- Windows CI 每条 native 命令检查退出码，防止早期失败被后续成功覆盖。

## 边界与历史证据

DAILY-03 的真实高风险 `request_changes → repair → approve → needs_human`
链路仍缺验收，不能用 fake Runner、预设 Reviewer 或低风险任务替代。
本阶段不追加计划完成事件，也不改变成功语义、风险门禁或执行授权。

[日用交付记录](DAILY-DELIVERY-0917.md) 和
[权限改造计划及验收](WORKER-PERMISSIONS-PLAN.md) 记录各自当时范围：
定向回归、BOM 与日用真实任务不是最终 0.7.0 制品或跨平台验收；
历史通过不替代本版本 CI，历史失败和未覆盖项保持原结论。

升级前核对未完成 Run 的协议和证据，先用 status/explain；不手改状态或把旧人工门禁
改成成功。更新生成 Skill 前核对项目定制内容，不默认强制覆盖。
Full Access 的 worktree 不是 OS 隔离；Vega 不自动 push、合并、部署或解除人工风险。

## 发布前待核对

1. 完成 DAILY-03 缺失的真实验收；不能强迫独立 Reviewer 产生指定结论。
2. 对最终提交执行完整验证、编译、Ruff、架构、卫生、计划及 diff 检查；核对精确
   PR HEAD CI 和合并后 main CI，不引用旧版本绿勾代替。
3. 从最终干净提交构建 wheel/sdist，检查内容、元数据、内置资源和敏感文件；
   在独立环境分别安装，核对 pip check、隔离导入来源、版本、CLI 与生成 Skill。
4. 核对 Tag 与最终提交、制品 SHA256、上传后的下载与安装。当前没有 0.7.0 下载承诺。

本次准备 PR 只做直接受影响回归与静态门禁；完整 pytest 和最终干净打包留待审阅提交后。
安全支持窗口仍待单独决定，本说明不替代 SECURITY.md 的支持政策。
