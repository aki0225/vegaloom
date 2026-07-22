# Gate 4.5 R6 Readiness

> 复审日期：`2026-07-17（星期五）`
>
> R5 历史结论：`partial-pass`，保持冻结
>
> R6 确定性准备：`ready to freeze pre-registration`
>
> R6 真实执行：`not started`
>
> Gate 5：`not approved`

---

## 1. 结论

R5 没有暴露新的 provider、LangGraph recovery 或安全协议问题，而是让三个独立 reviewer
一致发现了同一个真实需求缺口：

```text
非 ASCII 分隔符在 ASCII ignore 前被删除
-> 相邻单词错误拼接
-> 现有 unittest 通过
-> README 一般性要求未满足
```

本轮没有弱化 reviewer，也没有把 R5 改判为通过，而是把该 finding 转成确定性 fixture
回归。当前状态：

```text
R6 deterministic readiness = ready
R6 real preflight = not started
R6 business cases = not started
Gate 4.5 = partial-pass by frozen R5 evidence
Gate 5 = not approved
real provider calls in this readiness phase = 0
```

## 2. R5 finding 的证据

R5 三个 worker 都采用了等价顺序：

```python
normalized = unicodedata.normalize("NFKD", value)
ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
```

该实现对重音字母有效，但会得到：

```text
normalize_slug("foo—bar") = "foobar"
normalize_slug("foo，bar") = "foobar"
normalize_slug("foo💥bar") = "foobar"
```

README 的冻结要求是：

```text
非字母数字字符作为分隔符
```

因此 reviewer 的 `request_changes` 成立。R5 的三个 verdict 和 raw evidence 保持冻结。

## 3. Strengthened fixture

`TEST_SOURCE` 新增：

```python
def test_preserves_non_ascii_separator_boundaries(self) -> None:
    self.assertEqual(
        normalize_slug("café—déjà💥vu"),
        "cafe-deja-vu",
    )
    self.assertEqual(normalize_slug("foo，bar"), "foo-bar")
```

这两个断言覆盖：

- Unicode 重音字母仍然转为 ASCII；
- Unicode 破折号保留词边界；
- emoji 保留词边界；
- 全角标点保留词边界；
- 连续非 ASCII 分隔符继续折叠为单个 `-`。

README、任务目标、只允许修改的文件、依赖限制、change budget 和 reviewer evidence 选择规则
均未改变。

## 4. Fake reference 实现

fake Runner 的 `SOLVED_SLUGIFY` 不再先整体执行 `ascii(ignore)`，而是：

1. 先做 `NFKD`；
2. 保留 ASCII 字符；
3. 删除 Unicode combining mark；
4. 将其他非 ASCII 字符转换为分隔边界；
5. 最后按 `[a-z0-9]+` 生成 slug token。

该实现只用于 deterministic fake dogfood，不会发送给真实 worker，也不会进入真实 fixture
源码。真实 R6 worker 仍从 `NotImplementedError` 基线独立完成任务。

## 5. 回归证明

新增 harness 测试：

```text
test_fixture_rejects_r5_unicode_separator_regression
```

该测试执行两步：

1. 把 R5 三个 worker 的等价缺陷实现写入新 fixture，确认新增 unittest 明确失败；
2. 写入更新后的 fake reference 实现，确认同一完整 unittest suite 通过。

结果：

```text
focused regression = 1 passed
full Gate 4.5 harness = 48 passed
```

这证明新用例既能拒绝 R5 finding，又不是不可满足的测试。

## 6. 完整 fake Core Dogfood

```text
session = fake-core-r6-unicode-readiness-20260717
schema_version = 5
linear-low = passed
graph-low = passed
graph-crash-hitl = passed
conclusion = pass
elapsed = 113.828 seconds
```

三个 Case 均满足：

```text
state status = success
worker start count = 1
worker execution count = 1
reviewer execution count = 1
verification = passed
artifact integrity = true
evidence freshness = true
```

`graph-crash-hitl` 继续满足：

```text
fault triggered = true
decision count = 1
pending count = 1
consumption count = 1
graph state valid = true
checkpoint manifest valid = true
run status consumable = true
```

因此 strengthened fixture 没有破坏 Linear/Graph 语义、recovery、HITL 或 reviewer 证据链。

## 7. 变量隔离

后续真实 R6 除 fixture unittest 增加已确认的 Unicode separator 回归外，以下身份必须与 R5
保持一致：

```text
auth mode = api_key
config mode = isolated_provider
provider = sandboxproxy
provider base URL = http://127.0.0.1:18080/v1
provider descriptor SHA-256 =
  dfbc5ee355e628d747bcbcb9e64a26f5ae9be4bab135c84c151397e364898f65
model = sandbox-model
worker reasoning = high
reviewer reasoning = high
windows sandbox session override = elevated
automatic retries = 0
```

不得通过降低 reviewer 标准、删除 README 要求、扩大文件范围、增加 worker iteration 或切换
模型来解除 R5 的质量失败。

## 8. 仍未关闭的风险

1. 尚未证明真实 worker 会根据 strengthened fixture 形成完整正确实现；
2. 尚未证明真实 reviewer 会批准该实现；
3. provider、认证和 model 虽在 R5 可用，R6 执行时仍需重新 preflight；
4. fake dogfood 不能替代真实模型质量；
5. Windows abrupt process exit 残余风险仍未关闭；
6. Gate 5 三路 reviewer 尚未实现。

## 9. R6 准入顺序

下一步只能按以下顺序推进：

1. 完成静态检查并提交、推送本轮 fixture 与 readiness；
2. 以该干净实现提交的完整 SHA 冻结独立 R6 pre-registration；
3. 使用与 R5 相同的 provider/model/auth/sandbox 身份；
4. 使用全新 session、fixture、业务 run 和调用预算；
5. 只允许一个内置 preflight、3 个 worker 和 3 个 reviewer；
6. 任一 provider 或安全断言失败后停止，不自动重试；
7. 三个真实 Case 全部 `passed` 且所有安全不变量成立，Gate 4.5 才能改判 `pass`；
8. 只有 Gate 4.5 `pass`，才允许进入 Gate 5。

## 10. Readiness 判定

```text
R5 reviewer finding = confirmed
Unicode separator deterministic regression = ready
strengthened fake reference = ready
full fake Core Dogfood = pass
R6 pre-registration = approved to prepare
R6 real execution = not approved by this document
Gate 5 = not approved
```
