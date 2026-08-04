# Vega GitHub Pages 展示站 V1 交接

> 日期：2026-08-04
>
> 分支：`feat/github-pages-showcase-v1`
>
> 状态：已形成可运行的本地预览；尚未启用 GitHub Pages，尚未部署或合并

## 一、本轮完成内容

本轮在已确认的
[`GITHUB-PAGES-SHOWCASE-V1-PLAN.md`](GITHUB-PAGES-SHOWCASE-V1-PLAN.md)
范围内实现了第一版静态展示站：

- `site/index.html`
  - 首屏使用已确认文案；
  - 展示“一个 Bug 的两种处理方式”、六个检查点、三个真实案例、三种 Finish 结局、
    Design Boundary、Quick Start 和页脚入口；
  - 页面首屏和主要说明不依赖 JavaScript 才能阅读。
- `site/styles.css`
  - 暖米白编辑型长页、焦橙主色和六阶段低饱和色；
  - 原生响应式布局、键盘焦点和 `prefers-reduced-motion`；
  - 不加载外部字体、图床、前端框架或第三方脚本。
- `site/app.js`
  - 从本地 `site/data/cases.json` 读取白名单案例；
  - 支持三个案例切换、键盘方向键切换、阶段导航状态和 Quick Start 复制；
  - 案例字段使用 `textContent` 写入，不执行案例数据中的 HTML。
- `site/assets/`
  - 原创六检查点流程 SVG；
  - 原创三种 Finish 结局 SVG；
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

## 二、本地查看

在仓库根目录执行：

```powershell
python -m http.server 8765 --bind 127.0.0.1 --directory site
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

本轮使用的本地预览进程不属于仓库状态；换机器后重新运行上述命令即可。

## 三、当前验证证据

推送前应以本文件后续更新和最终终端结果为准。本轮实现阶段已经确认：

- `python scripts/build_showcase_data.py --check`：通过；
- `python -m pytest -q tests/test_showcase_data.py`：`7 passed`；
- `ruff check scripts/build_showcase_data.py tests/test_showcase_data.py`：通过；
- `python -m compileall -q scripts/build_showcase_data.py`：通过；
- `python -m compileall src scripts/check_repository_hygiene.py scripts/build_showcase_data.py`：
  通过；
- `python scripts/check_repository_hygiene.py --base-ref origin/main`：通过；
- `ruff check src tests scripts/check_repository_hygiene.py scripts/build_showcase_data.py`：通过；
- `git diff --check`：通过；
- 本地 HTTP：`200`；
- 浏览器 Console：`0` 个 warning / error；
- 页面运行时请求仅包含本仓库的 HTML、CSS、JavaScript、SVG、favicon 和案例 JSON；
- AnyIO 与 packaging 案例切换已实际验证，面板内容与白名单数据一致；
- 桌面首屏与完整长页已经生成本地截图进行目视检查。

`python -m pytest -q` 在约 `262` 秒后仍未返回可信终态，执行工具超时终止；没有残留对应
pytest 进程。本轮不能声明全量测试通过，现有可信测试证据仅为展示站定向测试 `7 passed`。

三个 Luna 子代理均在启动时被宿主代理返回 `502 Bad Gateway`，没有产生文件或结论；
本轮实现由主会话完成，没有静默改用其他子代理模型。

## 四、下一次继续时优先检查

1. 从远端拉取并切换到 `feat/github-pages-showcase-v1`；
2. 启动本地 HTTP 预览；
3. 检查普通笔记本、`390px` 移动端和长文滚动体验；
4. 实际验证：
   - 三个案例点击与键盘切换；
   - `needs_human` 的橙色状态和“Verification / Reviewer 未启动”；
   - Quick Start 复制；
   - `prefers-reduced-motion`；
   - 子路径部署资源引用；
5. 根据目视结果只调整文案密度、字号、间距和流程图，不扩大网站范围；
6. 用户确认本地效果后，再决定是否：
   - 补 README 和文档导航入口；
   - 创建 PR；
   - 合并后手工启用并触发 GitHub Pages。

## 五、当前明确不做

- 不在本分支修改 Vega Runtime；
- 不接入在线 Runner、表单、统计服务或后端；
- 不从 `runs/` 自动读取或发布内容；
- 不启用自动部署；
- 不把三个案例解释成总体成功率；
- 不在用户确认本地效果前合并实现分支。
