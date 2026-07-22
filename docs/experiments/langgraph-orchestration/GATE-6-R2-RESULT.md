# Gate 6 R2 真实执行结果

> 状态：`fail / DLP false positive`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`

---

## 1. 结论

R2 成功启动了真实 Codex preflight，并取得了正确的 CLI、provider、model、reasoning
和 sandbox header，但 harness 的输出 DLP 检查把 synthetic fixture 的 `workdir` 路径
误判为真实项目路径，因而在 preflight 结束后将 R2 分类为 `fail`。

```text
baseline = gate-6-r2-pre-run-v1
baseline commit = private-gate-6-r2-launcher-fix-redacted
consumed tag = gate-6-r2-consumed-v1
phase = failed:preflight
provider sessions used = 1
runner invocations = 1
execution slots used = 1
tokens used = 11,322
Session A/B = not-started
handoff/context = not-created
automatic retries = 0
```

R2 没有发送 source chat、accepted memory 或真实业务数据。R2 不能改判为成功，也不能
重跑；R3 将使用新的 baseline 和 consumed tag。

---

## 2. Real preflight 事实

真实命令：

```text
python scripts/gate6_handoff_dogfood.py --runner real --confirm-real `
  --session gate6-r2-real-v1 `
  --output-root .local-validation/gate-6-r2 `
  --fixture-root .tmp/gate-6-r2
```

execution header：

```text
Codex CLI = 0.144.5
provider = sandboxproxy
model = sandbox-model
reasoning effort = high
sandbox = workspace-write [workdir, /tmp, $TMPDIR]
execution status = completed
returncode = 0
termination_unconfirmed = false
tokens used = 11,322
```

Codex 输出只包含：

- synthetic fixture 的 `workdir` 路径；
- 固定 provider/model/reasoning/sandbox header；
- preflight sentinel；
- token 计数。

扫描没有发现 source chat canary 或 accepted memory 内容。

---

## 3. 阻断根因

旧 DLP 逻辑直接在完整输出中查找 clean checkout 的
`PROJECT_ROOT` 字符串。Codex 的正常 header 会打印 synthetic fixture 的绝对
`workdir`，而该路径位于 clean checkout 内，因此被误判为真实项目路径。

这是 harness 的路径边界误报，不是 provider 数据泄露：

```text
允许：
clean checkout/.tmp/gate-6-r2/gate6-r2-real-v1/repo

禁止：
clean checkout 根目录本身
source chat 内容
accepted memory 内容
```

R3 会在 DLP 检查中只允许当前 synthetic fixture root/repo 路径，仍拒绝 clean checkout
根路径、source chat 内容和 accepted memory 内容。

---

## 4. 安全和停止线

- R2 consumed tag 已创建并推送到远端。
- 没有重试 R2，也没有切换 provider、model、auth 或 reasoning。
- Session A/B 没有启动。
- sensitive fixture lock 为 `windows-share-deny`。
- accepted memory 写入为 `0`。
- R2 证据和 tag 不得删除、覆盖或重用。

---

## 5. Canonical Evidence

```text
.tmp/gate-6-r2/clean-checkout-gate6-r2-real-v1/
  .local-validation/gate-6-r2/gate6-r2-real-v1/summary.json
  .local-validation/gate-6-r2/gate6-r2-real-v1/report.md
  .local-validation/gate-6-r2/gate6-r2-real-v1/executions/preflight/
    execution.json
    process-output.txt
```

SHA-256：

```text
summary.json =
347e5e491fcbb296230f945555d1fced387865cf1a48ac61637958e0b26d84cc

report.md =
fe2159a014c990c136f93e8f72a45eff571333785646313a8ddcf872bcc9950f

execution.json =
d15e733478184e45cb1799da3f8ec699361e5a1ff74651471f100e77544b8053

process-output.txt =
d9bc1068aa16d1bc75586bf7d000aae7268c6a2e0edfb1ed3d13226d1372e67b
```

> 注：`summary.json` 的第一行 hash 在本地记录中保持连续十六进制字符串；文档换行
> 仅为可读性，不改变 raw evidence。
