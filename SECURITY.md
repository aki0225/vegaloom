# 安全策略

## 支持版本

| 版本 | 是否支持 |
|---|---|
| 0.2.x | 是 |
| 0.1.x | 否 |

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
