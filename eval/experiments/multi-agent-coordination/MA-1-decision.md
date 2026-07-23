# MA-1 委派合同与就绪性 Gate 决策

> 决策日期：2026-07-23<br>
> Gate：`MA-1`<br>
> 分支：`experiment/multi-agent-coordination`<br>
> 冻结基线：`main@521f9b924241ec258c75b2ecc893bdaa3be91abd`<br>
> 实验提交：`cd631c24f171984b0653d38128e5a5876c68b077`<br>
> 决策：`accept`<br>
> 后续授权：`MA-2 not authorized`

## 一、决策范围

接受 `MA-1` 作为严格、版本化、fail-closed 的执行前委派合同实现。

本结论只证明：

- `PlanContract` 可以被严格解析并绑定当前 task、policy、workspace snapshot、read/write
  scope、input artifact 与 verification command；
- `DelegationReadiness` 可以确定性产生 `budget_eligible`、`premium_required` 或
  `human_required`；
- 合同无效、事实错绑、读取或写入越界、未决决策和显式人工风险不会进入低成本 Worker
  路由；
- route evidence 可以形成 UTF-8、可哈希复核的最小 artifact。

本结论不证明 Planner 质量、真实 Worker 成功率、多 Worker 收益、A2A 必要性或主线合并
可行性，也不改变现有 CLI、Loop、Finish、Goal、Reviewer 或 Assurance 成功语义。

## 二、冻结输入与实现身份

研究合同使用 Git/LF 规范内容计算 SHA-256：将工作树 CRLF 规范化为 LF，以 UTF-8 无 BOM
字节计算。该口径避免 Windows 工作树换行转换造成虚假的合同漂移。

- 研究合同：
  `docs/experiments/multi-agent-coordination/RESEARCH-AND-EXPERIMENT-PLAN.md`
- 研究合同 SHA-256：
  `b9aa7d0e577b468aebc3b69e1eb3f5da70f8d6472d87d092bec61997aa6ed92a`
- `src/vega/delegation.py` SHA-256：
  `ab89081afa3d7887d0dc3b57130ac4c09539321484bbae85e49694e469287802`
- `tests/test_delegation_contract.py` SHA-256：
  `e75dfc65c47754c21b55d283d36b56130e65d12a89fec139cedd5cea4921baf1`
- `.github/workflows/ci.yml` SHA-256：
  `c7122e298b6b806c4079d5733da48d75915203d260470f33f4e9cdc6420df8bf`

以上三个收口文件尚未自动提交或推送；Vega 的行为边界保持不变。

## 三、Gate 复核中发现并关闭的问题

1. CI 仍固定为 `600` 个测试节点，且 Python 3.12 分片未包含委派合同测试。
   已更新为最终收集的 `649` 个节点，并把
   `tests/test_delegation_contract.py` 纳入语义与证据分片。
2. `read_paths` 原先由计划自报，未与权威 scope 绑定。
   已在 `DelegationValidationContext` 增加必填 `allowed_read_paths`，读取越界现在产生
   `read_path_outside_compiled_scope:<slice-id>` 并返回 `human_required`。
3. 仓库相对敏感路径原先可以进入合同。
   所有合同、artifact reference 与 validation context 路径现在复用
   `sensitive_path_reason()`，拒绝环境文件、凭据文件、私钥名称和敏感密钥后缀。
4. verification command 原先可以携带本机绝对路径。
   现在按仓库卫生门禁的同一口径拒绝 Windows 盘符绝对路径、Windows UNC 和真实 POSIX
   用户主目录。

对应测试先观察到预期红灯，修复后
`tests/test_delegation_contract.py` 为 `49 passed`。

## 四、验证证据

最终工作树共收集 `649` 个测试节点，使用互不重叠的文件集合和完整 node id 分片执行。
所有超时组合均不计入通过，随后使用新的独立 `--basetemp` 与 `cache_dir` 细分重跑。

