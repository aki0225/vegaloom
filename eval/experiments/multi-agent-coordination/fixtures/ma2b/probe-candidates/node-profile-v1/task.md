# 任务：补齐 Node Project Profile 的 script 与问题码语义

Vega 当前只要发现 `package.json` 和可选 package manager，就会推荐 Node test/lint 命令，
没有确认对应 script 是否真实存在；lockfile 冲突与非法 `packageManager` 又都会退化为
“未选择 manager”，导致项目上下文无法解释停止原因。

请做最小修改，满足以下行为：

1. `ProjectProfile` 新增 `profile_issues: list[str]`，默认空列表；
2. 只有 `package.json.scripts.test` 是非空字符串时才推荐对应 manager 的 test 命令；
3. 只有 `package.json.scripts.lint` 是非空字符串时才推荐对应 manager 的 lint 命令；
4. 无显式 manager 且存在多个 Node lockfile 时，停止选择 manager，并记录
   `node_lockfile_conflict`；
5. `packageManager` 字段存在但类型非法或 manager 不受支持时，停止选择 manager，并记录
   `node_package_manager_invalid`；
6. 有效显式 manager 继续优先于多个陈旧 lockfile；
7. `.vega.yaml` 显式 verification 继续覆盖自动推荐命令；
8. `tracked_only=True` 时，manager 和 scripts 必须读取同一固定 Git revision；
9. `render_project_context(...)` 在有问题码时呈现这些码。

边界：

- 只修改分配给你的允许写路径；
- 不修改测试、配置、CLI、Runtime、Reviewer、readiness 或正式 MA-2B 输入；
- 不新增通用诊断框架、issue class、receipt、ledger 或兼容性死代码；
- 不 commit、push、release、联网或写长期 Memory；
- 可以进行最小自检，但不要声称外部固定 verifier 已通过。
