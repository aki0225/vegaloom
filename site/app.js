(() => {
  "use strict";

  document.documentElement.classList.add("js");

  const themeStorageKey = "vega-showcase-theme";
  const themeToggle = document.querySelector("[data-theme-toggle]");
  const themeLabel = document.querySelector("[data-theme-label]");

  const replayButtons = Array.from(
    document.querySelectorAll("[data-replay-id]"),
  );
  const replayRail = document.querySelector(".replay-rail");
  const replayPanel = document.querySelector("#replay-panel");
  const replayPlayButton = document.querySelector("[data-replay-play]");
  const replayPlayLabel = document.querySelector("[data-replay-play-label]");
  const replayRun = document.querySelector("[data-replay-run]");
  const replayLinks = document.querySelector("[data-replay-links]");
  const replayFields = new Map(
    Array.from(document.querySelectorAll("[data-replay-field]")).map((node) => [
      node.dataset.replayField,
      node,
    ]),
  );

  const caseButtons = Array.from(document.querySelectorAll("[data-case-id]"));
  const finishPanel = document.querySelector("#finish-panel");
  const diffCode = document.querySelector("[data-diff-code]");
  const verificationList = document.querySelector("[data-verification-list]");
  const limitationsList = document.querySelector("[data-limitations-list]");
  const sourceLinks = document.querySelector("[data-source-links]");
  const caseFields = new Map(
    Array.from(document.querySelectorAll("[data-case-field]")).map((node) => [
      node.dataset.caseField,
      node,
    ]),
  );

  let replaySteps = [];
  let replayTimer = null;
  let replayIndex = 0;

  function bindReplayOrientation() {
    if (!replayRail || typeof window.matchMedia !== "function") {
      return;
    }

    const compactLayout = window.matchMedia("(max-width: 820px)");
    const update = () => {
      replayRail.setAttribute(
        "aria-orientation",
        compactLayout.matches ? "horizontal" : "vertical",
      );
    };
    update();
    if (typeof compactLayout.addEventListener === "function") {
      compactLayout.addEventListener("change", update);
    } else if (typeof compactLayout.addListener === "function") {
      // 兼容仍使用旧 MediaQueryList 监听接口的浏览器。
      compactLayout.addListener(update);
    }
  }

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

  function setMappedText(fields, field, value) {
    const node = fields.get(field);
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

  function evidenceUrl(relativePath) {
    const encodedPath = relativePath
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/");
    return `https://github.com/aki0225/vegaloom/blob/main/${encodedPath}`;
  }

  function makeEvidenceLink(label, relativePath) {
    const link = document.createElement("a");
    link.href = evidenceUrl(relativePath);
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${label} ↗`;
    return link;
  }

  function renderReplayLinks(sources) {
    if (!replayLinks || !Array.isArray(sources)) {
      return;
    }

    replayLinks.replaceChildren(
      ...sources.map((source) =>
        makeEvidenceLink(source.label, source.path),
      ),
    );
  }

  function renderReplay(step, button, options = {}) {
    if (!replayPanel || !step || !button) {
      return;
    }

    const update = () => {
      setMappedText(replayFields, "index", step.index);
      setMappedText(replayFields, "phase", step.phase);
      setMappedText(replayFields, "status", step.status);
      setMappedText(replayFields, "label", step.label);
      setMappedText(replayFields, "title", step.title);
      setMappedText(replayFields, "observation", step.observation);
      setMappedText(replayFields, "decision", step.decision);

      replayPanel.dataset.replayStatus = step.status;
      replayPanel.setAttribute("aria-labelledby", button.id);

      replayButtons.forEach((candidate) => {
        const active = candidate === button;
        candidate.classList.toggle("is-active", active);
        candidate.setAttribute("aria-selected", String(active));
        candidate.tabIndex = active ? 0 : -1;
      });

      replayPanel.classList.remove("is-switching");
      if (options.focus) {
        button.focus();
      }
    };

    replayPanel.classList.add("is-switching");
    window.setTimeout(update, options.immediate ? 0 : 90);
  }

  function stopReplayPlayback(completed = false) {
    if (replayTimer !== null) {
      window.clearInterval(replayTimer);
      replayTimer = null;
    }
    if (!replayPlayButton || !replayPlayLabel) {
      return;
    }

    replayPlayButton.setAttribute("aria-pressed", "false");
    const icon = replayPlayButton.querySelector("span");
    if (icon) {
      icon.textContent = "▶";
    }
    replayPlayLabel.textContent = completed ? "重新播放" : "播放这条 Run";
  }

  function activateReplayIndex(nextIndex, options = {}) {
    if (!replaySteps.length || !replayButtons.length) {
      return;
    }

    replayIndex =
      (nextIndex + replaySteps.length) % replaySteps.length;
    renderReplay(
      replaySteps[replayIndex],
      replayButtons[replayIndex],
      options,
    );
  }

  function startReplayPlayback() {
    if (!replayPlayButton || !replayPlayLabel || !replaySteps.length) {
      return;
    }

    if (replayTimer !== null) {
      stopReplayPlayback(false);
      return;
    }

    if (replayIndex >= replaySteps.length - 1) {
      activateReplayIndex(0);
    }

    replayPlayButton.setAttribute("aria-pressed", "true");
    const icon = replayPlayButton.querySelector("span");
    if (icon) {
      icon.textContent = "❚❚";
    }
    replayPlayLabel.textContent = "暂停回放";

    replayTimer = window.setInterval(() => {
      if (replayIndex >= replaySteps.length - 1) {
        stopReplayPlayback(true);
        return;
      }
      activateReplayIndex(replayIndex + 1);
    }, 2400);
  }

  function bindReplay(replay) {
    if (
      !replay ||
      !Array.isArray(replay.steps) ||
      replay.steps.length !== replayButtons.length
    ) {
      throw new Error("Agent 回放节点与页面结构不一致");
    }

    replaySteps = replay.steps;
    if (replayRun && typeof replay.run_id === "string") {
      replayRun.textContent = replay.run_id;
    }
    renderReplayLinks(replay.source_links);

    const stepsById = new Map(
      replay.steps.map((step) => [step.id, step]),
    );

    replayButtons.forEach((button, index) => {
      button.addEventListener("click", () => {
        const step = stepsById.get(button.dataset.replayId);
        if (!step) {
          return;
        }
        stopReplayPlayback(false);
        replayIndex = index;
        renderReplay(step, button);
      });

      button.addEventListener("keydown", (event) => {
        const currentIndex = replayButtons.indexOf(button);
        let nextIndex = currentIndex;

        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          nextIndex = (currentIndex + 1) % replayButtons.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          nextIndex =
            (currentIndex - 1 + replayButtons.length) % replayButtons.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = replayButtons.length - 1;
        } else {
          return;
        }

        event.preventDefault();
        stopReplayPlayback(false);
        activateReplayIndex(nextIndex, { focus: true });
      });
    });

    if (replayPlayButton) {
      replayPlayButton.addEventListener("click", startReplayPlayback);
    }

    renderReplay(replay.steps[0], replayButtons[0], { immediate: true });
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
      setMappedText(caseFields, "kind", caseData.kind);
      setMappedText(caseFields, "status", caseData.status);
      setMappedText(caseFields, "status_label", caseData.status_label);
      setMappedText(caseFields, "summary", caseData.summary);
      setMappedText(caseFields, "diff_file", caseData.diff.file);
      setMappedText(caseFields, "diff_summary", caseData.diff.summary);
      renderDiff(caseData.diff.excerpt);
      setMappedText(
        caseFields,
        "verification_headline",
        caseData.verification.headline,
      );
      setMappedText(caseFields, "review_verdict", caseData.review.verdict);
      setMappedText(
        caseFields,
        "review_severity",
        caseData.review.severity === "none"
          ? "0 findings"
          : `${caseData.review.severity} finding`,
      );
      setMappedText(caseFields, "review_title", caseData.review.title);
      setMappedText(caseFields, "review_evidence", caseData.review.evidence);
      setMappedText(
        caseFields,
        "review_recommendation",
        caseData.review.recommendation,
      );
      setMappedText(caseFields, "gate_scope", caseData.gates.scope);
      setMappedText(caseFields, "gate_risk", caseData.gates.risk);
      setMappedText(caseFields, "gate_finish", caseData.gates.finish);
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
    }, 90);
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
          nextIndex =
            (currentIndex - 1 + caseButtons.length) % caseButtons.length;
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

  function showDegradedNotice(anchor, text) {
    if (!anchor) {
      return;
    }
    const notice = document.createElement("p");
    notice.className = "noscript-note";
    notice.textContent = text;
    anchor.insertAdjacentElement("afterend", notice);
  }

  async function loadShowcaseData() {
    if (
      (!caseButtons.length || !finishPanel) &&
      (!replayButtons.length || !replayPanel)
    ) {
      return;
    }

    try {
      const response = await fetch("data/cases.json", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`展示数据请求失败：HTTP ${response.status}`);
      }

      const payload = await response.json();
      if (payload.schema_version !== 3) {
        throw new Error("展示数据格式不受支持");
      }

      try {
        bindReplay(payload.agent_replay);
      } catch (error) {
        console.warn(error);
        showDegradedNotice(
          replayPanel,
          "Agent 回放暂不可用，页面已保留首个节点和原始证据链接。",
        );
      }

      try {
        if (!Array.isArray(payload.cases)) {
          throw new Error("Reviewer 案例格式不受支持");
        }
        bindCaseTabs(payload.cases);
      } catch (error) {
        console.warn(error);
        showDegradedNotice(
          finishPanel,
          "案例切换暂不可用，页面已保留首个 Reviewer 案例和原始证据链接。",
        );
      }
    } catch (error) {
      console.warn(error);
      showDegradedNotice(
        replayPanel,
        "展示数据暂不可用，页面已保留首个 Agent 节点和静态证据。",
      );
      showDegradedNotice(
        finishPanel,
        "展示数据暂不可用，页面已保留首个 Reviewer 案例和原始证据链接。",
      );
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
  bindReplayOrientation();
  loadShowcaseData();
  bindCopyButton();
})();
