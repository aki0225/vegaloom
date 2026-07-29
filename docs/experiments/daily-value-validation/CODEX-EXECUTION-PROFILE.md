# Codex 正式 Treatment 执行配置

状态：2026-07-29 起对后续 treatment 生效；不追溯修改
`DV-B04/native` 的基础设施失败，也不允许重跑该 treatment。

## 1. 解决的问题

`DV-B04/native` 为隔离 hooks、Memory、Goal 等本机变量使用了
`--ignore-user-config`。真实运行证明该开关还会移除本机自定义 Provider 路由，导致认证信息
被发送到不匹配的默认路由。

后续不增加新的 Runtime 或 Provider adapter，只使用 Codex 原生命名 profile 收紧实验
启动方式：

- 正常加载当前 Codex 配置，以保留 Provider 路由；
- `vega-daily-value-v1` profile 显式关闭 hooks、Memory、Goal、多 Agent、插件和浏览能力；
- 使用 `project_root_markers=[]`，避免 baseline-only workspace 向父目录寻找项目根；
- Worker 使用 `workspace-write` 且关闭 sandbox 网络；
- Reviewer 使用 `read-only`；
- Native 与 Vega 必须使用相同的 Provider profile fingerprint。

profile 模板位于：

```text
docs/experiments/daily-value-validation/codex-profile.example.toml
```

运行前把同样内容放入 `$CODEX_HOME/vega-daily-value-v1.config.toml`。该文件不包含 Provider
或凭据，只覆盖实验隔离设置；基础 `config.toml` 继续提供 Provider 路由。

全局 `AGENTS.md` 属于 Owner 的日常 Codex 环境，两个 treatment 必须一致继承。任务 prompt
中的明确盲测边界仍禁止联网、子代理和读取另一 treatment 产物。

## 2. 离线预检

Worker 调用前执行：

```powershell
python scripts/daily_value_codex_preflight.py `
  --model gpt-5.6-sol `
  --profile vega-daily-value-v1 `
  --reasoning-effort medium `
  --sandbox workspace-write `
  --output .local-validation/daily-value-v1/provider-worker-preflight.json
```

Reviewer 调用前执行：

```powershell
python scripts/daily_value_codex_preflight.py `
  --model gpt-5.6-sol `
  --profile vega-daily-value-v1 `
  --reasoning-effort medium `
  --sandbox read-only `
  --expected-profile-fingerprint <worker-profile-fingerprint> `
  --output .local-validation/daily-value-v1/provider-reviewer-preflight.json
```

预检只读取 `CODEX_HOME/config.toml`、`custom-models.json` 与命名 profile，不会调用
Provider，也不会输出真实 endpoint。输出必须满足：

- `status=ready`；
- 冻结模型存在于当前模型目录；
- Provider 使用 `responses` 协议；
- `exec_args` 不包含 `--ignore-user-config`；
- 命名 profile 关闭所有实验外 feature、workspace 网络和自动审批；
- Native 与 Vega、Worker 与 Reviewer 的 `profile_fingerprint` 一致。

`.vega.yaml` 的 worker 与 reviewer 都必须指定：

```yaml
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      profile: vega-daily-value-v1
      model: gpt-5.6-sol
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      profile: vega-daily-value-v1
      model: gpt-5.6-sol
      reasoning_effort: medium
      ephemeral: true
```

原生 treatment 使用同一组 `profile / model / reasoning / ephemeral / sandbox` 参数；只额外
使用 `--json` 保存 Native 事件流。

## 3. 结论边界

该预检只能证明启动命令没有再次丢失 Provider 路由，不能证明凭据、网络或 Provider 当前
健康。正式调用仍可能得到 `infrastructure_failure`，且发生后仍不得静默重跑。

不新增独立的模型健康请求，因为它会增加额外 Provider 调用并改变 V1 成本口径。只有新的
实验版本明确预注册后，才能加入共享的 Provider readiness probe。
