# Scripts 规则

- `check_repository_hygiene.py` 与 `check_architecture_growth.py` 是 CI 门禁，不是实验脚本；修改时
  必须覆盖通过、拒绝和无法验证三类结果。
- `plan_state.py` 拥有机器计划、追加式状态事件和 `../docs/CURRENT.md` 的生成合同；修改时必须
  覆盖计划依赖、事件转换、既有事件不可变和生成视图一致性。
- `build_showcase_data.py` 拥有 `../site/data/cases.json` 的生成合同；生成文件不得绕过生成器手改。
- `dogfood_eval.py`、`pilot/`、`rcb*` 和 `run_assurance_*` 属于真实验证或冻结实验，不得被默认
  Runtime 导入，也不得把历史失败改写成成功。
- 脚本产生的临时文件写入 `../.tmp/`，最终人工日志写入 `../.local-validation/`，正式 Vega
  Artifact 写入 `../runs/`；不得写到仓库父目录或相邻项目。
- 脚本不得记录凭据、Authorization header、真实本机绝对路径或未脱敏 Provider 输出。
- 修改脚本时运行该脚本的定向测试、`python -m compileall -q scripts`、`ruff check scripts` 和
  `git diff --check`；CI 门禁或生成器变化还要运行对应工作流使用的完整命令。
