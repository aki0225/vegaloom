# Gate 0 独立复审记录

> 状态：`gate-0-pass`
>
> 日期：2026-07-15
>
> 分支：`experiment/langgraph-comparison`
>
> Gate 0 开始前 HEAD：`private-gate-0-contract-redacted`
>
> 代码实验基线：`private-experiment-base-redacted`

---

## 1. 复审方法

复审由不读取主会话完整上下文的独立只读 reviewer 执行。reviewer 只读取 Gate 0 契约，
必要时抽查当前源码验证文档声明，不修改文件。

Gate 0 通过条件：

- 无未关闭 Blocker；
- 无未关闭 High；
- HEAD、代码实验基线和历史测试证据口径准确；
- 状态所有权、P0 分类、Gate 职责和 Demo 边界无双真相源；
- Gate 1 / Gate 2 的硬验收足以保护 linear 默认、旧 run 兼容和 optional dependency。

## 2. 第一次完成复审

第一次完成复审结论：

```text
Blocker: 0
High: 5
verdict: blocked
```

发现：

1. 主执行计划与 `STATE-OWNERSHIP.md` 重复定义不一致的 Graph state schema。
2. P0-2、P0-4 和 P0-5 在不同文档中没有唯一、稳定的预注册分类。
3. Core Demo 提前要求 Gate 5 才实现的三路 reviewer。
4. Gate 1 硬门槛没有明确保护默认 `linear` 和旧 run 缺少 engine 字段的兼容性。
5. Gate 2 硬门槛没有验证 LangGraph dependency 保持 optional。

因此 Gate 0 保持 `blocked`，未进入 Gate 1。

## 3. 修复记录

### 3.1 Graph state 唯一来源

- `STATE-OWNERSHIP.md` 第 5 节成为 Graph state schema 的唯一规范来源。
- 主执行计划删除重复 `VegaGraphState`，只引用状态所有权契约。

### 3.2 P0 / P1 分类唯一来源

- `EVAL-PROTOCOL.md` 第 4 节成为 crash window 分类的唯一规范来源。
- P0-2 固定为 workspace 已修改但 terminal execution 尚未持久化的未知副作用现场。
- P0-4 拆成非终态 `P0-4a` 与终态 `P0-4b`。
- P0-5 固定为 `safe_resume_decision`。
- terminal execution 已存在但 step result 缺失移入 P1-16a/P1-16b。
- 条件型 reviewer 恢复拆成 P1-6a/P1-6b，禁止运行后选择有利分类。

### 3.3 Demo 分层

- Gate 4.5 Core Demo 只使用现有单 reviewer。
- Gate 5.5 Reviewer Extension Demo 独立运行三路 reviewer、隔离 canary 和 reducer 排列测试。
- 两套 Demo 的步骤、时间轴、必需 artifact 和断言已拆开。

### 3.4 Gate 1 兼容硬门槛

- 新 run 未指定 engine 时必须默认 `linear`。
- 旧 `state.json` 缺少 engine 字段时必须按 `linear` 读取。
- 旧 run 的 status、continue、finish 和 recover 必须保持兼容。
- engine 不匹配必须在写入 run artifact 前拒绝。

### 3.5 Gate 2 optional dependency 硬门槛

- 未安装 LangGraph 的基础依赖环境必须可以导入并运行 linear 路径。
- 安装项目可选 LangGraph extra 后才验证 graph 模块和顺序图。

## 4. 当前结论

第二次独立只读复审结论：

```text
Blocker: 0
High: 2
verdict: blocked
```

新增发现：

1. 主执行计划仍保留过时 P1 矩阵，恢复契约没有显式拆开 P1-16a/P1-16b。
2. Gate 2 提前要求检查 Gate 3 才实现的持久化 checkpoint。

对应修复：

- 主执行计划删除 P1 重复矩阵，只引用 `EVAL-PROTOCOL.md` 第 4.3 节。
- 恢复判定表显式区分可安全补写与必须 `needs_human` 的 step-result 缺失现场。
- Gate 2 改为检查 Graph state 及其实际序列化输出。
- 持久化 checkpoint 内容与身份硬门槛移入 Gate 3。

第三次复审发现复审记录的解锁条件仍错误引用已经 blocked 的第二次复审。修正为第三次复审
后，同一独立 reviewer 完成最终闭环复核：

```text
Blocker: 0
High: 0
verdict: pass
```

最终明确结论：

```text
无未关闭 Blocker/High
Gate 0 = pass
Gate 1 = ready
```
