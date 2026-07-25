# Vega v0.1.3 发布说明

v0.1.3 是 v0.1.x 的维护发布。它不增加新的默认 Runtime 能力，不引入数据库、Web UI、
LangGraph 主线、多 Agent、自动提交、自动发布或长期 Memory 写入。

本版本把 v0.1.2 之后进入主线的公开实验、发布准备材料和文档边界整理成一个新的稳定基线。
Stage 2/3 实验代码仍然保持隔离，不接入默认成功语义。

## 主要变化

### 公开实验证据进入主线

- Stage 2 已包含两个 SQLite migration 个案：
  - `AV-STAGE2-001`：SQLite 危险/安全双生实验。
  - `AV-STAGE2-002`：`expand -> backfill -> contract` 顺序验证。
- Stage 3 已包含固定 SQLite 有界 DML/Backfill 个案：
  - 有界 scope。
  - row budget。
  - interruption / recovery。
  - idempotency。
  - 独立 SQL oracle 和 evidence binding。

这些实验只证明固定个案和工程门禁，不证明通用生产数据库安全、PostgreSQL/MySQL 行为、
并发写入、真实流量或 Runtime 自动执行能力。

### 发布准备材料

- 新增发布前检查清单：安装、构建、源码树外 wheel smoke、日常 assist loop smoke、CI 和对外表述边界。
- 新增 v0.1.2/v0.1.3 发布摘要，便于 GitHub Release、公开首页和面试展示。
- README 增加发布摘要和发布前检查入口。
- 文档进一步统一产品边界：Vega 是本地优先的 AI 编码工作流 harness，不是通用 Agent 框架或
  操作系统级 sandbox。

### 版本与包验证

- Python distribution、导入包和 CLI 版本更新为 `0.1.3`。
- CI 中的 wheel/sdist 安装验证同步检查 `vegaloom-0.1.3`。
- `vega list-loops` 继续验证安装包内的 `engineering-change` baseline loop 可发现。

## 兼容性影响

- `.vega.yaml` schema 仍为 `version: 1`。
- CLI 主入口不变。
- `vega.__version__` 从 `0.1.2` 更新为 `0.1.3`。
- 旧 run 仍可读取和复盘，但不会因为新版本文档而改变历史 run 的成功语义。
- Stage 2/3 脚本和测试是实验性证据，不是默认用户流程。

## 发布验证要求

创建 tag 前必须确认：

1. `python -m compileall src` 通过。
2. `ruff check src tests scripts` 通过。
3. `python -m pytest --collect-only -q` 收集合同通过。
4. wheel/sdist 可以构建。
5. 源码树外安装 wheel 后 `vega --version` 输出 `0.1.3`。
6. 源码树外安装 wheel 后 `vega list-loops` 能列出 `engineering-change`。
7. GitHub Actions 主线同一 commit 的 10 项任务全部成功。

## 不变边界

- 不自动 commit、push、release、部署或删除目标文件。
- 不自动接受或写入长期 Memory。
- reviewer 与 worker 保持会话上下文隔离。
- read-only reviewer 不等于容器或操作系统级隔离。
- 不提供生产数据库事务、跨机器锁或目标仓库全局写入锁。
- Stage 3 不代表通用 backfill 或生产数据库安全已经成立。
