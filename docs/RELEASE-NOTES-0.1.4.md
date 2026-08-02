# Vega v0.1.4 发布说明

v0.1.4 是 v0.1.x 的可信执行维护版本，不增加新的默认 Runtime、Agent 角色或外部依赖。

## 主要修复

- 外部 worker、reviewer 和 verification execution 显式绑定可信根目录；创建 lease、刷新
  heartbeat 和持久化输出前都会拒绝符号链接、junction 或 reparse point 改道。
- Windows `cmd.exe` 配置预检拒绝双引号外的 POSIX 单引号分组和单管道，仍允许
  `python -c "print('ok')"` 这类双引号内的 Python 字符串。
- `{{vega_verification_temp}}` 的命令级叶目录改为独占创建；目录已存在或被链接占用时，
  不启动验证命令，也不清理预置内容。
- 文档明确 auto 模式下任何新增未跟踪文件都会交还人工，`budget.max_new_files` 不构成
  reviewer 读取或放行未跟踪内容的授权。
- PR CI 将完整测试集中在 Python 3.12 四个均衡分片，Python 3.11 保留编译与节点收集，
  Windows 只重复平台专项；main、release 和手工触发工作流仍执行 Python 3.11 全量测试。

## 不变边界

- Vega 不自动 commit、push、release、删除目标文件或写入长期 Memory。
- ignored 依赖目录仍采用有界清单语义；本版本不把 `.venv`、`node_modules` 等折叠目录
  简单判为失败。
- reviewer 与 worker 保持会话上下文隔离，但不宣称容器或操作系统级隔离。
