# PROFILE_ISSUE_CONTEXT_PACKET_V2

## 当前职责

只修改：

```text
src/vega/models.py
src/vega/project_context.py
```

本切片不负责检测问题，只定义最小合同并把另一个切片生成的问题码呈现在稳定项目上下文中。

## `ProjectProfile` 当前字段位置

`src/vega/models.py` 中 `ProjectProfile` 已包含：

```text
repo_name
repo_path
tech_stack
package_managers
test_commands
lint_commands
entrypoints
script_entrypoints
key_directories
config_files
agents_files
memory_hit_count
```

在这个模型中新增：

```text
profile_issues: list[str] = Field(default_factory=list)
```

不要新增 issue class、枚举、ledger、receipt 或额外持久化模型。

## 项目上下文当前结构

`render_project_context(...)` 的“项目画像”已经输出项目、路径、技术栈、包管理器、入口文件、
关键目录和配置文件，之后进入“推荐验证命令”。

在项目画像内增加一个最小“画像问题”呈现：

- 空列表时可以不增加噪声，或明确显示“未发现”；
- 非空时必须包含原始稳定问题码；
- `node_lockfile_conflict` 的解释必须包含“多个 Node lockfile”；
- `node_package_manager_invalid` 的解释必须包含“packageManager 声明无效”；
- 未知问题码应安全回退为原始码，不抛异常。

## 冻结问题码

```text
node_lockfile_conflict
node_package_manager_invalid
```

## 允许的窄补充读取

本切片不需要读取其他生产文件。不要搜索或读取 `tests/`、`eval/`、历史结果或 Node 检测实现。

## 兼容边界

- `ProjectProfile(...)` 的既有调用方不传 `profile_issues` 时必须继续工作；
- `render_project_context(...)` 的函数签名不变；
- 不修改项目验证职责边界、AGENTS.md 或 Memory 呈现；
- 只增加本任务需要的字段和文本，不顺手重构上下文渲染。
