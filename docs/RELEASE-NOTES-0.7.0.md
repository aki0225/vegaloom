# Vega v0.7.0 发布说明

源码包版本为 `0.7.0`，发布状态及可下载资产以 GitHub Release 为准。
安装前核对 [v0.7.0 Release](https://github.com/aki0225/vegaloom/releases/tag/v0.7.0)
实际资产；能力概览见 [版本摘要](RELEASE-SUMMARY-0.7.0.md)。
本说明概述 v0.6.0 之后的实现与发布前修复，最终提交与制品以正式发布记录为准。

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
- 可信 Brief 复制保留已脱敏原始字节，避免 Windows 换行转换破坏绑定；
  完整性检查仍拒绝篡改。证据完整且验证通过的人工确认终点不再误报为验证中断。

## 边界与历史证据

受控本地权限样例已实际经过 Worker、Candidate、固定验证（15项测试及diff检查）、
独立只读Reviewer及预期高风险人工确认边界；这不是生产权限系统安全证明。
Reviewer自然给出 needs_human，没有自然触发 request_changes、repair 或 approve。
范围内返修有定向测试覆盖，但上述真实完整链仍未覆盖，不宣称 DAILY-03 完成；
当前计划仍为36/37。发布不强迫独立 Reviewer 产生预设的审查结论序列，不以假Runner补造真实结论。
人工确认不等于自动成功，既有验证、风险、范围和完整性底线不变。

[日用交付记录](DAILY-DELIVERY-0917.md) 和
[权限改造计划及验收](WORKER-PERMISSIONS-PLAN.md) 记录各自当时范围：
定向回归、BOM 与日用真实任务不是最终 0.7.0 制品或跨平台验收；
历史通过不替代本版本 CI，历史失败和未覆盖项保持原结论。

升级前核对未完成 Run 的协议和证据，先用 status/explain；不手改状态或把旧人工门禁
改成成功。更新生成 Skill 前核对项目定制内容，不默认强制覆盖。
Full Access 的 worktree 不是 OS 隔离；Vega 不自动 push、合并、部署或解除人工风险。

## 发布核对流程

1. 复核代表主路径的真实运行结果与未覆盖限制；保留失败记录，不强迫 Reviewer 产生指定结论。
2. 对最终提交执行完整验证、编译、Ruff、架构、卫生、计划及 diff 检查；核对精确
   PR HEAD CI 和合并后 main CI，不引用旧版本绿勾代替。
3. 从最终干净提交构建 wheel/sdist，检查内容、元数据、内置资源和敏感文件；
   在独立环境分别安装，核对 pip check、隔离导入来源、版本、CLI 与生成 Skill。
4. 核对 Tag 与最终提交、制品 SHA256、上传后的下载与安装。下载前核对 Release 实际资产。

历史准备提交已有全量CI与独立安装证据，但不替代最终提交的CI和干净制品验收。
安全支持策略见 [SECURITY.md](../SECURITY.md)。
