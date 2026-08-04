# Core Real-World Pilot v1 执行登记

> Pilot ID：`CRWP-V1`
>
> 状态：`registration-review-blocked / preflight-blocked / worker-not-started`
>
> 登记日期：2026-07-28
>
> 预注册提交：`6f6878a244d7deaf495d656f1371cce06f770b2a`
>
> Amendment 提交：`d372e88`、`30c89e5`
>
> Vega Runtime 代码基线：`eb18d6ffd96436a0ce5f29d43fbda3c105c76464`
>
> 控制输入登记提交：以首次同时包含本文和 `scripts/pilot/crwp-v1/` 四个控制文件的提交为准

## 一、当前结论

三个目标副本、最终 `.vega.yaml`、控制 oracle 和依赖环境已经 materialize。控制端已经完成：

- 三个目标各自只用一个本地准备提交加入 `.vega.yaml`；
- 准备提交的 parent 分别等于预注册的冻结上游修订；
- 三个目标均已移除 Git remote，tracked、index 和未跟踪状态为空；
- 三次 `config check --json` 均为 `passed`，命令数分别为 `6 / 5 / 5`；
- 最终配置中的全部非 oracle 命令均通过；
- 三个 oracle 都在独立进程中连续两次以 `1` 退出，决定性输出字节完全一致；
- 最终命令均通过当前 Runtime 的 Windows 原生命令行构造执行，`shell_kind=cmd`，
  每条命令的 owned execution 均进入终态，`termination_unconfirmed=false`。

登记审查随后发现三个控制 oracle 仍有合同覆盖缺口：

- Dormice 默认 timeout 分支未校验 Authorization、name 和 command；
- Sequelize 未建立真实 `-- AUTOINCREMENT` / `/* AUTOINCREMENT */` SQL 注释负对照；
- OpenStates 幂等场景未断言首次运行真实生成预期类型和 `ocdid` 的 YAML。

因此当前 oracle 双次结果只能证明现有检查稳定复现目标行为，不能视为最终控制合同已经冻结。
修订三个 oracle 后必须更新哈希并重新执行各自双次基线。

但正式 worker **没有启动**。当前 Runtime 无法为三个已安装依赖的普通目标仓库建立可信的
ignored 工作区基线：

- Dormice 的 ignored 枚举在 Runtime 固定的 30 秒 Git 读取上限内超时；
- Sequelize 与 OpenStates 的 ignored 文件数超过 Runtime 的 4096 个元数据条目预算，
  `snapshot_workspace().capture_complete=false`。

这已经命中预注册中的运行前停止条件。控制端因此在创建正式 run 前停止，没有让 Dormice 的
`TimeoutExpired` 进入 auto loop。`config check` 和目标命令通过不能覆盖工作区基线不可信，
因此本登记不包含 run ID、worker 输出、reviewer verdict 或 Finish 结论，也不得表述为
“Pilot 已执行失败”或“模型没有修好”。当前准确状态是：**配置解析与非 oracle 命令已验证；
控制 oracle 仍待补齐合同，可信 workspace baseline 未建立，正式执行未启动。**

## 二、执行环境与角色参数

### 2.1 主机与工具

| 项目 | 登记值 |
|---|---|
| 操作系统 | Windows 10 Enterprise G 21H2，build `19044.1415` |
| 架构 | `AMD64` |
| Git | `2.33.0.windows.2` |
| Codex CLI | `0.144.6` |
| Vega 源码版本 | `0.1.3` |
| Vega 控制端 Python | `3.14.3` |
| Node.js | `22.22.0` |
| Dormice pnpm | `10.30.2` |
| Sequelize Yarn | `4.13.0` |
| OpenStates Python | `3.12.13` |
| uv | `0.10.10` |

全局 `vega.exe` 指向旧版 `0.1.0`，不属于本实验 Runtime。所有 Vega 只读配置检查和控制端
调用均通过当前仓库 `src/` 执行。

### 2.2 Worker 与 Reviewer

三个 Case 使用相同角色配置：

| 角色 | Runner | Model | Reasoning | Session | Sandbox |
|---|---|---|---|---|---|
| Worker | `codex-exec` | `gpt-5.4` | `medium` | `ephemeral` | `workspace-write` |
| Reviewer | `codex-exec` | `gpt-5.4` | `high` | `ephemeral` | `read-only` |

控制端对 `gpt-5.4` 的短探测曾返回 `READY`，Codex CLI 同时提示本地缺少该模型 metadata，
将使用 fallback metadata。另一次 `gpt-5.3-codex` 探测约 185 秒超时，未被静默替换为正式
模型，也未留下已知残余进程。上述探测没有独立原始日志文件，因此只作为模型选择说明，
不作为 Provider 可用性的持续证明；正式运行前仍需重新探测。

## 三、控制输入与合同哈希

### 3.1 控制目录

- `CRWP_CONTROL_ROOT` 仓库相对路径：`scripts/pilot/crwp-v1`

解析后绝对路径及其哈希只保存在 ignored 本地证据中，公开登记不保存本机路径指纹。当前
Runtime 没有为 worker、reviewer 和 verification 分别过滤环境变量，因此该目录只属于输入
分离和事后完整性边界，不构成操作系统级机密目录。

### 3.2 文件 SHA-256

| 文件 | SHA-256 |
|---|---|
| `scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py` | `75b7c31523a881cff66622d9a51cef99a32a916a3a6b8d44f42de941a80e4beb` |
| `scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs` | `67817feb79dc91ad700195c299c7351e4ec18723e4b4d4c5b4431799146d3225` |
| `scripts/pilot/crwp-v1/crwp-v1-03-stub-filename-oracle.py` | `31044c384b39d386c72be78f8505b86049dfb46c2733a1921028f45ab0baf3cb` |
| `scripts/pilot/crwp-v1/ignore-native-drivers.cjs` | `d168cf32755ccda649a13bf245873a0d7cad153c74d4792f3f7d963e62c28b65` |
| `docs/CORE-REAL-WORLD-PILOT-V1-PREREGISTRATION.md` | `cdbb2d48df5959630b786e6cb4d2c6e1e6cd2a22d6a4623f5128df55ca841a89` |
| `eval/real-world-runs.md` | `ceeba74220545a2b11fe0543f803c2e6aa2f2e93c9c3f1d3183480d97b6b51ec` |

OpenStates oracle 已在导入目标模块前把
`src.init_migration.generate_recursive` logger 的级别设为 `WARNING`。这只过滤目标模块的
正常 `DEBUG/INFO`，不吞掉 warning、error 或 Python 异常；两次最终 oracle 输出都是单一 JSON。

本表记录的是当前待修订控制文件哈希。第十二节列出的三个合同缺口修复后，必须更新本表和
本地机器登记，不能沿用当前哈希启动正式 Worker。

## 四、目标准备提交

