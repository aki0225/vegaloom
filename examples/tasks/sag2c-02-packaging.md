# SAG2C-02：packaging `Requirement` 哈希完整路径

> 用途：Supervisor Agent Gate 2C R2 的受控完整路径案例。
>
> 本文件用于跨机器重建任务输入，不包含公开修复提交、修复 Diff 或旧 Worker 对话。

## 用户目标

修复两个相等的 `Requirement` 对象可能产生不同哈希的问题，并补充回归测试。

## 已确认事实

- 上游基线为 `b34d12acb28c9ad3a6b0b3cc82f03a4b0b98c8c0`。
- 准备提交只增加受控 `.vega.yaml`，不包含目标修复。
- `Requirement("foo==1.0.0")` 与 `Requirement("foo==1.0.0.0")`
  比较相等，但哈希不同，放入集合后保留两个元素。
- 干净基线使用目标 `src` 的完整 `tests/test_requirements.py` 为 `5307 passed`。
- 正式 Worker 不接收公开修复提交、修复 Diff、SAG2C-01 的 Worker Diff 或旧 Worker 对话。

## 允许范围

- `src/packaging/requirements.py`
- `tests/test_requirements.py`
- `CHANGELOG.rst`

禁止修改项目配置、依赖、生成文件和上述范围之外的路径。

## 验证合同

```powershell
python -B -c "import sys; sys.path.insert(0, 'src'); from packaging.requirements import Requirement; a = Requirement('foo==1.0.0'); b = Requirement('foo==1.0.0.0'); assert a == b; assert hash(a) == hash(b); assert len({a, b}) == 1"
python -B -c "import sys; sys.path.insert(0, 'src'); import pytest; raise SystemExit(pytest.main(['-q', '-p', 'no:cacheprovider', 'tests/test_requirements.py']))"
ruff check --no-cache src/packaging/requirements.py tests/test_requirements.py
git diff --check
```

第二条命令必须在导入 pytest 前把目标 `src` 放入 `sys.path`。仅使用
`python -m pytest -o pythonpath=src` 会让 pytest 启动依赖提前导入虚拟环境中的
`packaging`，不能作为本案例的受信验证。

## 运行边界

1. 只允许一个未完成 Work Item；
2. 自动重试为 0；
3. 仅当确定性 Supervisor Decision 为 `repair` 时，允许一次同 child repair；
4. `replan`、`human`、`needs_human` 或任一门禁缺失均不通过本 Gate；
5. 不自动 commit、push、release 或写入长期 Memory。
