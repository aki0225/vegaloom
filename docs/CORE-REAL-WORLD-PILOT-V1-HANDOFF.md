# Core Real-World Pilot v1 接力说明

> 日期：2026-07-28
>
> 分支：`main`
>
> 远端续做入口：`origin/main`

## 当前结论

CRWP-V1 的预注册、两次 Amendment、四个控制脚本和执行登记草案已经整理并可从远端接续。
正式 Worker **没有启动**，三个 Case 也没有产生 Reviewer 或 Finish 结果。

当前有两个独立阻断，不能只修其中一个：

1. 控制 oracle 登记审查发现三个合同覆盖缺口；
2. Vega Runtime 无法为带完整依赖目录的目标仓库建立可信 workspace baseline。

Runtime 阻断：

- Dormice 的 ignored 枚举超过 Runtime 固定的 30 秒 Git 读取上限；
- Sequelize 与 OpenStates 的 ignored 文件数超过 4096 个元数据条目预算；
- Dormice 的 `subprocess.TimeoutExpired` 还会直接向上传播，可能留下
  `running / current_step=worker` 的半完成 run。

控制 oracle 阻断：

- Dormice 默认 timeout 场景还要校验 Authorization、name 和 command；
- Sequelize 还要加入真实 SQL 行注释与块注释负对照；
- OpenStates 还要证明首次幂等运行真实生成预期 division/jurisdiction YAML 和 `ocdid`。

因此不能靠删除依赖、提高条目上限、沿用当前 oracle 哈希或放宽 fail-closed 语义继续实验。

## 本次接力提交包含

- `docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md`
- `docs/CORE-REAL-WORLD-PILOT-V1-RUN-REGISTRATION.md`
- `eval/real-world-runs.md` 中 2026-07-28 的追加记录
- `scripts/pilot/crwp-v1/` 下四个控制文件

`.local-validation/` 和 `.tmp/` 仍是本机 ignored 证据，不会推送到公开仓库。它们包含目标
副本、原始命令输出和本机路径信息，不能直接加入 Git。后续正式运行本来就必须从冻结 SHA
重新创建三个目标副本，因此晚间续做不依赖复制这些本机目录。

## 验证现状

已通过：

```powershell
python -m compileall src scripts/check_repository_hygiene.py
python scripts/check_repository_hygiene.py --base-ref origin/main
ruff check src tests scripts/check_repository_hygiene.py
ruff check scripts/pilot/crwp-v1
node --check scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs
node --check scripts/pilot/crwp-v1/ignore-native-drivers.cjs
git diff --check
```

三个当前版本 oracle 都连续复现两次，非 oracle 命令全部通过；但 oracle 代码审查发现上述
三个覆盖缺口，因此双次结果只属于待修订控制基线，不属于最终冻结证据。

`python -m pytest` 尚无可信全量终态。本机为 Python 3.14.3 + pytest 9.0.2；仓库 CI 使用
Python 3.11/3.12。定向诊断确认 `tests/test_assurance_verification_semantics.py` 是重型集成
测试集合，共 18 个 node，单文件预计需要 40 至 50 分钟；此前 900 秒停止不是
cacheprovider 挂死。不要把部分节点结果写成全量通过。

## 晚间续做

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
git status -sb
Get-Content docs/CORE-REAL-WORLD-PILOT-V1-HANDOFF.md
```

先补控制合同，不启动正式 Worker：

1. 修复三个 oracle 覆盖缺口；
2. 对每个变更后的 oracle 运行语法/静态检查和双次缺陷基线；
3. 更新控制文件哈希、执行登记、本地机器证据和 Control Amendment。

然后做 Vega 主线修复：

1. 为 `node_modules`、`.venv` 等大型依赖目录定义有界、可解释的 ignored inventory。
2. 保留对高价值 ignored 文件新增、修改和删除的检测能力。
3. 将 ignored 枚举 timeout 转成结构化、可恢复的 `needs_human` 终态。
4. 补 Node/Yarn/Python 大依赖目录回归测试，证明 worker 前后使用同一套 workspace 语义。
5. 修复通过后追加 Runtime Amendment，再从三个冻结 SHA 重建目标副本。

OpenStates 的生产文件路径会命中 `migration` 高风险门禁，后续即使 workspace 阻断解除，也应
按当前 fail-closed 语义转人工，不能为了让三个样本都进入 Reviewer 而放宽门禁。

## 不要做

- 不复用当前办公室机器上的目标副本作为正式运行输入；
- 不把 `config check` 或控制基线写成 Pilot 已执行；
- 不提交 `.local-validation/`、`.tmp/`、`.env`、数据库、Office 文件或本机绝对路径；
- 不自动 commit、push、release 或启动正式 Worker。
