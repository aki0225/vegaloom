# Phase 0：实验基线

- 冻结标签：`v0.1.0`
- 基线提交：`<source-phase-0-baseline>`（公开标签：`v0.1.0`）
- 实验分支：`experiment/selective-memory`
- Python：`3.14.3`
- Pydantic：`2.12.5`
- 实验启动日期：`2026-07-13`

## 基线证据

`v0.1.0` 冻结前已完成：

- `python -m compileall src`
- `ruff check src tests`
- `git diff --check`
- 主线收口验证共收集 `332` 个测试节点；全量首轮暴露 3 个回归，修复后按测试文件和
  参数化 node id 分片复跑覆盖全部节点并通过
- `python scripts/dogfood_eval.py --runner none --workspace .tmp/dogfood-p0-rerun`
  得到 `8/8`

独立 Sol reviewer 曾启动但因耗时过长被人工停止，因此不把它列为基线已完成证据。

实验分支从该标签建立。随后只快进包含主线 README 瘦身提交；README 变更不修改 runtime
行为、成功条件、退出码或 artifacts。

## 实验隔离

Phase 1–2 的实现仅位于：

```text
eval/selective_memory/
tests/experimental/selective_memory/
docs/experiments/selective-memory/
```

不修改 `src/vega/`，不接入真实运行链。
