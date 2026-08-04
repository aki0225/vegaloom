(() => {
  "use strict";

  document.documentElement.classList.add("js");

  const themeStorageKey = "vega-showcase-theme";
  const themeToggle = document.querySelector("[data-theme-toggle]");
  const themeLabel = document.querySelector("[data-theme-label]");
  const caseButtons = Array.from(document.querySelectorAll("[data-case-id]"));
  const finishPanel = document.querySelector("#finish-panel");
  const diffCode = document.querySelector("[data-diff-code]");
  const verificationList = document.querySelector("[data-verification-list]");
  const limitationsList = document.querySelector("[data-limitations-list]");
  const sourceLinks = document.querySelector("[data-source-links]");
  const fieldNodes = new Map(
    Array.from(document.querySelectorAll("[data-case-field]")).map((node) => [
      node.dataset.caseField,
      node,
    ]),
  );

  function applyTheme(theme, persist) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;

    if (themeToggle) {
      const darkMode = nextTheme === "dark";
      themeToggle.setAttribute("aria-pressed", String(darkMode));
      themeToggle.setAttribute(
        "aria-label",
        darkMode ? "切换到浅色模式" : "切换到黑夜模式",
      );
    }
    if (themeLabel) {
      themeLabel.textContent = nextTheme === "dark" ? "浅色" : "黑夜";
    }

    if (persist) {
      try {
        window.localStorage.setItem(themeStorageKey, nextTheme);
      } catch {
        // 浏览器禁用本地存储时，主题切换仍对当前页面有效。
      }
    }
  }

  function bindThemeToggle() {
    if (!themeToggle) {
      return;
    }

    applyTheme(document.documentElement.dataset.theme, false);
    themeToggle.addEventListener("click", () => {
      const nextTheme =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(nextTheme, true);
    });
  }

  function setText(field, value) {
    const node = fieldNodes.get(field);
    if (node && typeof value === "string") {
      node.textContent = value;
    }
  }

  function renderList(node, values) {
    if (!node || !Array.isArray(values)) {
      return;
    }

    node.replaceChildren(
      ...values.map((value) => {
        const item = document.createElement("li");
        item.textContent = value;
        return item;
      }),
    );
  }

  function renderDiff(excerpt) {
    if (!diffCode || typeof excerpt !== "string") {
      return;
    }

    const fragment = document.createDocumentFragment();
    excerpt.split("\n").forEach((line) => {
      const node = document.createElement("span");
      node.className = "diff-line";
      if (line.startsWith("+")) {
        node.classList.add("diff-line--added");
      } else if (line.startsWith("-")) {
        node.classList.add("diff-line--removed");
      }
      node.textContent = line || " ";
      fragment.append(node);
    });
    diffCode.replaceChildren(fragment);
  }

  function evidenceUrl(relativePath) {
    const encodedPath = relativePath
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/");
    return `https://github.com/aki0225/vegaloom/blob/main/${encodedPath}`;
  }

  function renderSourceLinks(caseData) {
    if (!sourceLinks || !Array.isArray(caseData.source_links)) {
      return;
    }

    const sources = [
      {
        label: "Issue",
        href: caseData.issue_url,
      },
      ...caseData.source_links.map((source) => ({
        label: source.label,
        href: evidenceUrl(source.path),
      })),
    ];

    sourceLinks.replaceChildren(
      ...sources.map((source) => {
        const link = document.createElement("a");
        link.href = source.href;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = `${source.label} ↗`;
        return link;
      }),
    );
  }

  function selectCase(caseData, button) {
    if (!finishPanel) {
      return;
    }

    finishPanel.classList.add("is-switching");
    window.setTimeout(() => {
      setText("kind", caseData.kind);
      setText("status", caseData.status);
      setText("status_label", caseData.status_label);
      setText("summary", caseData.summary);
      setText("diff_file", caseData.diff.file);
      setText("diff_summary", caseData.diff.summary);
      renderDiff(caseData.diff.excerpt);
      setText("verification_headline", caseData.verification.headline);
      setText("review_verdict", caseData.review.verdict);
      setText(
        "review_severity",
        caseData.review.severity === "none"
          ? "0 findings"
          : `${caseData.review.severity} finding`,
      );
      setText("review_title", caseData.review.title);
      setText("review_evidence", caseData.review.evidence);
      setText("review_recommendation", caseData.review.recommendation);
      setText("gate_scope", caseData.gates.scope);
      setText("gate_risk", caseData.gates.risk);
      setText("gate_finish", caseData.gates.finish);
      renderList(verificationList, caseData.verification.checks);
      renderList(limitationsList, caseData.limitations);
      renderSourceLinks(caseData);

      finishPanel.dataset.status = caseData.status;
      finishPanel.setAttribute("aria-labelledby", button.id);

      caseButtons.forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", String(active));
        candidate.tabIndex = active ? 0 : -1;
      });
      finishPanel.classList.remove("is-switching");
    }, 100);
  }

  function bindCaseTabs(cases) {
    const casesById = new Map(cases.map((item) => [item.id, item]));

    caseButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const caseData = casesById.get(button.dataset.caseId);
        if (caseData) {
          selectCase(caseData, button);
        }
      });

      button.addEventListener("keydown", (event) => {
        const currentIndex = caseButtons.indexOf(button);
        let nextIndex = currentIndex;

        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (currentIndex + 1) % caseButtons.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex = (currentIndex - 1 + caseButtons.length) % caseButtons.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = caseButtons.length - 1;
        } else {
          return;
        }

        event.preventDefault();
        caseButtons[nextIndex].focus();
        caseButtons[nextIndex].click();
      });
    });
  }

  async function loadCases() {
    if (!caseButtons.length || !finishPanel) {
      return;
    }

    try {
      const response = await fetch("data/cases.json", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`案例数据请求失败：HTTP ${response.status}`);
      }

      const payload = await response.json();
      if (payload.schema_version !== 2 || !Array.isArray(payload.cases)) {
        throw new Error("案例数据格式不受支持");
      }
      bindCaseTabs(payload.cases);
    } catch (error) {
      console.warn(error);
      const notice = document.createElement("p");
      notice.className = "noscript-note";
      notice.textContent = "案例切换暂不可用，当前保留首个案例和原始证据链接。";
      finishPanel.insertAdjacentElement("afterend", notice);
    }
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
      throw new Error("浏览器拒绝复制命令");
    }
  }

  function bindCopyButton() {
    const button = document.querySelector("[data-copy-quickstart]");
    const code = document.querySelector("[data-quickstart-code]");
    const status = document.querySelector("[data-copy-status]");
    if (!button || !code || !status) {
      return;
    }

    button.addEventListener("click", async () => {
      try {
        await copyText(code.textContent.trim());
        status.textContent = "命令已复制。";
      } catch (error) {
        console.warn(error);
        status.textContent = "复制失败，请手动选择命令。";
      }
    });
  }

  bindThemeToggle();
  loadCases();
  bindCopyButton();
})();
