# DV-B05 重跑 V2 Provider Profile 修订

> 修订日期：2026-07-30
>
> 修订发生在正式 Provider 调用之前；Worker、Reviewer 均未启动。

V2 调用前门禁发现，当前 Codex `model_provider` 名称已从上一轮的 `ciii` 变为 `sub2api`。
Provider origin 的脱敏 SHA-256、Responses 协议、模型目录和执行 profile 均未改变，安全设置
仍满足：

- `project_root_markers=[]`；
- `approval_policy=never`；
- workspace 网络关闭；
- hooks、Memory、Goal、浏览、插件和 Multi-Agent 全部关闭。

Owner 已明确说明当前 Provider 恢复稳定，因此本实验版本不要求恢复旧 provider 名称，而是
在调用前冻结当前 profile：

```text
model_provider: sub2api
provider_origin_sha256: 6d04c9e03d12fcd92ccf96ef5b973056f0c28d35d37d8668851b4d8390266909
profile_fingerprint: 4a7e838f4d6e027eb240a4d3fe3a0db53ac1caa430ff7cd5106a3b81c9d07cb0
execution_profile_sha256: c1158162ed8bf0ed8249d09cc5e5550d376c95eaccde9af2758307f2cd6cf110
wire_api: responses
```

Native Worker、Native Reviewer 以及未来可能授权的 Vega treatment 必须匹配该 fingerprint。
任何后续配置变化都重新触发 fail-closed，不得在同一次正式调用中临时切换 Provider。
