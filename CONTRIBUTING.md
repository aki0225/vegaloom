# 为 Vega 贡献

## 开发环境

- Python 3.11+
- 使用可隔离的虚拟环境

```bash
python -m pip install -e ".[dev]"
```

## 运行验证

```bash
python -m compileall -q src scripts
python scripts/check_repository_hygiene.py --base-ref origin/main
python scripts/check_architecture_growth.py --base-ref origin/main
python -m pytest
ruff check src tests scripts
git diff --check
```

开发时先运行最接近改动的完整测试文件，再按 `tests/AGENTS.md` 扩大到 Core、Supervisor、
Security 或 Experimental 职责目录。超时或被中断的命令不能作为通过证据。

## 提交规范

- Git 提交信息使用中文。
- 不提交 `runs/`、`memory/`、`.tmp/` 或 `.local-validation/`。
- 不提交真实 Token、provider 原始输出、run artifact 或 workspace fingerprint。
- 变更核心逻辑时同步补充自动化测试。

## 安全问题

报告漏洞前，请先阅读 `SECURITY.md`。