| Case | 本地分支 | Upstream base | Prepared HEAD | Prepared tree | `.vega.yaml` SHA-256 |
|---|---|---|---|---|---|
| `CRWP-V1-01` | `pilot/crwp-v1-01-dormice-33` | `f26ba3748e79c7225f4aafb757c6f9f1f6b2d733` | `9bf20d0c955aab205278e00e01293ea7e12abea7` | `7735e8269afab4a26b2b7c8cf66e074961f8ce28` | `d4322d5ce2c9e86dad259bfcf4795dc70d548a81eb01d115d5f84cc40c2711a7` |
| `CRWP-V1-02` | `pilot/crwp-v1-02-sequelize-18265` | `f0cea95e38b4f2c9096267371ab305d08f7b8497` | `6a7bf0c5457914fd034fbf6dd0ec8f46f82e6da6` | `67f271bb1fbd2506fc556ecab4ea319b827b234f` | `844800d61f6dbd016357e796f3db5bb7f371b22c0cdc1e50ccf25d47e92b2024` |
| `CRWP-V1-03` | `issue-122-ancestor-stub-filename` | `6fbe7d6aed32c3b781490c8e4c5a737bdd6e4705` | `d5f89f526924a08c6f0c6b882a59cdde07763247` | `d85dd61273c754204a9e32a345874b8938d5845c` | `9ae1f19d5417928656a114ca66ed23a4613ef374d7a3f6a978bb75ab54634b8e` |

三个 prepared commit 的 parent 均与对应 Upstream base 完全相同，commit diff 都只包含
`.vega.yaml`。三个目标的 remote 数量均为 `0`，`git status -sb` 只有分支标题。

控制端另记录了目标绝对路径哈希、porcelain v2 状态哈希和 index flag 哈希。这些值保存在
ignored 本地证据中，不在公开文档暴露路径；它们是控制端清单，不替代 Vega 未能生成的可信
workspace fingerprint。

## 五、最终机器策略

### 5.1 `CRWP-V1-01`

```yaml
version: 1
verification:
  commands:
    - pnpm --filter @dormice/cli... build
    - pnpm --filter @dormice/cli test
    - pnpm --filter @dormice/cli typecheck
    - pnpm exec biome check packages/cli/src
    - python "%CRWP_CONTROL_ROOT%\crwp-v1-01-timeout-oracle.py" --repo .
    - git diff --check
  max_commands: 6
  timeout_seconds: 300
budget:
  max_changed_files: 2
  max_diff_lines: 350
  max_new_files: 0
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - packages/cli/src/main.ts
    - packages/cli/src/commands.test.ts
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      model: gpt-5.4
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      model: gpt-5.4
      reasoning_effort: high
      ephemeral: true
```

### 5.2 `CRWP-V1-02`

```yaml
version: 1
verification:
  commands:
    - set "NX_DAEMON=false" && corepack yarn build
    - set "NODE_OPTIONS=--require=%CRWP_CONTROL_ROOT%\ignore-native-drivers.cjs" && set "DIALECT=sqlite3" && corepack yarn workspace @sequelize/core mocha test/integration/query-interface/createTable.test.js test/integration/sequelize.test.js
    - corepack yarn eslint packages/sqlite3/src/query-interface.ts packages/core/test/integration/query-interface/createTable.test.js packages/core/test/integration/sequelize.test.js --report-unused-disable-directives
    - node "%CRWP_CONTROL_ROOT%\crwp-v1-02-sqlite-autoincrement-oracle.cjs" --repo .
    - git diff --check
  max_commands: 5
  timeout_seconds: 300
budget:
  max_changed_files: 3
  max_diff_lines: 600
  max_new_files: 0
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - packages/sqlite3/src/query-interface.ts
    - packages/core/test/integration/query-interface/createTable.test.js
    - packages/core/test/integration/sequelize.test.js
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      model: gpt-5.4
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      model: gpt-5.4
      reasoning_effort: high
      ephemeral: true
```

### 5.3 `CRWP-V1-03`

```yaml
version: 1
verification:
  commands:
    - uv run pytest tests/src/init_migration/test_generate_recursive.py -q
    - uv run ruff check src/init_migration/generate_recursive.py tests/src/init_migration/test_generate_recursive.py
    - uv run python "%CRWP_CONTROL_ROOT%\crwp-v1-03-stub-filename-oracle.py" --repo .
    - uv run pytest -m "not integration and not slow" -q
    - git diff --check
  max_commands: 5
  timeout_seconds: 300
budget:
  max_changed_files: 2
  max_diff_lines: 400
  max_new_files: 0
  forbid_new_dependencies: true
  forbid_large_generated_files: true
scope:
  allowed_paths:
    - src/init_migration/generate_recursive.py
    - tests/src/init_migration/test_generate_recursive.py
runner:
  worker: codex-exec
  reviewer: codex-exec
  codex_exec:
    worker:
      model: gpt-5.4
      reasoning_effort: medium
      ephemeral: true
    reviewer:
      model: gpt-5.4
      reasoning_effort: high
      ephemeral: true
```

三次 `config check` 均返回 `status=passed`、`issues=[]`。每项都满足：

```text
configured_command_count
== distinct_selected_command_count
== max_commands
```

`config check` 不执行命令，也不验证 oracle 哈希、`CRWP_CONTROL_ROOT`、模型可用性或命令结束后
是否残留后台进程。本节只能证明 Runtime 能解析策略，不能证明目标可运行。

## 六、依赖与项目规则哈希

### 6.1 依赖清单

| Case | 文件 | SHA-256 |
|---|---|---|
| `CRWP-V1-01` | `pnpm-lock.yaml` | `f4c3208f72bc388b14e766883a050ffe51bf4cd6a385473fdeedab38cba481a6` |
| `CRWP-V1-01` | `package.json` | `0d7a5f38ea761e394b43cd58f253a04bb325c990dcb484758a36b3dfad5e7cfe` |
| `CRWP-V1-02` | `yarn.lock` | `521c128c9ba08b91ff40d12ed6443f742c43784b05fe1fd347c233c6bd45926c` |
| `CRWP-V1-02` | `package.json` | `76771e9678f240f67cbfe5e81b6189e2f19e3d684af651bc0150708d080cd6cd` |
| `CRWP-V1-02` | `.yarnrc.yml` | `013aff27ac2a697fb9b89079fea97266d53742471f16c0ad50001c37d97197f2` |
| `CRWP-V1-03` | `uv.lock` | `8a5732317a4928b90f446a47caba5859ed92afd894cc14876f64244878b14811` |
| `CRWP-V1-03` | `pyproject.toml` | `6a47f93dff659f742187aced57dfe46f61d03e9dfc6839f6df55fad893988c44` |

Dormice 与 Sequelize 冻结修订的仓库根没有受跟踪的 `AGENTS.md`。OpenStates 已读取
`AGENTS.md`、catalog 以及本任务相关的 active 指令：

| 文件 | SHA-256 |
|---|---|
| `AGENTS.md` | `ed45348c0eccf0eddc49eda2d9e156f6ed54d4ebe4abd370ca1d36385cccc459` |
| `ai_tools/catalog.yaml` | `1cc017f3ac67b981924f65a72c626e4f42a62203d85fc8b8e143243c1079fbca` |
| `ai_tools/system/repo-agent-system.instruction.md` | `a2b73c9dd7d29c6b74736336e7ba57fa60aabc32a0822eae4e536df4021fa9d2` |
| `ai_tools/system/pre-commit-checks.instruction.md` | `182c887dc27bf6b03bbe1d52d1b9005f5bfcc4ff482d8885cc1c4818d08f3a6d` |
| `ai_tools/system/contributor-workflows.instruction.md` | `ba03e5a068846df24c64bd022454f3a79736ab60e4c38b82e3c70ce76701a233` |
| `ai_tools/tasks/feature-delivery.instruction.md` | `e67cc1eca3b571f2f49ea6c31dcd8e9e62a5d37bdd384b99a9a342b827a03585` |

