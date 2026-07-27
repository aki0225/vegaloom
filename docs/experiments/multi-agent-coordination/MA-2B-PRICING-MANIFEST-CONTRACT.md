# MA-2B pricing manifest schema 合同

> 日期：2026-07-25<br>
> 分支：`experiment/ma2b-planner-worker-pilot`<br>
> 当前状态：`pricing_manifest_schema_fake_verified / real_execution_blocked`
>
> 2026-07-27 状态更新：12 个正式 task-pack 与 ground truth 已迁入 readiness 默认根，但真实
> pricing、execution binding、authorization 和 Provider 可用性仍未建立。

## 一、边界

本 Slice 只冻结 `MA-2B-pricing` manifest 的本地 schema、loader 与 fail-closed 校验，不创建真实
pricing artifact，不读取 Provider 凭据，不访问 Provider endpoint，也不授权 Pilot 执行。

真实执行前仍必须单独新增预注册要求的：

- 真实 `eval/experiments/multi-agent-coordination/pricing/MA-2B-pricing.json`；
- `eval/experiments/multi-agent-coordination/MA-2B-execution-binding.md`；
- owner 或独立复审后的明确执行授权。

## 二、已冻结的校验

`src/vega/experimental/ma2b/pricing.py` 只接受公开可复核的定价快照字段：

1. schema version、币种、公开来源类别和脱敏来源标签；
2. 定价观测时间与有效窗口，全部必须是 UTC `Z` 格式；
3. 预注册的 12 个 case 与 3 个 treatment 覆盖；
4. 单 case 与总成本上限，金额必须是固定小数字符串；
5. Planner premium、Worker budget、Reviewer balanced 三类模型的固定模型标识；
6. 每个模型的 input、output 与可选 cached input token 单价，单位固定为
   `usd_per_1m_tokens`。

execution binding 现在不再只检查 pricing manifest 是否为有效 JSON。它还必须确认：

- pricing manifest 字节哈希与 `pricing_manifest_ref` 一致；
- pricing manifest schema 和语义校验全部通过；
- 三类模型标识与 execution binding 中的模型映射精确一致；
- pricing manifest 的 `observed_at_utc` 不晚于 execution binding 的
  `availability_observed_at_utc`。

## 三、拒绝条件

以下情况必须 fail-closed：

- 未知字段，例如 `api_key`；
- 来源标签或模型标识包含 endpoint、Authorization、API key、本机路径、路径逃逸或会触发脱敏的内容；
- 模型标识使用 `latest`、`default` 或 `auto` 等别名；
- 三类模型未按固定顺序完整覆盖，或重复使用模型标识；
- 金额不是字符串、使用指数写法、为负数，或必需计费项为 0；
- case count 不是 12，treatment count 不是 3；
- 定价有效窗口顺序无效，或 pricing 观测时间晚于绑定观测时间；
- manifest 路径逃逸、缺失、不是普通文件、JSON 无效、JSON key 重复或文件过大。

## 四、停止线

本 Slice 不能被解释为 `execution_authorized`。pricing manifest schema 通过只说明“未来真实
定价快照有可校验格式”，不说明当前已有真实价格、真实 Provider 可用性或真实 Pilot 样本。
当前正式输入虽已齐全，但只要仍缺真实 pricing artifact、execution binding artifact 或
复审授权，状态必须保持 `real_execution_blocked`。
