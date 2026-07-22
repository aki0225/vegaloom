# Gate 7 R3 Readiness

> 状态：`ready-for-baseline-freeze`
>
> 日期：`2026-07-19`

## 已验证

- R2 case、consumed tag 和失败结果保持原位；
- R3 case overlay 可解析，case/plan hash 已固定；
- R3 session、baseline tag、consumed tag 使用独立命名空间；
- worker Git 临时配置只允许当前 fixture repo 的 `safe.directory`；
- owned subprocess 能继承 `GIT_CONFIG_GLOBAL`，且宿主进程环境不被修改；
- Gate 7 专项测试：`20 passed, 1 skipped`；
- execution control 回归：`17 passed`；
- `compileall`、Ruff、`git diff --check` 通过；
- loopback `/health` 与 `/v1/models` 可达；
- 无模型字段 transport diagnostic 返回上游校验 `400 model is required`，未出现 R2 的
  `502 Bad Gateway`。

## 未验证

- R3 真实 Codex worker 是否能完成 CP01；
- 三个真实 checkpoint 是否能完成；
- Gate 7A 是否成功；
- Gate 7C 是否会被触发；
- LangGraph 相对 linear 的恢复成本；
- 真实物理换机。

## Baseline 进入条件

只有包含本 readiness、预注册、R3 case、Git safe-directory 修复和专项测试的干净提交，
才能创建并推送：

```text
gate-7a-pre-run-r3-v1
gate-7c-langgraph-pre-run-r3-v1
```

执行前不创建任何 R3 consumed tag。Gate 7A 成功前不启动 Gate 7C。
