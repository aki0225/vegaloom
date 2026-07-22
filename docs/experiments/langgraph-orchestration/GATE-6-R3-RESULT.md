# Gate 6 R3 真实执行结果

> 状态：`fail / expected verification cache misclassified`
>
> 日期：`2026-07-19（星期日）`
>
> 时区：`Asia/Shanghai`
>
> 分支：`experiment/langgraph-comparison`

---

## 1. 结论

R3 已真实完成 preflight 和 Session A。Session A 按冻结 prompt 运行
`python -m pytest -q`，生成了 `src/__pycache__` 和 `tests/__pycache__`。Git guard 将
这些已知测试缓存误判为 worker 修改了 ignored 状态，因此在 Session A 后 fail-closed。

```text
baseline = gate-6-r3-pre-run-v1
baseline commit = private-gate-6-r3-output-hardening-redacted
consumed tag = gate-6-r3-consumed-v1
phase = failed:session-a
provider sessions used = 2
runner invocations = 2
execution slots used = 2
tokens used = 31,770
preflight = success
Session A = success
Session B = not-started
handoff/context = not-created
automatic retries = 0
```

R3 的 provider、身份、sandbox、prompt DLP 和 process termination 均在 Session A 前通过；
失败点是 harness 对正常 verification 副作用的错误分类。R3 不重跑，R4 使用新 baseline。

---

## 2. 真实执行事实

```text
preflight tokens = 11,309
Session A tokens = 20,461
preflight execution = completed / returncode=0
Session A execution = completed / returncode=0
termination_unconfirmed = false
sensitive fixture lock = windows-share-deny
source chat included = false
accepted memory writes = 0
real project data sent = false
```

Session A 的 worker 只修改了授权文件：

```text
src/labels.py
```

其余新增 ignored 文件为：

```text
src/__pycache__/__init__.cpython-312.pyc
src/__pycache__/labels.cpython-312.pyc
tests/__pycache__/test_labels.cpython-312-pytest-9.0.2.pyc
```

没有发现 `.cache/worker-output.txt`、source chat 内容、accepted memory 内容或其他业务
artifact 修改。

---

## 3. 阻断根因

R3 已将 `status_ignored` 收窄为 ignored 文件清单，但仍把所有 ignored 路径视为异常。
冻结 verification 命令本身会产生 Python bytecode cache，因此该规则与 worker prompt
冲突。

R4 将只过滤以下已知工具缓存路径：

```text
任意层级的 __pycache__/
任意层级的 .pytest_cache/
```

其他 ignored 路径仍然改变摘要并触发 fail-closed。

---

## 4. 安全和停止线

- R3 consumed tag 已创建并推送到远端。
- 没有重试 R3，也没有切换 provider、model、auth 或 reasoning。
- Session B 没有启动。
- provider 真实调用和 token 证据完整保留。
- R3 baseline/tag 不得删除、覆盖或重用。

---

## 5. Canonical Evidence

```text
.tmp/gate-6-r3/clean-checkout-gate6-r3-real-v1/
  .local-validation/gate-6-r3/gate6-r3-real-v1/summary.json
  .local-validation/gate-6-r3/gate6-r3-real-v1/report.md
  .local-validation/gate-6-r3/gate6-r3-real-v1/executions/preflight/
  .local-validation/gate-6-r3/gate6-r3-real-v1/executions/session-a/
```

SHA-256：

```text
summary.json =
bf33f540fb08858e32b349b2ee6db90a1a35f82b1ef8b5a01fff0167cb806091

report.md =
ff334d5c71f8935941fc36e7b838be0bc2b116dbb1e9de8fd097b8d1d65d06e0

preflight execution.json =
a53042e91ed676f237093e7498f7b7fe3329499394e89ece739f8afd69d63e59

Session A execution.json =
378f58f23f138cee722cbb2cfa569ba8d3bc1915a4f87bb2a9e40979a15dc683
```