## 七、依赖准备中的失败与更正

### 7.1 Dormice

执行：

```text
corepack pnpm install --frozen-lockfile
corepack pnpm --filter @dormice/cli... build
```

依赖安装和目标包构建完成。项目根 `pnpm` 在该仓库内根据 `packageManager` 解析为 `10.30.2`。

### 7.2 Sequelize

执行：

```text
corepack yarn install --immutable --mode=skip-build
```

初次 `corepack yarn build` 因 SQLite native binary 尚不存在而失败。随后一次
`corepack yarn rebuild sqlite3` 超过十分钟，并继续尝试本 Case 不使用的 `ibm_db`；控制端只
终止该次 owned 进程树。该过程已经产生可用的 SQLite binary：

```text
length = 1980416
sha256 = 5e1d1275e126c3fc584bcf5752fbf747bff89454bfcf8bc76c982b24e7815057
```

之后完整 workspace build 通过。上述 rebuild timeout 来自控制端交互记录，未保留独立原始
日志文件，因此只作为必须披露的 setup 事件，不把它包装成结构化验证证据。

第一次使用原命令 `corepack yarn build` 做最终基线诊断时，命令以 `0` 退出但留下 Nx daemon。
控制端执行 `corepack yarn nx reset` 后完成清理，并通过 Amendment 2 把最终命令改为：

```text
set "NX_DAEMON=false" && corepack yarn build
```

最终基线重新执行后，控制端交互扫描未发现目标相关残余进程；该历史扫描没有独立 artifact，
不能反推为结构化的“残余进程数为 `0`”证据。登记整理阶段另生成带时间和匹配规则的当前
进程审计，见第十一节。

### 7.3 OpenStates

执行：

```text
uv sync --frozen --all-extras
```

依赖准备完成；准备阶段定向 pytest 为 `21 passed`，定向 Ruff 通过。

## 八、最终基线执行

所有最终命令都由当前 Runtime 的 `build_verification_shell_command(..., "cmd")` 生成
Windows 原生命令行，再交给 `run_owned_process`。这避免了用普通 argv 列表调用 `cmd.exe`
时对嵌套双引号的错误转义。

### 8.1 `CRWP-V1-01`

| 命令序号 | 运行 | Exit | 秒 | 输出 SHA-256 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 66.895 | `e0631a26cee533eb9b98aeb4b7363f7be4d70f596df7d3de5d67fb890ade53b6` |
| 2 | 1 | 0 | 15.890 | `b0d5abf3103d7cd295b58313997cdff9b0a6d3b889c5bd2c43374af066778961` |
| 3 | 1 | 0 | 11.179 | `beb26139f96bcd659b4364837d4dcc198662e3a605ad66ec3a1fd828209bd36d` |
| 4 | 1 | 0 | 14.275 | `237a0e8c2cab7117db7ff68897f50d9ac235530ece0d6b1068d288602ce4858f` |
| 5 oracle | 1 | 1 | 34.387 | `6929af7b286572abbb975d6b4040ee7315d21b135ec8b6bfa0e43f9a67a344cb` |
| 5 oracle | 2 | 1 | 31.111 | `6929af7b286572abbb975d6b4040ee7315d21b135ec8b6bfa0e43f9a67a344cb` |
| 6 | 1 | 0 | 0.738 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

决定性基线：

- `not-a-number`、`10m`、`Infinity` 在建立连接前失败，但错误暴露内部 `delay`，没有指向
  timeout 参数；
- `0`、`-1`、`1.5` 被错误接受，并各建立一次连接；
- `1`、`60`、`1e2`、`0x10`、`+1`、`86401` 均按预期进入原路径；
- 未提供 timeout 时 SDK 默认值仍为 `300`。

两次 oracle 的完整输出字节相同。

### 8.2 `CRWP-V1-02`

| 命令序号 | 运行 | Exit | 秒 | 输出 SHA-256 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 170.399 | `cd2abe8dc0a16eeeb4f6043398da99edacbe336947364f3ae3ffd715b025c9e0` |
| 2 | 1 | 0 | 19.686 | `c098a63a3dbc5b71bd9c163c6672f22e1518f5acc73fcbeb19565463ed9ff3fb` |
| 3 | 1 | 0 | 19.543 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| 4 oracle | 1 | 1 | 4.705 | `438c8cc053fd2834403f73f5ee703de5fb1ab0ab8069202c03ae092cf3681cca` |
| 4 oracle | 2 | 1 | 3.847 | `438c8cc053fd2834403f73f5ee703de5fb1ab0ab8069202c03ae092cf3681cca` |
| 5 | 1 | 0 | 1.049 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

两次 oracle 均稳定得到：

```json
{
  "status": "failed",
  "before_has_autoincrement": true,
  "after_has_autoincrement": false,
  "second_alter_has_autoincrement": false,
  "describe_reports_autoincrement": false,
  "data_preserved_after_first_alter": true,
  "inserted_id_after_deleting_max": 2,
  "deleted_id_not_reused": false,
  "data_preserved_after_second_alter": true,
  "plain_pk_not_autoincrement": true,
  "literal_text_not_autoincrement": true,
  "dash_literal_preserves_later_autoincrement": false
}
```

### 8.3 `CRWP-V1-03`

| 命令序号 | 运行 | Exit | 秒 | 输出 SHA-256 |
|---:|---:|---:|---:|---|
| 1 | 1 | 0 | 8.739 | `6f1b64fb1d62ce9cb53de027dd44078c6632b1f49d0cf1916c8a8f359f570f63` |
| 2 | 1 | 0 | 4.719 | `82b3e6a6c090a57601d22943bd23fca9218d1031dbe5a7b754092f9a156b4f18` |
| 3 oracle | 1 | 1 | 4.841 | `34a9633e54a1a57827773487846fb508f46f641ea5478b62e6da56f7f62b34bf` |
| 3 oracle | 2 | 1 | 5.254 | `34a9633e54a1a57827773487846fb508f46f641ea5478b62e6da56f7f62b34bf` |
| 4 | 1 | 0 | 23.498 | `1d7de9b4cddcef5bc784bc9ca736e55857b15c239390b6ed2baa9b1137a5425a` |
| 5 | 1 | 0 | 1.284 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

决定性基线：

- Division 与 Jurisdiction writer 都连续返回
  `washington_county_stub.yaml`；
- 每个同目录双写场景最终都只有一个文件，第一份被覆盖；
- legacy 与 UUID 命名文件均能由 `stub_exists()` 按 YAML 内 `ocdid` 找到；
- `ensure_ancestor_stubs()` 第二次运行全部 skip，writer 调用数为 `0`，manifest 不变；
- 两次 oracle 输出字节完全相同。

### 8.4 控制端更正记录

