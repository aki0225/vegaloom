# 任务：补齐 Node Project Profile 的 script 与问题码语义

Vega 当前会在没有真实 `test` / `lint` script 时仍推荐 Node 命令；lockfile 冲突与非法
`packageManager` 又都会退化成“未选择 manager”，导致项目上下文无法解释原因。

请做最小修改，满足：

1. `ProjectProfile` 新增 `profile_issues: list[str]`，默认空列表；
2. 只有 `package.json.scripts.test` 是非空字符串时才推荐 Node test 命令；
3. 只有 `package.json.scripts.lint` 是非空字符串时才推荐 Node lint 命令；
4. 无有效显式 manager 且存在多个 Node lockfile 时记录 `node_lockfile_conflict`；
5. `packageManager` 存在但类型非法或 manager 不受支持时记录
   `node_package_manager_invalid`；
6. 有效显式 manager 继续优先于多个陈旧 lockfile；
7. `.vega.yaml` 显式 verification 继续覆盖自动推荐命令；
8. `tracked_only=True` 时 manager 与 scripts 必须来自同一固定 Git revision；
9. `render_project_context(...)` 在有问题码时呈现具体码和简短中文解释。

执行边界：

- 只修改当前分配切片列出的允许写路径；
- 先使用随 prompt 提供的窄上下文，不执行整仓搜索；
- 只按上下文包列出的文件做必要补充读取；
- 不修改测试、配置、CLI、Runtime、Reviewer、readiness 或正式 MA-2B 输入；
- 不新增通用诊断框架、issue class、receipt、ledger 或兼容性死代码；
- 不 commit、push、release、联网、调用子代理或写长期 Memory。
