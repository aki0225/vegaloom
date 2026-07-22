# LangGraph 公开归档身份脱敏闭环

> 日期：`2026-07-22`
>
> 修复分支：`fix/langgraph-public-sanitization-closure-v2`
>
> 同源基线：公开标签 `v0.1.0`
>
> 最终实验分类：`partial`

## 1. 问题

二次公开审计使用源实验对象库和公开对象库逐项对照 Git 身份。既有公开归档 tree 中仍有：

- `36` 个完整源实验 commit/tag object 身份，共 `102` 处引用；
- `18` 个源实验 commit 缩写，共 `106` 处引用；
- `2` 处真实 Windows 主机名。

完整和缩写集合有重叠，因此不能把两组数量相加解释为独立 commit 数。泄漏内容属于实验
历史和本机环境元数据，不是凭证。审计未发现真实 API key、Authorization token、Cookie、
私钥、带凭证 URL 或 `.env` 内容。

首轮候选推送后的独立复扫还发现 `1` 个源实验实现起点 SHA 被误登记为测试常量。v2 分支
已将其替换为稳定语义标签，并删除错误白名单。为确保该身份不留在可达历史中，v2 同样从
公开 `v0.1.0` 重建单提交归档，而不是在首轮候选上追加修复。

## 2. 修复边界

本次只允许：

1. 把源实验 Git 身份替换为稳定语义标签；
2. 保持同一原始身份在不同文档和 JSON 中使用同一标签；
3. 把真实 Windows 主机名替换为 `<windows-sandbox-host>`；
4. 增加机器审计脚本和 CI 入口；
5. 更新公开交接文档。

禁止改变：

```text
final classification = partial
default engine = linear
default reviewer topology = single
LangGraph = optional experimental recovery / HITL control plane
Goal / Checkpoint / Handoff = engine-neutral
```

`eval/gate-7/result-*.json` 只替换 commit、tag object 和主机名字段。status、failure、metric、
token、retry、checkpoint 和结论保持原值。这是公开身份脱敏，不是重新运行实验，也不是
润色负面结果。

## 3. 历史策略

如果在既有公开归档提交上追加修复 commit，旧身份仍会通过父历史可达，无法满足分支历史
审计。因此修复分支必须：

1. 以公开 `v0.1.0` 为唯一父历史；
2. 使用修复后的完整归档 tree；
3. 在 `v0.1.0` 之上只包含一个归档提交；
4. 使用 GitHub noreply 提交身份；
5. 不 merge、rebase 或 force-push 既有公开归档分支。

既有 `experiment/langgraph-comparison` 和 `fix/langgraph-archive-ci` 不因本次新分支推送而
自动消失。它们是否更新、删除或标记为 superseded，由 owner 另行决定。

## 4. 机器检测

```powershell
python scripts/check_langgraph_public_archive.py --history-base v0.1.0
```

脚本检查：

- `v0.1.0..HEAD` 恰好一个 commit；
- commit 作者邮箱属于公开允许域；
- 7 到 40 位未知 Git 身份；
- Windows 真实主机名；
- 本机私有根路径；
- 私有实验 remote 标识；
- 非示例邮箱。

提交前模式还会扫描未被 Git 忽略的未跟踪文件，防止待提交内容绕过工作区检查。

允许的十六进制身份只包括：

- 当前公开仓可解析的 commit；
- 明确登记的公开主线/发布 commit；
- GitHub Actions 固定版本；
- synthetic fixture、tree identity 和测试常量。

错误只输出规则、相对路径、行号和公开 commit 缩写，不回显疑似敏感值。

## 5. 当前风险

修复分支推送后可以证明“新的候选分支 tree 和可达历史已脱敏”，但不能证明旧 GitHub
commit URL、Actions 日志、缓存或旧分支立即不可访问。

本次没有发现凭证，因此无需凭证轮换。若以后发现真实凭证，必须立即轮换，不能依赖 Git
历史重写作为凭证处置手段。

## 6. Owner 决策

修复分支 CI 通过后，owner 可选择：

1. 保留旧归档分支，并明确标记为 superseded；
2. 经单独确认后，让旧归档分支采用新脱敏 tree；
3. 删除不再需要的旧公开 ref；
4. 创建不合并到 `main` 的 decision-record PR。

本分支不自动执行上述治理动作，不合并 `main`，不打标签。