第一次 Dormice 基线 runner 把 `cmd.exe` 当普通 argv 列表启动，嵌套双引号被错误保留，
oracle 路径因而以 `2` 退出。这是控制 harness 调用错误，不是 oracle 内部错误或目标行为。
该次输出保留在本地证据中，随后使用 Runtime 的原生命令行构造把全部六条命令重新执行；
本节只采用重新执行的最终结果。

第一次 Sequelize 最终基线使用原 `corepack yarn build` 命令，发现命令结束后仍有 Nx daemon。
该次结果同样只作为诊断，最终结果来自 Amendment 2 后重新执行的五条命令。

上述两次作废尝试已写入本地 `registration-data.json` 的 `control_corrections`。Dormice
作废尝试有完整 summary 和 execution artifact；Sequelize 的基线 summary 可复核，但当时
Nx daemon 的进程清单、`nx reset` 和 rebuild timeout 没有独立原始 artifact，登记中明确以
`null` 表示缺失，不把交互记录包装成结构化证据。

## 九、正式 Worker 的前置阻断

当前 Runtime 的关键边界：

- ignored 枚举使用 Git 读取固定 30 秒 timeout；
- ignored manifest 最多完整登记 4096 个元数据条目；
- auto loop 在 worker 前要求 `snapshot_workspace().capture_complete=true`；
- 该条件不满足时不得把后续 diff 归因给 worker。

最终目标现状：

| Case | Ignored 文件数 | Git 输出字节 | 控制端枚举秒 | Vega snapshot |
|---|---:|---:|---:|---|
| `CRWP-V1-01` | 407934 | 43086563 | 38.847 | `TimeoutExpired`，未生成 baseline |
| `CRWP-V1-02` | 65438 | 4130499 | 4.831 | `capture_complete=false` |
| `CRWP-V1-03` | 7152 | 433568 | 1.078 | `capture_complete=false` |

Dormice 的 Runtime 读取在 30 秒处超时；控制端放宽到 180 秒后，实际枚举耗时仍为
38.847 秒。Sequelize 与 OpenStates 虽能完成枚举，但都超过 4096 个条目的 manifest 上限，
因此 `ignored_manifest_complete=false`。

Dormice 还暴露出一个更严格的错误边界：`snapshot_workspace()` 只捕获 `RuntimeError`，
没有捕获 `subprocess.TimeoutExpired`。若直接启动当前 auto loop，状态会先写成
`running / current_step=worker`，随后在调用 worker runner 前抛出该异常；worker 不会启动，
但 run 可能停留在非终态并需要 recovery。此次是控制端在创建正式 run 前主动停止，不能把它
描述成 Runtime 已经生成了干净的 `needs_human` 终态。

这不是简单把 timeout 或条目上限调大就能可信解决的问题。无界读取 `node_modules`、`.venv`
和构建缓存会放大启动耗时、内存与证据体积；直接忽略所有 ignored 路径又会失去对 worker
修改构建产物、缓存、凭据文件或其他本地状态的检测。下一步应先在 Vega 主线为“大型依赖目录
与高价值 ignored 状态并存”定义有界、可解释的策略，并补回归测试，再创建新的运行登记提交。

在此之前不得：

- 启动任一正式 worker；
- 删除依赖目录后假装目标仍具备相同可执行环境；
- 只依据 `git status` 干净或 `config check` 通过宣称 workspace 可信；
- 把当前阻断记为模型失败、oracle 失败或 reviewer 拒绝。

### 9.1 控制 oracle 登记审查阻断

除 workspace baseline 外，当前控制端还有三个独立阻断：

1. Dormice 默认 timeout 场景需要同时校验请求路径、Authorization、name、command 和
   `timeoutSeconds=300`；
2. Sequelize 负对照需要真实 SQL 行注释和块注释，不能只用字符串字面量近似；
3. OpenStates 幂等场景需要证明首次运行真实写出预期 division/jurisdiction YAML，并校验
   对应 `ocdid`，再验证第二次不新增、不改写且 writer 调用为 `0`。

这些修复会改变控制文件哈希。完成后必须重新执行各自 oracle 两次并追加 Amendment；当前
双次输出不得直接升级为最终冻结证据。

### 9.2 OpenStates 的后续风险门禁

即使 ignored 基线问题修复，当前 `HIGH_RISK_PATH_KEYWORDS` 也会让
`src/init_migration/generate_recursive.py` 命中 `migration`。若 worker 修改该生产文件，
当前 risk gate 将给出 `human-review`，auto loop 会在隔离 reviewer 前停止。

这符合 fail-closed，但意味着 `CRWP-V1-03` 预期不能作为“自动 reviewer 完整通过”的样本；
它只能成为高风险路径转人工的真实结果。若未来目标改为“三项都必须进入隔离 reviewer”，
必须在运行前另行 amendment，不能在看到结果后放松风险门禁。

## 十、Sequelize 输入污染负向词表

正式调用 worker 和 reviewer 前必须扫描其最终编译输入，至少拒绝以下精确值：

```text
sequelize/sequelize#18274
fix(sqlite3): preserve autoincrement on alter sync
14aab37348d0d8d7bdca7dbe1faeeb4f8dedb67b
91bb5f43bed59d82f4db47d8cece9431558fdc4405463551d3441aefdc605b37
```

截至 2026-07-28 本次登记检查时，PR `#18274` 仍为 open、非 draft，base 与 head 仍分别为
预注册值。由于正式 worker 没有启动，最终角色输入尚未编译，负向扫描也尚未执行；不能把
“词表已登记”写成“输入已证明无污染”。

本地 Git 输入面审计未发现冻结修订之后的 heads、reflog 或已审计候选提交包含三个允许路径的
变更，公开 PR `#18274` 的 head 对象也不在目标副本中。但当前副本仍保留额外 `main`、reflog、
677 个 tag 和 promisor 元数据，不属于最小冻结副本。因此它只作为准备阶段证据；解除 Runtime
阻断后必须从冻结 SHA 重建只保留 Case ref 的干净副本并重新登记。

## 十一、本地原始证据

原始控制证据保存在 ignored 目录：

```text
.local-validation/crwp-v1/control-evidence/
```

关键入口：

```text
.local-validation/crwp-v1/control-evidence/registration-data.json
.local-validation/crwp-v1/control-evidence/process-audit.json
.local-validation/crwp-v1/control-evidence/sequelize-ref-surface-audit.json
.local-validation/crwp-v1/control-evidence/baseline-dormice-final/
.local-validation/crwp-v1/control-evidence/baseline-sequelize-final-v2/
.local-validation/crwp-v1/control-evidence/baseline-openstates-final/
```

`registration-data.json` SHA-256：

```text
92d2f06bcb05b628fac892c70e8367bcf3cc11868a8c584894482267a939d163
```

附加本地审计 artifact：

| Artifact | SHA-256 |
|---|---|
| `process-audit.json` | `6959ae44a38298908b6eda6b93dbfefc61986c4df54dc4a06516679ffd7e4318` |
| `sequelize-ref-surface-audit.json` | `10d0d8f00392098dd3840bfd7b6285d3cc7ef258553ac32271275e7acc5d91f9` |

