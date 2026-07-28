# NODE_PROFILE_DETECTION_PACKET_V2

## 当前职责

只修改：

```text
src/vega/project_profile.py
```

目标是让 Node manager、scripts、命令和问题码都由同一次 `build_project_profile(...)` 调用中的
同一 revision 输入推导出来。

## 已知代码地图

`build_project_profile(...)` 已经：

1. 在 `tracked_only=True` 时解析一次 `resolved_revision`；
2. 用该 revision 读取 tracked file list；
3. 调用 `_detect_node_package_manager(...)`；
4. 再调用 `_detect_test_commands(...)` 与 `_detect_lint_commands(...)`；
5. 最后让 `.vega.yaml` 的显式 verification 覆盖自动命令。

现状缺口：

- `_detect_node_package_manager(...)` 只返回 manager 或 `None`，丢失“多 lockfile”与“非法声明”
  的区别；
- `_read_declared_node_package_manager(...)` 已经通过 `_read_project_file(...)` 支持固定 revision；
- `_detect_test_commands(...)` 与 `_detect_lint_commands(...)` 只看是否存在 `package.json`，
  没有检查真实 script；
- manager 和 scripts 不应分别从 HEAD 与工作树读取。

## 冻结行为

- 无 `packageManager` 且恰好一个 lockfile：使用该 manager；
- 无 `packageManager` 且多个 lockfile：manager 为 `None`，问题码为
  `node_lockfile_conflict`；
- `packageManager` 存在但不是字符串，或 manager 不是 `npm` / `pnpm` / `yarn`：
  manager 为 `None`，问题码为 `node_package_manager_invalid`；
- 有效显式 manager 优先于陈旧 lockfile；
- 没有 lockfile 且没有显式 manager 时，`package.json` 仍默认 `npm`；
- `scripts.test` / `scripts.lint` 只有在值为非空字符串时才算存在；
- test 命令保持既有映射：`npm test`、`pnpm test`、`yarn test`；
- lint 命令保持既有映射：`npm run lint`、`pnpm run lint`、`yarn lint`；
- 将问题码传给已由另一切片定义的 `ProjectProfile.profile_issues`；
- 显式 verification 的优先级不得改变。

## 允许的窄补充读取

只在确有必要时读取：

```text
src/vega/models.py
src/vega/project_config.py
src/vega/repository_identity.py
```

不要搜索或读取 `tests/`、`eval/`、历史结果、文档树或其他 Runtime 模块。

## 最小实现提示

可以让现有 Node 检测内部返回一个小型 tuple，或在同一文件内增加一个私有结果结构；不要发展
通用 issue 模型。优先复用 `_read_project_file(...)`，一次解析 `package.json` 后把 script
存在性传给命令检测函数，避免对固定 revision 和工作树进行两套读取。
