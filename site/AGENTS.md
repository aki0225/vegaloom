# Site 规则

- `index.html`、`styles.css`、`app.js` 和 `assets/` 是静态展示实现；保持无构建框架和无运行时服务。
- `data/cases.json` 由 `../scripts/build_showcase_data.py` 生成并校验，不直接手改生成结果。
- 展示内容只能来自已登记的脱敏公开证据，不读取本地 `../runs/`、`../.env`、`../memory/`
  或私有仓库。
- 页面不得把 Reviewer approve、测试通过或实验个案描述成生产安全证明。
- 修改案例数据、Schema 或生成器时运行 `python scripts/build_showcase_data.py --check` 和
  `python -m pytest -q tests/experimental/test_showcase_data.py`。只改 HTML、CSS 或交互展示时
  运行对应静态检查和 `git diff --check`，不机械重跑数据生成测试。