该本地 JSON 包含绝对路径哈希、配置检查原始结果、完整命令、准备提交、baseline summary、
oracle JSON、ignored inventory、snapshot 结果、作废尝试和 setup 证据缺口。它不进入 Git，
也不是公开证据包。

当前控制证据下共有 30 个 owned execution 记录：`21 completed / 9 failed`，其中 8 个
`returncode=1` 是符合预期的缺陷 oracle，唯一非预期项是作废 Dormice runner 的
`returncode=2`。30 个记录均已进入终态，`termination_unconfirmed=0`。

登记整理阶段的 `process-audit.json` 使用“三个目标副本绝对路径是否出现在进程命令行中”
作为限定匹配规则，扫描结果为目标相关进程 `0`、已登记 execution PID 存活数 `0`。该结论
只绑定 artifact 中的观察时间，不证明历史任意时刻都没有残余进程。

## 十二、解除阻断后的固定顺序

1. 先补齐第 9.1 节的三个 oracle 合同，重新执行双次控制基线并追加 Control Amendment。
2. 再修复并验证 Vega ignored workspace inventory，不改本 Pilot 的三个业务题目。
3. 用覆盖 `node_modules`、Yarn workspace 和 `.venv` 的自动化测试证明：
   - 大型依赖目录不会造成无界枚举；
   - 高价值 ignored 文件被新增、修改或删除时仍会 fail-closed；
   - ignored 枚举 timeout 会被结构化捕获并形成可恢复的终态，不留下 `running` 半现场；
   - timeout、清单截断和内容预算都有结构化证据；
   - worker 前 baseline 和 worker 后比较使用同一语义。
4. 形成独立主线提交后，追加新的 CRWP-V1 Runtime amendment，登记新 Runtime SHA。
5. 从本登记的 Upstream base 重新创建三个干净目标副本，不复用可能被后续操作改变的现场。
6. 重新加入修订后的 `.vega.yaml`，移除 remote，重跑 config check、全部非 oracle 命令和 oracle
   双次基线。
7. 重新执行 Issue/PR 资格检查、模型探测和 Sequelize 输入负向扫描。
8. 只有新的 workspace baseline 完整且工作区 clean，才按固定顺序启动
   `CRWP-V1-01`、`CRWP-V1-02`、`CRWP-V1-03`。

本登记提交完成后仍不 push、不启动正式 worker，也不创建新的 Vega 工作分支。

## 十三、2026-07-29 Control Amendment：oracle 合同完成

本记录只追加 2026-07-29 的控制端结果，不改写第八、九节的历史阻断。正式 Worker、
Reviewer 和 Finish 仍未启动。

### 13.1 控制结果

| Case | Oracle SHA-256 | 两次退出码 | stdout SHA-256 | 结论 |
|---|---|---|---|---|
| `CRWP-V1-01` | `a1a9152a9d96f0ac935f6c26baccba7ce4632453b388ba570ceb62731ae65b5f` | `1 / 1` | `4308870971d831b3f42883a42ea06057da9267782574b278a527f614172095e1` | 合同完整，稳定复现 |
| `CRWP-V1-02` | `f784abc3518e12991f3f0b93628773adda1d68c9add4fe2a75d9e93b318e93d0` | `1 / 1` | `0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b` | 合同完整，稳定复现 |
| `CRWP-V1-03` | `79fd7227f44e1cf1aaff40d9f02b9d19a96834b14aa858de6c507503208ace0f` | `1 / 1` | `e3cf488350744510cbe084c452e4dc4ce4a314724ea0d02910837084c6675f21` | 合同完整，但资格已变化 |

表中 oracle SHA-256 来自 Git index 的 LF 字节，是提交后的权威控制哈希。Windows 工作树
CRLF checkout 的三个本机哈希分别为
`5af44f7ec73328d373c791b4b042c5465ceeb754e2d2bf4f1aef45d17328cf76`、
`61c85e423715ad748b3adabef6b9cd9718b51fb1a4aa31b416b981885479c294` 和
`596145bfa64a84ae98bf63f6b96401e027d145a50aafc89410f0be68954f7e02`，只用于行尾诊断。

本机原始输出位于：

```text
.local-validation/crwp-v1/control-evidence/oracle-contract-index-final-20260729/
```

其中 Dormice 一次并行尝试触发 `cli_timeout`，以 `exit=2` 结束。该尝试未被选入基线，
随后使用新目录串行重跑两次并得到一致结果。OpenStates 首次 index-byte 探测误用控制端
系统 Python，因缺少目标依赖 `i18naddress` 以 `exit=2` 结束；改用目标仓库既有 `.venv`
Python 后双次结果一致。无效尝试均不计入基线，三份有效 oracle 的 stderr 均为空。

### 13.2 阻断变化

- 第 9.1 节的三个 oracle 合同缺口已解除；
- PR `#22` 已解除旧的 ignored inventory 与 timeout 非终态阻断；
- 三个现有目标副本仍停在冻结 upstream SHA、工作区干净且没有 `.vega.yaml`；
- OpenStates PR `#125` 触发 `eligibility-changed-before-run`，Case 03 停止；
- Case 01/02 仍需从冻结 SHA 重建最小目标副本、加入并只提交 `.vega.yaml`，再重跑全部
  preflight 后才能启动正式 Worker。

当前状态固定为：

```text
oracle-contract-ready
case-01-preflight-pending
case-02-preflight-pending
case-03-eligibility-changed-before-run
worker-not-started
```

下一步只准备和执行 `CRWP-V1-01`、`CRWP-V1-02`。不得顺带增加 Runtime、测试框架、
Memory、LangGraph、多 Reviewer 或新的 Pilot Case。

## 十四、2026-07-31 Runtime 与正式输入 Amendment

本修订发生在任何正式 Worker、Reviewer 或 Finish 启动之前，只补齐 Case 01/02 的运行时
基线、独立任务输入、调用前扫描和总墙钟控制。历史失败与更正记录保持不变。

### 14.1 Runtime 与目标副本

本轮准备起点为：

```text
Preparation base: 6cfd51a9ca047bfc6cb4df3793c8925e17b351f4
```

`6cfd51a` 相比已完整测试的 `a8a58cb` 只追加接力文档，两者产品 `src` tree 相同。既有产品
Runtime 的可信 pytest 终态为 `908 collected / 900 passed / 8 skipped`。

正式控制输入采用严格的两提交冻结：

1. Runtime 提交 A 包含最终 controller、两个 task、两个 oracle、负向词表、native helper、
   控制测试和当时的 `src/`；
2. 登记提交 B 必须是 A 的单父直接子提交，并且只能新增
   `scripts/pilot/crwp-v1/crwp-v1-control-manifest.json`；
3. manifest 登记 A 的 commit/tree/`src` tree，以及每个控制文件在 A 中的 Git blob、
   SHA-256 与大小；
4. 正式命令显式传入完整 `--registration-head B`，当前 `HEAD` 必须精确等于 B。合并、
   rebase、squash 或后续提交都不能沿用本次登记，必须重新生成冻结。

Supervisor 启动 child 前、child 创建根 run 前、Finish 后以及 Supervisor 接受成功前都会
重新校验同一组冻结证据。任何工作区候选、HEAD 漂移、manifest 漂移、受控 Git blob 或
工作区字节变化都会 fail-closed；因此 A/B 尚未完成时不能启动正式 Worker。

