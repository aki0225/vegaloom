# Vega GitHub Pages 展示站 V1 交接

> 日期：2026-08-04
>
> 分支：`feat/github-pages-showcase-v1`
>
> 状态：本地预览与视觉验收已完成；已获授权通过 PR 合并，并在 CI 通过后部署 GitHub Pages

## 一、本轮完成内容

本轮在已确认的
[`GITHUB-PAGES-SHOWCASE-V1-PLAN.md`](GITHUB-PAGES-SHOWCASE-V1-PLAN.md)
范围内实现了第一版静态展示站：

- `site/index.html`
  - 首屏以 `Vega` 为主视觉，只保留核心承诺、一行证据链和两个行动入口；
  - 页面压缩为 Hero、真实案例、Quick Start 三个主体区块；
  - 三个真实案例从页面中段提前到 Hero 之后；
  - 案例面板默认只显示裁决、验证与 Reviewer，完整证据和边界按需展开；
  - 页面首屏和主要说明不依赖 JavaScript 才能阅读。
- `site/styles.css`
  - 暖米白编辑型长页、焦橙主色和六阶段低饱和色；
  - 去除阶段导航、状态说明字典、重复案例时间线、第二套六步流程和大型边界章节；
  - 移动端案例选择改为带吸附的横向轨道，避免卡片文字重叠；
  - 原生响应式布局、键盘焦点和 `prefers-reduced-motion`；
  - 不加载外部字体、图床、前端框架或第三方脚本。
- `site/app.js`
  - 从本地 `site/data/cases.json` 读取白名单案例；
  - 支持三个案例切换、键盘方向键切换和 Quick Start 复制；
  - 删除不再使用的阶段滚动跟踪和案例时间线渲染；
  - 案例字段使用 `textContent` 写入，不执行案例数据中的 HTML。
- `site/assets/`
  - 原创 Worker、Evidence、Reviewer 与人工决定关系 SVG；
  - favicon 和 Open Graph PNG。
- `scripts/build_showcase_data.py`
  - 使用人工核准的三个案例清单生成确定性 JSON；
  - 对照 `eval/real-world-runs.md` 中的来源标题和决定性事实；
  - 检查本机路径、明显凭据、原始模型内容和伪造成功率；
  - 支持 `--check`。
- `tests/test_showcase_data.py`
  - 覆盖确定性生成、来源、限制说明、敏感内容扫描和 CRWP-V1-02
    `needs_human` 事实。
- `.github/workflows/pages.yml`
  - 只允许 `workflow_dispatch` 手工触发；
  - 当前提交不会自动启用或部署 GitHub Pages；
  - 部署前重新核对案例、定向测试、Ruff 和仓库卫生。
- `.github/workflows/ci.yml`
  - 将新增脚本和测试纳入现有静态检查及 Python 3.12 分片覆盖。

`eval/`、Vega Runtime、Plan-first 和 Finish 产品逻辑均未修改。

## 二、编辑减法结果

2026-08-04 根据三轮本地桌面和移动端审阅完成纯展示层减法：

- 主叙事从 `7` 个长章节缩减为 `3` 个 section 和 `1` 条边界短条；
- 桌面页面高度由约 `8282px` 降到约 `2203px`；
- `390px` 移动端默认页面高度由约 `10100px` 降到约 `2498px`；
- 默认渲染的可见文字约 `569` 字符，段落由 `40` 个降到 `6` 个；
- 首屏流程图从六张说明卡压缩为 Worker、Evidence、Reviewer 与人工决定四个关系节点；
- 删除未再使用的 `site/assets/outcome-map.svg`；
- 未修改案例 JSON、生成器、Finish 字段或 JavaScript 数据合同；
- 未增加前端框架、构建工具、后台、统计或在线 Runner。

## 三、本地查看

在仓库根目录执行：

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory site
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

本轮使用的本地预览进程不属于仓库状态；换机器后重新运行上述命令即可。

## 四、当前验证证据

本轮实现阶段已经确认：

- `python scripts/build_showcase_data.py --check`：通过；
- `python -m compileall src scripts/check_repository_hygiene.py scripts/build_showcase_data.py`：
  通过；
- `python scripts/check_repository_hygiene.py --base-ref origin/main`：通过；
- `ruff check src tests scripts/check_repository_hygiene.py scripts/build_showcase_data.py`：通过；
- `git diff --check`：通过；
- 本地 HTTP：`200`；
- 浏览器 Console：`0` 个 warning / error；
- 页面运行时请求仅包含本仓库的 HTML、CSS、JavaScript、SVG、favicon 和案例 JSON；
- `1440 × 900` 桌面视口无横向溢出，默认页面高度约 `2203px`；
- `390 × 844` 移动视口无横向溢出，默认页面高度约 `2498px`；
- 移动端案例选择轨道可以横向滚动，卡片文字不再重叠；
- CRWP-V1-02 切换后显示 `needs_human`，并明确标注 Verification 与 Reviewer 未启动；
- “证据边界”默认折叠，展开后仍能读取实际变更、Gate 和证据上限；
- Quick Start 复制反馈为“命令已复制。”；
- `prefers-reduced-motion` 生效；
- 桌面首屏、移动端 Hero、案例、边界短条和 Quick Start 均已目视检查。

`python -m pytest -q tests/test_showcase_data.py` 在当前机器的 60 秒执行上限内没有返回可信
终态，遗留进程已终止。因此本轮不能声明 pytest 通过。补充证据为：

- pytest 成功收集 `7` 个测试；
- 同一文件的 `7` 个测试函数直接执行通过；
- 直接执行不是 pytest 正式通过证据，PR 的干净 CI 仍必须确认最终结果。

## 五、后续步骤

1. 推送现有分支并创建单一 PR；
2. 以 PR CI 的 pytest、Ruff、数据检查和仓库卫生结果作为正式工程门禁；
3. 合并后启用并手工触发 GitHub Pages；
4. 在实际项目子路径上复核资源、Open Graph、favicon、键盘操作和移动端布局。

在以上步骤完成前，只能表述为“本地展示实现已完成”，不能表述为“GitHub Pages V1
已经发布”。

## 六、当前明确不做

- 不在本分支修改 Vega Runtime；
- 不接入在线 Runner、表单、统计服务或后端；
- 不从 `runs/` 自动读取或发布内容；
- 不启用自动部署；
- 不把三个案例解释成总体成功率；
- 不绕过 PR CI 直接部署。
