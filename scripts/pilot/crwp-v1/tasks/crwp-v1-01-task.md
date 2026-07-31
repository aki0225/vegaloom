# CRWP-V1-01 任务合同

## 目标

修复 Dormice CLI 对 `dor sandbox exec --timeout` 的参数校验。当前无效值会继续进入内部
`AbortSignal` 路径并暴露实现异常；应在建立连接或调用执行逻辑前给出明确的参数错误。

公开来源为 `BitMiracle-AI/Dormice#33`，运行基线固定为仓库已准备提交。

## 行为合同

1. 使用 `Number(value)` 解析 `--timeout`，结果必须同时满足有限、整数且大于零。
2. 拒绝 `not-a-number`、`10m`、`0`、`-1`、`1.5` 和 `Infinity`。
3. 接受 `1`、`60`、`1e2`、`0x10` 和 `+1`，并把解析后的正整数传入既有执行路径。
4. 未传 `--timeout` 时保持现有默认行为。
5. 无效值不得调用 `clientFromEnv`、`sandboxExec` 或建立连接。
6. 错误应明确指向 `--timeout` 或 seconds 参数，不得出现 `AbortSignal`、`delay`、
   `RangeError` 或堆栈。
7. 不重复实现服务端已有的最大值限制，不改变其他 CLI 命令的解析和退出码。

测试必须从 `packages/cli/src/commands.test.ts` 直接覆盖 `main.ts` 的 Commander 参数解析入口，
不得只测试新抽出的独立 helper。对无效值必须使用 mock 或 spy 证明 `clientFromEnv` 与
`sandboxExec` 均未调用；对有效值必须证明传入既有执行路径的是解析后的正整数。

## 修改边界

只允许修改：

```text
packages/cli/src/main.ts
packages/cli/src/commands.test.ts
```

不得修改依赖、锁文件、工作区配置、项目规则、任务合同、Vega 策略或独立 oracle；不得新增
文件。不要 commit、push、release、删除文件、联网检索或写入长期 Memory。

## 验证

Vega 将独立执行目标包 build、CLI 单测、typecheck、Biome、外部 timeout oracle 和
`git diff --check`。如需求或环境阻塞，停止并明确说明，不要通过放松断言或修改验证入口绕过。