两个目标均从冻结 SHA 新建，不复用旧 `.local-validation/` 副本：

| Case | Upstream base | Prepared HEAD | Prepared tree | `.vega.yaml` SHA-256 |
|---|---|---|---|---|
| `CRWP-V1-01` | `f26ba3748e79c7225f4aafb757c6f9f1f6b2d733` | `1b2084e4cae0e88c7fdabee7a851094832f6d0cf` | `7735e8269afab4a26b2b7c8cf66e074961f8ce28` | `d4322d5ce2c9e86dad259bfcf4795dc70d548a81eb01d115d5f84cc40c2711a7` |
| `CRWP-V1-02` | `f0cea95e38b4f2c9096267371ab305d08f7b8497` | `18431b84c44eaa14736a2f4f6e9d92fe812a923e` | `67f271bb1fbd2506fc556ecab4ea319b827b234f` | `844800d61f6dbd016357e796f3db5bb7f371b22c0cdc1e50ccf25d47e92b2024` |

两个准备提交都只包含 `.vega.yaml`，remote 为空，tracked、index 和未跟踪状态为空。
首次准备后的 review workspace fingerprint 分别为：

```text
CRWP-V1-01: 07dbe0954425174602496c88f0dca22419fb5094d6ecda5cb23e7af7d0d8ab29
CRWP-V1-02: 79a123bbf7bf716839a27f4043bf63a95924d78207db99d7c29d76e99887d4ec
```

这些指纹早于最终 Worker 启动，只用于说明准备现场。每个 Case 都必须在自身正式 Worker
前由控制器重新捕获 `workspace-preflight.json`；本表不能替代最终启动证据。

### 14.2 Case 02 SQLite 本机依赖准备

`--mode=skip-build` 安装后，正式准备只执行：

```text
corepack yarn --cwd node_modules/sqlite3 exec prebuild-install -r napi
```

该命令在 `node_modules/sqlite3` 内直接启动 `prebuild-install`，不进入项目级 install/build
阶段。结果为 `exit=0`、`7825 ms`，唯一新增本机产物为：

```text
node_modules/sqlite3/build/Release/node_sqlite3.node
bytes: 1980416
SHA-256: 5e1d1275e126c3fc584bcf5752fbf747bff89454bfcf8bc76c982b24e7815057
```

`require("sqlite3")` 在 Node `22.22.0 / win32-x64` 下成功并报告 SQLite `3.52.0`。
`ibm_db`、`oracledb`、全部既有 `.node` 文件、Yarn 状态文件、`package.json` 和 `yarn.lock`
均未变化；目标 Git clean、HEAD 未变、remote 为空，相关残余进程为 `0`。

本地证据：

```text
.local-validation/crwp-v1/preflight-20260731/case-02/native-setup/attempt-01-isolated-prebuild/
summary.json SHA-256:
2793ee75171ab8260226a2d25d9c5197e78857f466995b8ddf9abf1c48b241d8
```

一次人工残余进程复查命令发生 PowerShell ParserError，解析阶段未执行。该错误已单独保留，
随后正确复查得到 `match_count=0`；不得把更正包装成首次命令成功。

首次 native setup 后 baseline 在第二条命令的 Mocha 收集阶段停止。决定性异常是未使用的
IBM i `odbc` 包缺少本机 binding；该次运行没有进入 SQLite 用例，不能计入有效基线：

```text
.local-validation/crwp-v1/preflight-20260731/case-02/baseline-after-native/aborted.json
SHA-256: 139ef1d99a60e4cafb0e8f1871eabaf8e46554dd5a19be57c7679013588f1c8a
```

`ignore-native-drivers.cjs` 随后从只隔离顶层 `ibm_db`，收紧为精确隔离顶层 `ibm_db` 与
`odbc`；`sqlite3`、`@sequelize/sqlite3`、`odbc-extra` 和其他请求仍交给原 loader。
helper 的新 SHA-256 为
`2e6a0f95133df1ba2a928d2f99be5068ee13bad75e2bba01b10213b284020bde`。

修正后在全新目录从第一条命令重新执行完整 baseline，不从失败命令续跑：

```text
.local-validation/crwp-v1/preflight-20260731/case-02/baseline-after-native-v2/
preflight.json SHA-256:
94af6c67d6b8287bd8836c9ec5b33a9e8698aa4d3b7e804914191b9a9f830f31
summary.json SHA-256:
ed84e7fba9bb949df4d4b4115d2ce0a0bce8d26b3531a402557195e8163e5057
final-state.json SHA-256:
87d5bbf317eedfddc8196eddd82aa41a1f5ad3f55f376d56ea4053516ec5306a
```

结果为 `accepted=true`：build、`41 passing` 的定向 SQLite tests、ESLint 与
`git diff --check` 均 `exit=0`；oracle 两次均 `exit=1`，完整输出字节一致，SHA-256 为
`0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b`。
全部六次 owned execution 均 `termination_unconfirmed=false`，目标 Git clean、remote 为空，
相关残余进程为 `0`。

本轮保留但不计入有效结果的控制更正还包括：
`setup-control-error.json`、`prepared-attestation-control-error.json`、
`workspace-snapshot-control-error.json`、provider process audit 的 control error、
native setup 的 ParserError，以及 Case 01 的 hash normalization correction。Case 01
权威 baseline 是 `summary-final.json`，SHA-256 为
`52b754c163d5444e722ed5495c91b142a46b016b17b03a3a1024bc427f4645ff`。

### 14.3 资格与 Provider

2026-07-31 的只读资格复核得到：

- Dormice Issue `#33` 仍为 open、无 assignee、`0` comments，正文 SHA-256 仍为
  `efcabe0528e47271a5003b356340df6a120000c6f010859f70341007fc0f4021`；
- timeline 无 cross-reference，冻结 SHA 到默认分支 compare 为 `identical`；公开文本搜索未
  找到关联修复，但该搜索不能穷举无关联或无关键词的开放补丁；
- Sequelize Issue `#18265` 仍为 open、无 assignee、`0` comments，正文 SHA-256 仍为
  `2effa9288425d1227a5b3c61bf68eb33d8fd0ba81f023c2530d2cfb712df481e`；
- PR `#18274` 仍为 open、非 draft、未合并，base/head、`6934` bytes diff 和 diff SHA-256
  均与冻结值一致。

Codex CLI 为 `0.144.6`。`gpt-5.4 / medium / ephemeral / read-only` 与
`gpt-5.4 / high / ephemeral / read-only` 各执行一次，分别用时 `94.038s` 与 `86.057s`，
均 `exit=0` 且 stdout 精确为 `READY\n`。两个已知探测 PID 均已退出，没有换模型或挑结果
重跑。CLI 同时明确提示使用 fallback model metadata，并省略未声明支持的 `priority`
service tier；这不影响本次可用性探测，但必须作为环境限制保留。

本地汇总：

```text
.local-validation/crwp-v1/preflight-20260731/qualification/summary.json
SHA-256: 224e11f37faa21fe52ce31d2bcab80ca4aef863552ee186d389a9b4729d93223

.local-validation/crwp-v1/preflight-20260731/qualification/manifest.json
SHA-256: 974cf9961204a41319631fee0c2d11f1c25bac58734f17bacc419f9baba0c7e0
```

