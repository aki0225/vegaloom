# Vega GitHub Pages 展示站 V1 计划

> 文档类型：历史设计记录。本文保留 V1 冻结范围与验收过程，不是当前页面维护计划；当前页面
> 与数据合同以 `site/`、`scripts/build_showcase_data.py` 和 `tests/experimental/test_showcase_data.py`
> 为准。
>
> 日期：2026-08-04
>
> 状态：`completed / deployed`；展示站已合并并发布到 GitHub Pages
>
> 当前入口：<https://aki0225.github.io/vegaloom/>
>
> 阅读说明：本文保留实施前冻结的范围、分支和验收合同。正文中的“准备部署”“等待 CI”
> 等表述属于当时的计划，不是当前待办；当前使用与版本状态以 `README.md`、
> `docs/README.md` 和 `docs/ROADMAP.md` 为准。

## 2026-08-19：Supervisor Agent 运行回放

展示站现已随 `v0.2.0` 调整为 Supervisor Agent 叙事，原 V1 计划正文继续作为历史设计记录，
不再代表当前页面结构。当前页面不再重复罗列控制能力，而是把同一次发布验收中的多个 Run
拆成三段短回放：

1. Writer 中断、现场对账、Git Task Card 接手与 Provider 429；
2. 前端验证通过后，Reviewer 因行为覆盖和后端证据不足打回；
3. 人工批准 Plan revision 2，完成后端 `361 passed`、设置页定向 `7 passed` 和前端完整
   `180 passed`，随后进入 `ready_to_commit`。

回放左侧按时间展示主会话可见的低频事件，右侧同步更新 Vega 状态卡；访客可以播放、暂停、
重置或切换片段。页面只保留一条紧凑职责说明，并把 Reviewer 历史案例降为一个代表案例与
两个次级证据入口，避免把 Supervisor Agent 重新讲成单纯的代码评审工具。

`site/data/cases.json` 由 `scripts/build_showcase_data.py` 从人工核准的公开来源生成，schema
升级为 `4`。每个场景列出可公开核对的关联 Run；前序 Handoff 未公开 Run ID，因此只从
Provider 429 的恢复 Run 开始列出。每个回放事件必须绑定允许来源，生成器冻结事件顺序、
状态语义、数量、时长和 scenarios 规范化 JSON 的 SHA-256。这个哈希不覆盖完整
`cases.json` 或源证据文件。页面明确披露：这是从发布验收证据编排的结构化状态回放，不是
原始 Trace、实时终端或完整模型会话；本机 state、Trace、命令日志、凭据和隐藏推理仍不进入
展示站。公开证据链接和生成器读取来源均固定到 `v0.2.0`，不会用当前工作区文件替代发布
证据。数据文件读取失败时保留页面内置摘要并停用回放控件，不再维护第二份事件数据。

## 一、目标

为 Vega 建立一个适合公开介绍、简历引用和技术交流的静态展示站，让第一次接触项目的人能够：

1. 在 30 秒内理解 Vega 解决什么问题；
2. 在 3 分钟内理解 Worker、验证、Reviewer 和 Finish 的关系；
3. 在 10 分钟内通过真实案例判断项目已经证明了什么、没有证明什么；
4. 从展示站直接进入快速开始、GitHub 仓库和详细证据。

展示站不负责增加 Vega 的产品能力。它只整理当前 `v0.1.4` 已经存在的机制、真实运行结果和
边界说明。

如果本计划获批，展示站 V1 临时排在调查与 Plan-first 协议之前。正式优先级变更只在用户确认
后更新 `docs/ROADMAP.md` 和 `docs/DAILY-USAGE-COMPLETION-PLAN.md`。

## 二、为什么现在做

Vega 当前已经具备可公开展示的材料：

- `v0.1.4` 稳定发布与跨平台 CI；
- Worker 与 Reviewer 独立会话；
- Workspace、Scope、Verification 和 Risk Gate；
- `ready_to_commit`、`request_changes` 与 `needs_human` 终态；
- timeout、工作区污染和主机中断后的 fail-closed / recover 记录；
- Python、JavaScript 和 Go 真实项目案例；
- 追加式 `eval/real-world-runs.md` 证据记录。

继续增加 Runtime、Agent 或 Artifact 的边际收益已经低于把现有能力讲清楚。展示站可以先验证：

- 外部访问者能否准确理解产品定位；
- 当前证据是否足以支撑公开表述；
- 哪些信息确实值得进入 Finish 第一屏；
- 后续 Plan-first 或 Finish 调整是否来自真实反馈，而不是继续抽象扩张。

## 三、非目标

V1 明确不做：