| 分组 | 收集 | 通过 | 跳过 | 失败 |
| --- | ---: | ---: | ---: | ---: |
| smoke、P0、CLI、执行控制与安全 | 271 | 270 | 1 | 0 |
| 成功语义、委派合同与证据完整性 | 174 | 174 | 0 | 0 |
| 路径、恢复、配置、脱敏与上下文 | 190 | 190 | 0 | 0 |
| Assurance verification 慢测试 | 14 | 14 | 0 | 0 |
| **合计** | **649** | **648** | **1** | **0** |

唯一跳过节点为：

```text
tests/test_runtime_safety_integration.py::test_posix_verification_temp_env_does_not_re_evaluate_path
```

跳过原因是该测试只覆盖 POSIX shell 变量展开语义，在 Windows 上由显式 `skipif` 跳过。
本机没有可用 WSL，因此未把该节点包装成通过；POSIX 与 Python 3.11 仍由后续 CI 复核。

其余门禁结果：

```text
python -m compileall src scripts/check_repository_hygiene.py
passed

ruff check src tests scripts/check_repository_hygiene.py
passed

python scripts/check_repository_hygiene.py --base-ref origin/main
passed

git diff --check
passed

Python 3.12 分片文件集合
23 / 23 covered
```

## 五、静态边界复核

- 未发现 `PlanContract` 自报 route、隐式默认 route 或 Planner 反向构造权威 context 的代码
  路径。
- schema、文件读取、snapshot、scope、artifact、verification command 任一无效或错绑时均
  fail-closed。
- 未发现 MA-1 接入 CLI、Loop、Finish、Goal、Reviewer 或 Assurance 成功条件。
- `write_delegation_readiness_result()` 的物理输出路径仍由调用方提供。由于 MA-1 尚未接入
  Runtime，该项记录为低风险残余边界；未来接入前必须把输出限定到 repo-owned `runs/`
  或 `.tmp/`，并在解析后执行 containment 检查。

## 六、最终裁决

`MA-1` 结论为 `accept`。

停止条件保持有效：

- 不自动进入 `MA-2`；
- 不启动真实 Planner、Worker、多 Worker、mailbox 或 A2A；
- 不把 Delegation Readiness 当作执行成功或 Assurance 证据；
- 不整分支合并公开主线；
- 若后续需要改变冻结 baseline、route 变量或成功语义，关闭当前 Gate 并重新预注册。

## 七、提交前最终复核修正

最终提交审查发现，原 verification command 路径规则虽然拒绝真实 POSIX 用户目录，但仍会
接受 `/tmp/...`、`/var/...` 等一般 POSIX 绝对路径。该发现发生在提交和推送之前，因此没有
把旧实现固化为远端结果。

本轮追加收紧为：

- 在命令 token 边界拒绝一般 POSIX 绝对路径；
- 保持 `https://...` URL 不被误判；
- 保持 `cmd /d /c` 和 `/?` 等单字符 Windows 开关可用；
- 使用 `/tmp/check.py` 红灯用例替代只覆盖用户主目录的 POSIX 用例；
- 在既有成功场景中加入 `cmd /d /c` 兼容性验证。

最终专项测试仍为 `49 passed`，节点总数保持 `649`。源码变更后重新从零执行全部分片，结果
仍为：

```text
649 collected
648 passed
1 skipped
0 failed
```

第二节记录的 `.github/workflows/ci.yml` 哈希保持有效；以下两个最终 Git/LF SHA-256 替代
第二节中的旧值：

- `src/vega/delegation.py`：
  `71408029f8b4d924ba4548ac08eaa957b305cd57d4bb90f098ceb937ce0a6968`
- `tests/test_delegation_contract.py`：
  `4b013507ea2aacadaf73d6533d9685055ebbbec014c969d1b12261f8dafb52fc`

残余低风险：在没有显式 shell kind 的合同中，单字符 `/x` 同时可能表示 Windows 开关或
POSIX 单段绝对路径；`file:///...` 也属于 URI 而非当前命令路径 token 规则。MA-1 尚未接入
Runtime，当前不把该歧义视为 Gate 阻断。后续若要把委派合同接入真实执行，应先显式绑定
shell kind，并分别校验 shell 开关、POSIX 路径与本机文件 URI。

以上修正不改变 `MA-1` 的 `accept` 结论，也不授权进入 `MA-2`。