资格与 Provider 只证明启动条件的一部分，不证明模型会修复任务。正式控制器同时校验上述
两个文件的冻结哈希、`overall_preflight_component_passed=true` 和生成时间不超过 24 小时；
超期时必须重新复核，不能沿用旧探测启动 Worker。

### 14.4 独立任务合同与负向词表

正式 Worker 只接收对应 Case 的独立任务合同，不接收本文或整份预注册：

| 控制输入 | Runtime 提交 A 候选 SHA-256 |
|---|---|
| `scripts/pilot/crwp-v1/tasks/crwp-v1-01-task.md` | `25400d4a907ee90153cf9f69f659a44a06dc197fff7c67789b1b6f971033401c` |
| `scripts/pilot/crwp-v1/tasks/crwp-v1-02-task.md` | `803c3a516e833d41a8cb8c3009595fa66d2386776204ff8d14fea18dc2c22ad6` |
| `scripts/pilot/crwp-v1/crwp-v1-02-blocked-terms.txt` | `3c6cb6f708588702865b15cab2fa0dc0bb7a0044401e4cd7fe154a6cf40d05d8` |
| `scripts/pilot/crwp-v1/crwp-v1-01-timeout-oracle.py` | `a1a9152a9d96f0ac935f6c26baccba7ce4632453b388ba570ceb62731ae65b5f` |
| `scripts/pilot/crwp-v1/crwp-v1-02-sqlite-autoincrement-oracle.cjs` | `f784abc3518e12991f3f0b93628773adda1d68c9add4fe2a75d9e93b318e93d0` |
| `scripts/pilot/crwp-v1/ignore-native-drivers.cjs` | `2e6a0f95133df1ba2a928d2f99be5068ee13bad75e2bba01b10213b284020bde` |

两个任务合同都补充了必须由目标仓库测试直接证明的职责：Case 01 必须覆盖 Commander
入口并用 mock/spy 证明无效值不会调用连接或执行路径；Case 02 必须直接覆盖 SQL、metadata、
数据保留、主键不复用、连续两次 alter、行注释与块注释负对照。Case 02 任务合同按 UTF-8
原始字节扫描冻结的四项负向词表，命中数为 `0`；不在任务合同、项目规则或角色输入中主动
提供公开补丁。

最终权威值不依赖本文手工抄录：登记提交 B 的 manifest 会同时登记上述文件、controller 和
`tests/test_crwp_v1_control.py` 在 Runtime 提交 A 中的 Git blob、SHA-256 与大小；控制器
还会逐项确认登记提交 B 没有改写这些 blob。

### 14.5 Pilot 专用控制器

正式调用使用：

```text
scripts/pilot/crwp-v1/run_crwp_case.py
```

该文件的最终 Git blob、SHA-256 和大小由登记提交 B 的 manifest 绑定，不在本文复制易失
哈希。它是 Pilot 控制输入，不增加 Vega 产品 CLI、状态或 artifact schema；仍调用当前
`LoopAutomationRuntime` 与 `FinishRuntime`，并增加以下 Pilot 专用控制与证据边界：

1. 只允许在已登记的 Windows 环境运行；正式证据目录必须是
   `.local-validation/crwp-v1/formal-runs/` 下的独立子目录，且不得与目标仓库互相包含。
2. 隐藏 child 必须持有 supervisor 生成的一次性 nonce，并校验登记提交、当前父 PID、
   live owned execution、`execution_id`、child PID、lease 和固定 execution 路径；手工调用
   `--execute-child` 会在创建根 run 前拒绝。
3. 严格校验 Runtime 提交 A 与登记提交 B：B 必须是 A 的单父直接子提交且只能新增 manifest；
   当前 HEAD 必须精确等于 B；`src` tree、controller、tasks、oracles、负向词表、helper 和
   控制测试必须与 A 的 Git blob 及 manifest 完全一致。另绑定资格/Provider、对应 Case 的
   baseline、`.vega.yaml` 和 sqlite native 文件哈希。
4. 每个 Case 在根 run 创建前捕获 `workspace-preflight.json`，要求 HEAD、tracked/untracked、
   unsafe index、ignored manifest 和 Git control 证据满足冻结边界。
5. `PromptAuditRunner` 在每次真实 `Runner.run()` 前取得最终 prompt，经 Vega redaction 后把
   实际发送字节的 SHA-256、字符数、字节数和行数写入证据。它同时强制 Worker
   `workspace-write`、Reviewer `read-only`、角色 step、目标 repo、iteration、根 run ID 与
   `runs/<run-id>/iterations/<iteration>/executions/<role>` 路径一致；Case 02 每轮精确扫描
   负向词表，命中时不调用外部 runner。
6. Pilot 子进程只在运行期间包裹实际 `create_run_dir` 调用，把其真实返回 ID 独占写入
   `run-created.json`，随后恢复原函数；第一轮 Worker 前再用 `execution_context.run_id`
   交叉校验并独占写入 `wall-clock-start.json`。不扫描 `runs/`，也不猜“最新 run”。
7. 外层 supervisor 以 Vega owned process 运行整个 Case。首个 Worker 到 `1800` 秒时先对
   精确 run 执行 `vega stop`；stop 无 active execution、未在宽限期结束或 monitor 异常时，
   只停止自己创建的控制进程树。controller 返回后再严格重读 launch、workspace preflight、
   input attestation、根 state 和真实 Finish，并用 `validate_loop_evidence_snapshot()`、
   artifact integrity、freshness、最新验证与 Reviewer verdict 独立重算成功门禁；不会只信任
   child 写出的 `ready_to_commit`。同时用 controller 实际返回时刻复核 deadline。
8. controller 终止确认后始终检查精确根状态；仍为 `running` 时调用
   `RecoveryRuntime` 并确认转为同一 run 的 `needs_human`。任何 deadline、startup timeout、
   monitor error、termination-unconfirmed、身份冲突、recovery 或非 `ready_to_commit`
   Finish 都返回非零。

首个 Worker 在控制子进程启动后 `300` 秒内仍未开始时，同样停止该 owned 控制进程并保留
现场。外层 owned process 另有 `2400` 秒失效保护，只用于处理控制器异常，不放宽 `1800`
秒正式总墙钟。

`tests/test_crwp_v1_control.py` 当前包含 `33` 个测试函数、`40` 个 pytest case；本轮定向执行
结果为 `40 passed`。覆盖：

- prompt 阻断、实际 UTF-8 哈希、角色 sandbox、execution 路径、Reviewer 正向和跨 run 拒绝；
- 根 run 实际返回 ID、Worker 起点幂等和 direct child 阻断；
- live supervisor lease 正向，以及过期 lease、错误 child PID 的负向；
- evidence/repo 双向路径边界；
- 在线 deadline stop、controller 结束竞态 postcheck 和 monitor 异常；
- 两提交 Runtime 冻结的正向、额外登记改动、错误 manifest 和错误 HEAD；
- 表面成功与真实 state/Finish 冲突、缺失或 `null` artifact、独立重算失败与异常；
- Supervisor 对 child 伪成功和 owned controller 异常的最终拒绝，非法 JSON 终态摘要，
  以及精确 running run recovery 到 `needs_human` 与 recovery 权限失败；
