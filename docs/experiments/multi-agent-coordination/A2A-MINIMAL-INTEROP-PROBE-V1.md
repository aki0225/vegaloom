# A2A 最小互操作探针预注册 V1

> 日期：2026-07-28
> 分支：`experiment/ma2b-pilot-next`
> 状态：`design_only / runtime_not_implemented / provider_not_authorized`
> 当前授权：只冻结问题、变量、协议映射、门禁与停止线
> 当前不计入：MA-2B readiness、Multi-Worker 能力或经济性结论

## 一、唯一实验问题

本探针只回答：

> Vega 已有的最小任务、写范围和固定 verifier，能否通过 A2A 交给独立 Agent，并在不传递
> 完整会话、不新增证据模型的前提下，得到可安全应用和统一验证的结果？

它不回答：

- A2A 是否让 Multi-Worker 更快或更省 Token；
- A2A 是否改善任务拆分、计划质量、代码质量或合并语义；
- 不同模型、Provider 或 Agent Runtime 谁更强；
- A2A 是否应进入默认 Runtime、CLI 或主线产品。

当前 C07/C05 已经给出 Multi-Worker 机械能力正向信号，但没有观察到经济收益。A2A 是新的
跨运行时互操作问题，不能被包装成 MA-2B 的下一组性能样本。

## 二、真实使用场景和进入条件

唯一有效场景是：

```text
Vega 本地协调器
→ 独立部署、不同运行时或不同权限域的 Agent
→ 返回有限的代码 artifact
→ Vega 本地检查写范围并运行固定 verifier
```

真实执行前必须同时满足：

1. 已确定一个不能安全地作为进程内 `WorkerAdapter` 调用的独立 Agent；
2. 对方需要通过网络边界发现能力、接收任务并返回 artifact；
3. 任务可以完全由现有 task-pack、写范围和 verifier 表达；
4. 身份认证、传输加密、数据最小化和取消语义已经明确；
5. 映射不要求恢复 `PlanContract`、新增 ledger/receipt 或修改通用 Runtime。

如果没有这样的独立 Agent，结论就是当前不需要 A2A，不以本地自测代替真实需求。

## 三、最小协议映射

只映射已有事实，不创建新的持久化 `HandoffPacket`：

| Vega 已有内容 | A2A 表达 | 约束 |
|---|---|---|
| task summary、acceptance、constraints | 初始 `Message` 的文本或结构化 Part | 不包含聊天历史和隐藏推理 |
| case id、task-pack hash、workspace revision | `Message` metadata 或结构化 Part | 只使用仓库相对标识 |
| 冻结初始 workspace | 有界 `FilePart` 或不可变 source ref | 不发送凭据、本机绝对路径或额外仓库内容 |
| 允许修改的文件 | 结构化 Part 中的 `allowed_write_paths` | 只能来自现有 project policy |
| 修改后的文件 | 完成 `Task` 返回的 `Artifact` | 每个文件必须带仓库相对路径 |
| 执行状态 | A2A `Task` lifecycle | 只映射状态，不接受 Agent 自评作为成功依据 |
| 取消 | A2A cancellation | 取消后不应用迟到 artifact |
| 最终成功 | Vega 本地固定 verifier | 远端 Agent 无权改变 verifier 或成功条件 |

首轮不使用 streaming、push notification、动态 Agent 网络、mailbox 或 Agent 间自由聊天。
Agent Card 只用于发现一个预先配置的目标，不做全网发现。

## 四、两步探针

### P0：本地协议资格检查

P0 不调用 Provider，不产生 Agent 能力结论。

- 输入：现有 fake fixture `MA2B-F01`；
- 控制：同一个确定性 fake Worker 直接通过 `WorkerAdapter` 执行；
- 处理：同一个 fake 行为包装为本地 loopback A2A Agent；
- 唯一变量：直接函数调用与 A2A 传输；
- verifier、初始 workspace、允许写路径和 fake 行为保持一致。

