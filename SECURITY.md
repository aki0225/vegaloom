# 安全策略

## 支持版本

最新正式稳定版本受支持，以 [GitHub latest](https://github.com/aki0225/vegaloom/releases/latest)
为准。旧版本建议升级，不承诺为旧版本单独回移补丁。源码准备版本不等于正式稳定发布。

## 报告漏洞

请通过 GitHub Security Advisory 私下报告安全漏洞：

`https://github.com/aki0225/vegaloom/security/advisories/new`

请勿在公开 Issue 中提交：

- Token 或 API key
- 真实 provider 原始输出
- Run artifact
- Workspace fingerprint

## 安全边界

Vega 不是沙箱隔离系统，不是 EDR，也不是 DLP。外部 runner、模型 provider、目标仓库和验证命令仍需由使用者独立评估和隔离。