- native helper 只隔离 `ibm_db`、`odbc`，继续转发 SQLite 与相邻模块名。

### 14.6 正式命令与顺序

Case 01：

```powershell
$env:PYTHONPATH = "$repoRoot\src"
$registrationHead = git rev-parse HEAD
python scripts/pilot/crwp-v1/run_crwp_case.py `
  --case-id CRWP-V1-01 `
  --repo .tmp/crwp-v1/targets/dormice-33 `
  --task scripts/pilot/crwp-v1/tasks/crwp-v1-01-task.md `
  --registration-head $registrationHead `
  --evidence-dir .local-validation/crwp-v1/formal-runs/<case-01-run-id>
```

Case 02：

```powershell
$env:PYTHONPATH = "$repoRoot\src"
$registrationHead = git rev-parse HEAD
python scripts/pilot/crwp-v1/run_crwp_case.py `
  --case-id CRWP-V1-02 `
  --repo .tmp/crwp-v1/targets/sequelize-18265 `
  --task scripts/pilot/crwp-v1/tasks/crwp-v1-02-task.md `
  --blocked-terms scripts/pilot/crwp-v1/crwp-v1-02-blocked-terms.txt `
  --registration-head $registrationHead `
  --evidence-dir .local-validation/crwp-v1/formal-runs/<case-02-run-id>
```

`<case-*-run-id>` 是控制端在启动前创建的唯一证据目录名，不是 Vega 根 run ID；真实 run ID
由控制器在根 run 创建点登记，再与角色 `execution_context` 交叉校验。`$registrationHead`
必须是只新增 manifest 的登记提交 B；控制器拒绝任何其他 HEAD。两个 Case 不并行，一项形成
终态记录后才进入下一项。Case 03 继续固定为 `eligibility-changed-before-run`。

### 14.7 提交前与 Worker 前最后门禁

严格的 Runtime 提交 A 与登记提交 B 完成前不得启动 Worker。固定顺序为：

1. 对 controller 与控制测试执行定向 pytest、Ruff、compileall 和 `git diff --check`；
2. 把 controller、tasks、oracles、负向词表、helper、控制测试、本 Amendment 与当前 `src/`
   提交为 Runtime 提交 A；
3. 从 A 的 Git blob 生成 `crwp-v1-control-manifest.json`，再创建只新增该 manifest 的
   单父登记提交 B；
4. 在 B 上确认 `git diff --name-status A..B` 只有 manifest，并运行仓库卫生、完整 pytest、
   全量 Ruff、compileall 与 `git diff --check`；
5. 确认 Case 02 v2 baseline 仍为 `accepted=true`、oracle 两次稳定失败，资格/Provider
   证据仍在 24 小时有效期内；超期必须重新生成资格证据并更新冻结合同；
6. 每个 Case 都由控制器在自身 Worker 前重新捕获 workspace snapshot，并确认目标 HEAD、
   Git clean、remote 为空、冻结文件未变且没有目标相关残余进程。

上述条件同时满足后，才先启动 Case 01。Case 01 的真实结果必须形成终态并追加
`eval/real-world-runs.md`，之后才能判断是否进入 Case 02。

## 十五、2026-08-04 Amendment 5：Case 02 单项重新冻结

Case 01 已于 2026-07-30 形成正式终态。其后 Dormice 出现新的关联 PR，该变化不回溯改写
Case 01 的既有结果，但 2026-07-31 同时覆盖 Case 01/02 的资格摘要不再用于启动 Case 02。
本 Amendment 只重新冻结 `CRWP-V1-02`，不重跑 Case 01，也不增加产品 Runtime、状态或
artifact。

### 15.1 新资格合同

2026-08-04 的资格与 Provider 证据明确包含：

```text
active_case_ids == ["CRWP-V1-02"]
```

控制器要求该字段与当前 Case 精确相等，不能把 Case 02 的资格证据用于其他 Case，也不能沿用
同时包含多个 Case 的旧摘要。冻结文件为：

```text
.local-validation/crwp-v1/preflight-20260804/qualification/summary.json
SHA-256: 876495381e3c4f6f449debbe0a1692d0cac6214c0ac44b0a40f1c3d4260c09c7

.local-validation/crwp-v1/preflight-20260804/qualification/manifest.json
SHA-256: be9b00ad4c6269f7430ab73692043aa1c6749461cd291a7665dff3f447a0ea06
```

证据生成时 Sequelize Issue `#18265` 与受控 PR `#18274` 的状态、base、head、diff 字节数和
diff SHA-256 均与预注册值一致。Codex CLI `0.146.0` 的
`gpt-5.4 / medium` 与 `gpt-5.4 / high` 探测均精确返回 `READY`。资格证据仍只有 24 小时
有效期，正式 Worker 启动前由控制器再次校验。

### 15.2 新基线合同

fresh prepared target 保持：

```text
HEAD: 18431b84c44eaa14736a2f4f6e9d92fe812a923e
parent: f0cea95e38b4f2c9096267371ab305d08f7b8497
tree: 67f271bb1fbd2506fc556ecab4ea319b827b234f
```

第一次控制尝试保留在 `baseline-after-native-v2/`。它在启动第一条命令前因
`RunnerExecutionContext` 新增必填 `execution_root` 参数而失败，不计入有效基线，也不覆盖。
只补充当前 Runtime 所需的 `execution_root` 后，使用全新目录从第一条命令重新运行：

```text
.local-validation/crwp-v1/preflight-20260804/case-02/baseline-after-native-v3/
summary.json SHA-256:
229df0aa22bcef2b2b91ceacf18bbe6e43d3c64f044230ac901eb7f10834f59a
```

结果为 `accepted=true`：build、`41 passing`、ESLint 与 `git diff --check` 均按冻结预期
通过；oracle 两次均以 `1` 退出，完整输出字节一致，SHA-256 仍为
`0ee06abd2c7451e416e7514f49ada0f7ff1017a14c6a94dde27d6db35464626b`。
六次执行均无未确认终止，目标 Git clean、remote 为空，相关残余进程为 `0`。

prepared target 的 `945` 个 tracked 文件、共 `9,657,058` 字节按四项冻结负向词表执行
原始字节扫描，命中数为 `0`。该前置扫描不能替代正式控制器对每次 Worker/Reviewer 最终
prompt 的调用前扫描。

### 15.3 Case 02 专用登记提交

旧的 `crwp-v1-control-manifest.json` 保留为上一轮登记历史，不修改、不复用。本轮控制器改用：

```text
scripts/pilot/crwp-v1/crwp-v1-case02-control-manifest.json
```

提交顺序保持两提交冻结：

1. Runtime 提交 A 包含控制器、控制测试和本 Amendment；
2. 登记提交 B 必须是 A 的单父直接子提交，且只能新增上述 Case 02 manifest；
3. 正式运行必须精确 checkout 到 B，控制器逐项校验 A/B、`src` tree、控制文件、资格、
   baseline、目标配置和 SQLite native 哈希；
4. B 上完整门禁通过后，才允许启动 Case 02 Worker。