- 在线运行 Worker、Reviewer 或 Vega CLI；
- 上传仓库、用户登录、任务队列和 Web 控制台；
- 模型 API Key、Provider 代理或服务端；
- 在线编辑 `.vega.yaml`；
- 实时展示模型正文、推理或原始工具参数；
- 发布本机 `runs/`、`.local-validation/` 或 `.tmp/`；
- 宣称容器、操作系统级安全隔离或生产安全；
- 统计没有预注册口径的整体成功率；
- 重写 README、产品契约或历史 `eval/` 证据；
- 为网站引入 React、Next.js、数据库、CMS 或新的长期 Runtime。

## 四、参考风格与取舍

视觉参考：

- [从 AI 写代码到 AI 工作流 · 六阶段之旅](https://czm15053.github.io/ai-workflow-six-stages/)

采用的部分：

- 米白纸张背景、宋体风格大标题和窄正文栏；
- 用一个实际工程问题贯穿页面，而不是堆功能卡；
- 使用阶段颜色、序号、图示和故事卡组织长页面；
- 先展示失败方式，再解释对应机制；
- 图、表、短段落交替，保持中文技术文章的阅读节奏。

不直接复制的部分：

- 不制作约 45 分钟的超长文章；
- 不采用 Brainstorm、Design、Plan、Execute、Verify、Ship 作为 Vega 已实现功能；
- 不比较大量外部 Agent 框架；
- 不依赖外部图床、字体 CDN 或大体积视频；
- 不复用参考页面的插图、文案、布局代码或品牌元素。

Vega 展示站应保留编辑型长页的质感，但首屏和关键结论必须更接近产品案例页。

## 五、核心叙事

### 产品标识（已确认）

```text
One writes, one reviews.
```

### 首屏开场（2026-08-04 编辑减法后）

```text
Vega

AI 写的代码，得看证据。
```

### 首屏说明

```text
Worker 留下真实 Diff，项目验证实际运行，Reviewer 从一个全新会话审查。
证据不足，Vega 就停在人工可以接管的位置。
```

首屏只保留产品名、核心承诺、一行证据链和两个行动入口。边界统一放到页面后部的短条中，
不在首屏重复解释。

### 全页故事

```text
一次 AI 修改，三种结局：

ready_to_commit
证据完整，可以人工检查后提交。

request_changes
Reviewer 找到具体问题，需要继续修改。

needs_human
超时、验证失败、高风险或证据不足，停止并交还人工。
```

展示站不把“成功”作为唯一有价值的结果。Vega 的价值还包括准确停止、保留现场和说明为什么
不能继续。

## 六、页面信息结构

V1 使用一张单页页面，不建设多级文档站。主体只有三个阅读块：Hero、真实案例和 Quick
Start。产品边界压缩为案例与 Quick Start 之间的一行短条；顶部导航与 Footer 只承担跳转。

### 1. 顶部导航（辅助）

- 左侧：`Vega`
- 右侧：运行记录、GitHub

### 2. Hero（主体 1）

- 产品名 `Vega` 是首屏最强文字；
- 一句核心承诺和一行证据链；
- `查看真实运行` 与 `开始使用` 两个行动入口；
- 右侧只画 Worker、Evidence、Reviewer 与人工决定的关系，不再复述六个检查点。

### 3. 真实案例与 Finish 面板（主体 2）

#### 主案例 A：AnyIO #1231

用途：展示无中断的标准成功路径。

```text
3 个文件
+23 / -1
相关测试与目标测试通过
Reviewer：approve
Finish：ready_to_commit
```

必须同时显示：

- 这是单案例；
- Issue 已明确期望行为；
- 不代表跨仓库成功率或未知缺陷发现能力。

#### 主案例 B：packaging #1232

用途：展示主机中断后的现场保留和恢复。

```text
Worker 执行期间主机关机
原候选 Diff 保留
恢复后重新建立验证和 Reviewer 证据
5311 passed
Finish：ready_to_commit
```

该案例作为简历和首页最重要的差异化证据。

#### 主案例 C：CRWP-V1-02

用途：展示 timeout 时不伪造成功。

```text
900 秒 timeout
0 个文件被修改
Verification 未启动
Reviewer 未启动
Finish：needs_human
```

不得把它写成“模型修复失败”，只能说明 Vega 按冻结合同停止后续流程并保留现场。

#### 次级案例

Node SemVer 与 Testify 继续保留在追加式证据记录中，不再占用展示站主叙事空间。

#### Finish 交互面板

页面使用一个固定 Finish 面板，点击主案例后切换内容。默认只显示：

```text
当前裁决
验证结果
Reviewer
```

实际变更、确定性 Gate 和证据边界放入“完整证据与边界”折叠区，原始记录保留单独链接。
数据必须来自人工核准的脱敏清单。前端不读取原始 run 目录，也不自行推断 Reviewer 未提供的
行号或结论。

### 4. 边界说明（辅助）

使用一条三项短条，不再建立独立章节或重复产品定位：

- Reviewer 会话隔离不是操作系统级安全沙箱；
- Vega 不自动 commit 或 push；
- `approve` 不能覆盖验证失败。

### 5. Quick Start（主体 3）

只展示标题和首次启动路径；继续运行与 Finish 命令留在 README：

```powershell
python -m pip install -e ".[dev]"
vega config check --repo .
vega loop bug --repo . --text "修复一个边界明确的缺陷" --mode assist
```

详细安装和边界继续链接到仓库 README，不在展示站复制完整文档。

### 6. Footer（辅助）

- GitHub；
- 使用文档；
- MIT License。

## 七、视觉系统

### 总体风格

```text
中文技术长文
+ 工程飞行记录器
+ 手绘技术草图
```

### 色彩

- 页面背景：暖米白；
- 主文字：接近黑色；
- 品牌强调：焦橙色；
- Baseline：蓝色；
- Worker：橙色；
- Gates：黄色；
- Verify：青色；
- Review：洋红色；
- Finish：绿色；
- `needs_human`：琥珀色；
- 明确失败：红色。

颜色不能作为唯一状态信息，所有状态同时显示文字和图标。

### 字体

- 中文标题：`Songti SC`、`Noto Serif SC`、serif；
- 中文正文：`PingFang SC`、`Microsoft YaHei`、sans-serif；
- 命令与状态：`Cascadia Code`、`JetBrains Mono`、monospace。

V1 不依赖 Google Fonts。后续若自带字体，必须核对许可并控制体积。

### 图形

- 使用项目内 SVG；
- 线条保留轻微手绘感，但文字和状态必须清晰；
- 不使用机器人、脑电波、宇宙渐变等通用 AI 插画；
- 不从模型输出直接生成带文字的位图；
- 不依赖外部图床。

### 动效

- 首屏只做一次轻量流程出现动画；
- Finish 案例切换使用短暂淡入；
- 尊重 `prefers-reduced-motion`；
- 不模拟 AI 打字或伪造实时运行。

## 八、技术结构

计划文件：

```text
site/
  index.html
  styles.css
  app.js
  assets/
    vega-flow.svg
    og-image.png
  data/
    cases.json

scripts/
  build_showcase_data.py

.github/workflows/
  pages.yml
```

### 技术选择

- 原生 HTML、CSS 和少量 JavaScript；
- 不增加 Node 构建工具和前端框架；
- `build_showcase_data.py` 只读取一个人工维护的案例清单；
- GitHub Actions 上传静态目录并部署 Pages；
- 不人工维护 `gh-pages` 分支；
- 本地可使用 `python -m http.server` 预览。

### 数据生成原则

`site/data/cases.json` 不从原始 `runs/` 自动抓取。建议维护一个显式白名单：

```json
{
  "id": "packaging-1232",
  "title": "主机中断后的恢复",
  "source_record": "eval/real-world-runs.md",
  "status": "ready_to_commit",
  "changed_files": 3,
  "verification_summary": "5311 passed",
  "review_verdict": "approve",
  "limitations": [
    "单案例",
    "不代表任意中断点均可恢复"
  ]
}
```

生成脚本只做 schema 校验、字段排序和输出，不从 Markdown 中猜测事实。

## 九、公开数据与安全边界

进入展示站的每条案例必须通过人工白名单，并检查：

1. 不含盘符绝对路径、UNC 路径或真实用户主目录；
2. 不含 API Key、Token、Cookie、私钥或环境变量值；
3. 不含原始 Prompt、模型推理、完整工具参数和未经审查的 stdout/stderr；
4. 不含目标仓库未公开文件或本机 ignored artifact；
5. 所有数字都能回到 `eval/`、发布记录或公开提交；
6. `Reviewer 认为` 与确定性事实分开；
7. 失败、跳过、超时和证据不足不得隐藏；
8. 不生成没有来源的行号、结论或成功率。

Pages Workflow 在部署前必须运行：

```powershell
python scripts/build_showcase_data.py --check
python scripts/check_repository_hygiene.py --base-ref origin/main
git diff --check
```

实现阶段再补充静态链接、HTML 语义和页面资源检查。

## 十、实施步骤

### Step 1：内容和视觉骨架

状态：本地完成。

- 建立 `site/`；
- 完成 Hero、真实案例、边界短条、Quick Start 和 Footer；
- 完成桌面和移动端基础布局；
- 不启用 Pages。

退出条件：

- 用户确认首屏、页面顺序和视觉方向；
- 页面不依赖外部图床或第三方脚本；
- 不出现未实现能力。

### Step 2：真实案例与 Finish 面板

状态：本地完成。

- 建立案例白名单和 schema；
- 接入 AnyIO、packaging、CRWP-V1-02；
- 完成 Finish 面板切换；
- 为每项结论显示证据来源和限制。

退出条件：

- 展示数据与 `eval/real-world-runs.md` 一致；
- 不读取原始本机 run；
- 凭据和路径扫描为 `0`。

### Step 3：质量检查

状态：浏览器验收与静态检查已完成；pytest 命令在当前机器未形成可信终态，等待 PR CI
提供正式结果。

- 桌面宽屏、普通笔记本和移动端截图检查；
- 键盘导航、焦点状态、替代文字和颜色对比检查；
- `prefers-reduced-motion` 检查；
- 失效链接和 HTML 结构检查；
- 页面首屏不依赖 JavaScript 才能阅读；
- 主要内容在禁用动画后仍完整。

建议性能目标：

- 不使用前端框架；
- 首屏不加载视频；
- JavaScript 保持在约 `50 KB` 以内；
- 除项目内图片和字体外无运行时第三方请求。

### Step 4：部署

状态：已获授权，等待 PR CI 与合并。

- 新增 `pages.yml`；
- 首次部署到 GitHub Pages；
- 检查项目子路径下的资源引用；
- 检查 Open Graph、页面标题、描述和 favicon；
- 更新 README 与 `docs/README.md` 的展示站入口。

页面已完成验收。部署仍必须使用已核对的 `site/` artifact，并在合并后手工触发现有 Pages
workflow。

## 十一、分支与 PR

计划文档评审期间：

- 不创建实现分支；
- 不提交或推送本计划；
- 不修改主线 Roadmap。

用户确认后：

- 只创建一个短生命周期分支：`feat/github-pages-showcase-v1`；
- 所有 V1 页面、案例数据、检查脚本和 Workflow 都留在该分支；
- 使用一个 PR 完成；
- CI 修正继续留在同一分支，不为小修正创建新分支；
- 合并后删除远端分支。

网站不得与 Runtime、Plan-first 或 Finish 产品逻辑修改混在同一 PR。

## 十二、验收标准

### 访客理解

让没有参与 Vega 开发的人只看展示站，不先读 README，并回答：

1. Vega 解决什么问题；
2. Worker 和 Reviewer 为什么要分开；
3. 哪些事实由测试和 Gate 决定；
4. `ready_to_commit` 与 `needs_human` 有什么区别；
5. Vega 不承诺什么；
6. 如何开始使用。

至少前五项必须能在 3 分钟内准确回答。

### 工程质量

- GitHub Pages 可以从干净 checkout 重建；
- 页面在项目子路径部署时资源完整；
- 无本机路径、凭据、原始 Prompt 或未审查模型输出；
- 案例数据能追溯到现有公开证据；
- `eval/` 没有被修改或重写；
- 移动端和桌面端均可阅读；
- 无阻塞级无障碍和失效链接问题；
- 仓库卫生、差异检查和新增定向测试通过。

### 范围

- 不增加 Vega 命令、状态、Artifact 或成功条件；
- 不增加在线 Runner、用户数据或服务端；
- 不增加长期网站内容管理系统；
- 不把展示需求变成新的 Web 产品路线。

## 十三、停止条件

以下条件满足后，展示站 V1 立即停止新增内容：

1. 单页公开可访问；
2. Worker、证据、Reviewer 与人工决定的关系表达清楚；
3. 三个主案例可以切换查看；
4. Finish 面板能区分事实、Reviewer 意见和证据上限；
5. Quick Start 和 GitHub 入口可用；
6. 移动端、无障碍、隐私和仓库卫生检查通过；
7. README 已加入展示站入口。

V1 发布后进入实际展示观察，不立即增加：

- 博客系统；
- 在线 Demo；
- 多语言完整站；
- 案例后台；
- 用户统计平台；
- 视频课程；
- 新的产品功能。

## 十四、决策状态

以下决策均已确认：

1. 展示站临时排在 Plan-first 实现之前；
2. 使用已确认的首屏开场和说明；
3. 使用 AnyIO、packaging、CRWP-V1-02 作为三个主案例；
4. 使用原生 HTML/CSS/JavaScript，不引入前端框架；
5. V1 只使用默认 GitHub Pages 地址，自定义域名和完整英文版以后再决定。

当前已获准在唯一实现分支完成页面、通过 PR 合并并部署 Pages。网站实现仍不得与 Vega
Runtime、Plan-first 或 Finish 产品逻辑修改混在一起。