P0 还包括两个故障检查：

1. A2A Agent 返回越界路径或格式无效的 artifact，Vega 必须在写入前拒绝；
2. A2A Task 被取消后，即使迟到返回 artifact，也不得写入最终 workspace。

P0 通过只代表协议路径有资格进入真实跨运行时探针。

### P1：未来真实跨运行时探针

P1 需要 Owner 另行授权真实目标、网络访问和 Provider 调用；当前不执行。

- 输入：优先复用已验证 task/verifier 一致的 `MA2B-C07`；
- 执行：一个独立 A2A Agent 完成整个单 Worker 任务，不引入第二个 Worker；
- 输出：只返回允许路径内的文件 artifact；
- 裁决：Vega 在隔离 workspace 应用结果后运行 C07 固定 verifier；
- readiness：不修改 C07 task-pack、ground truth、hash 或正式 12-case gate。

如果同一个 Agent Runtime 同时提供直接 Adapter 和 A2A 接口，可以额外观察协议墙钟开销。
如果两路使用不同 Runtime、模型或 Provider，则只报告互操作结果，不比较性能或经济性。

## 五、P0 资格条件

以下条件必须全部满足：

1. 能读取预先配置目标的 Agent Card；
2. A2A Task 能完成明确的状态转换并返回 Artifact；
3. 直接路径与 A2A 路径产生相同的允许写路径和最终文件内容；
4. 两路都通过同一个 `MA2B-F01` verifier；
5. 越界或格式无效的 artifact 在写入前 fail-closed；
6. 取消后的 artifact 不进入最终 workspace；
7. 实现只位于 MA 实验范围，不修改通用 Runtime、CLI 或 CI；
8. 不增加新的 task-pack 字段、证据 schema 或 readiness 条件。

墙钟和 payload 大小只作为观测值记录，不预设经济性成功阈值。

## 六、结果解释

- P0 全部通过：允许讨论一次 P1，不代表 A2A 已产生产品价值；
- P0 协议或 artifact 映射失败：停止，不通过增加通用基础设施绕过；
- P0 安全、scope 或取消语义失败：拒绝当前方案；
- P1 verifier 通过且无 scope violation：只能称为跨运行时互操作正向信号；
- P1 的 Agent、模型或 Provider 与控制路径不同：不得把差异归因于 A2A；
- 只有一个真实 case：不得宣称通用可靠性、经济性或产品 readiness。

## 七、停止线

发生以下任一情形立即停止：

- 需要修改通用 Runtime、Reviewer、Runner、CLI、CI 或成功语义；
- 需要新建证据 ledger、receipt、路由合同或长期运行记录层；
- 需要放宽正式 12-case readiness、scope 或 verifier；
- 需要发送 API key、完整会话、隐藏推理或未裁剪仓库；
- 需要 A2A Agent 直接决定 Vega 的成功状态；
- 需要启动第二个 Worker、Reviewer、MA-3 或 multi-worker 产品化；
- 当前没有可识别的真实跨运行时目标，却继续扩建 A2A 框架。

## 八、当前产物与后续授权

当前只提交本文和研究计划中的过时条件修正，不新增依赖、代码、fixture、测试或运行结果。

未来若单独授权 P0，实现范围仍应保持为一次性 MA 定向探针。P0 通过后，必须再次由 Owner
确认具体 A2A 目标、网络边界和调用上限，才能执行 P1。

## 九、协议基线

本设计依据 A2A 1.0 系列公开协议中的 Agent Card、Message、Task、Artifact、Part 和取消语义。
真正实现 P0 前必须冻结精确协议与 SDK 版本，不能直接跟随浮动的 `latest`：

1. A2A Protocol Specification：<https://a2a-protocol.org/latest/specification/>
2. A2A 官方仓库 Releases：<https://github.com/a2aproject/A2A/releases>
