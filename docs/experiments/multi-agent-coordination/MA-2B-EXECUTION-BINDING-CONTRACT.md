# MA-2B execution binding schema 合同

> 日期：2026-07-25<br>
> 分支：`experiment/ma2b-planner-worker-pilot`<br>
> 当前状态：`execution_binding_and_pricing_schema_fake_verified / real_execution_blocked`

## 一、边界

本 Slice 只冻结 `MA-2B-execution-binding` 的本地 schema、loader 与 fail-closed 校验，不创建真实
execution binding artifact，不读取 Provider 凭据，不调用真实 Planner、Worker、Reviewer 或
Provider，也不授权 Pilot 执行。

真实执行前仍必须单独新增预注册要求的：

- `eval/experiments/multi-agent-coordination/MA-2B-execution-binding.md`；
- pricing manifest artifact；
- 12 个 `MA2B-Cxx` task-pack 与 ground truth；
- owner 或独立复审后的明确执行授权。

## 二、已冻结的校验

`src/vega/ma2b_execution_binding.py` 只接受脱敏、可公开复核的绑定字段：

1. Provider family、interface、client version；
2. premium、budget、balanced reviewer 的固定模型标识；
3. Planner、Worker、Reviewer 的 reasoning 配置描述；
4. tool policy SHA-256；
5. pricing manifest 的仓库相对路径和 SHA-256；
6. UTC 可用性观测时间和执行窗口。

loader 支持从 Markdown fenced YAML 或纯 YAML 读取 payload，但所有引用都必须解析到仓库内普通
文件，pricing manifest 字节哈希必须与 `pricing_manifest_ref` 一致，且引用内容必须通过
`MA-2B-pricing` manifest schema。

execution binding 与 pricing manifest 的组合还必须满足：

1. Planner premium、Worker budget、Reviewer balanced 三类模型标识精确一致；
2. pricing manifest 的观测时间不晚于 execution binding 的 Provider 可用性观测时间；
3. pricing manifest 覆盖预注册的 12 个 case 与 3 个 treatment。

## 三、拒绝条件

以下情况必须 fail-closed：

- 模型标识使用 `latest`、`default` 或 `auto` 等别名；
- premium model 与 budget model 相同；
- 公开字段包含 endpoint、Authorization、API key、本机路径、路径逃逸或会触发脱敏的内容；
- binding 文件包含未知字段，例如 `api_key`；
- pricing manifest 路径逃逸、缺失、不是普通文件、JSON 无效、JSON key 重复或哈希不匹配；
- pricing manifest schema 无效、模型映射不一致，或 pricing 观测时间晚于 execution binding 观测时间；
- 时间戳不是 UTC `Z` 格式，或执行窗口顺序无效。

## 四、停止线

本 Slice 不允许把 schema 通过解释为 `execution_authorized`。只要还缺真实 `MA2B-Cxx` task-pack、
pricing manifest artifact、execution binding artifact 或复审授权，状态必须保持
`real_execution_blocked`。
