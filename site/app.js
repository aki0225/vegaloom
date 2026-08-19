(() => {
  "use strict";

  document.documentElement.classList.add("js");

  const PLAYBACK_RATE = 4;
  const MIN_EVENT_DELAY_MS = 180;
  const MAX_EVENT_DELAY_MS = 3200;
  const EVIDENCE_REF = "v0.2.0";
  const EVIDENCE_COMMIT = "2fb1bd856df55907a4d3ef1039ea62658b30b2b4";
  const FINAL_RUN_ID = "20260818-231923-agent-resume";
  const RELATED_RUN_ID = "20260818-221144-agent-resume";
  const themeStorageKey = "vega-showcase-theme";
  const reducedMotionQuery = window.matchMedia?.(
    "(prefers-reduced-motion: reduce)",
  );

  const themeToggle = document.querySelector("[data-theme-toggle]");
  const themeLabel = document.querySelector("[data-theme-label]");
  const player = {
    tabs: document.querySelector("[data-player-tabs]"),
    panel: document.querySelector("[data-player-panel]"),
    log: document.querySelector("[data-player-log]"),
    play: document.querySelector("[data-player-play]"),
    playLabel: document.querySelector("[data-player-play-label]"),
    rewind: document.querySelector("[data-player-rewind]"),
    progress: document.querySelector("[data-player-progress]"),
    fill: document.querySelector("[data-player-progress-fill]"),
    current: document.querySelector("[data-player-current]"),
    total: document.querySelector("[data-player-total]"),
    notice: document.querySelector("[data-player-notice]"),
  };
  const playerFields = mappedNodes("playerField");
  const statusNodes = mappedNodes("statusField");
  const evidenceNodes = mappedNodes("evidenceField");
  const replaySources = document.querySelector("[data-player-source-links]");
  const replayLimits = document.querySelector("[data-player-limitations]");
  const primaryNodes = mappedNodes("primaryField");
  const primarySources = document.querySelector("[data-primary-source-links]");
  const moreCases = document.querySelector("[data-more-evidence-list]");
  const secondaryPanel = document.querySelector("[data-secondary-case-panel]");
  const secondaryNodes = mappedNodes("secondaryField");
  const secondarySources = document.querySelector("[data-secondary-source-links]");

  const playback = {
    replay: null,
    cases: [],
    scenarioIndex: 0,
    visibleCount: 0,
    playing: false,
    timer: null,
    reducedMotion: Boolean(reducedMotionQuery?.matches),
  };

  function mappedNodes(datasetKey) {
    return new Map(
      [...document.querySelectorAll(`[data-${toKebab(datasetKey)}]`)].map(
        (node) => [node.dataset[datasetKey], node],
      ),
    );
  }

  function toKebab(value) {
    return value.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
  }

  function setText(nodes, key, value) {
    const node = nodes.get(key);
    if (node && value !== undefined && value !== null) {
      node.textContent = String(value);
    }
  }

  function formatClock(milliseconds) {
    const safe = Math.max(0, Math.round(milliseconds));
    const minutes = Math.floor(safe / 60000);
    const seconds = Math.floor((safe % 60000) / 1000);
    const remainder = safe % 1000;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(
      2,
      "0",
    )}.${String(remainder).padStart(3, "0")}`;
  }

  function evidenceUrl(relativePath, ref = EVIDENCE_REF) {
    const encodedPath = relativePath
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/");
    return `https://github.com/aki0225/vegaloom/blob/${encodeURIComponent(
      ref,
    )}/${encodedPath}`;
  }

  function makeEvidenceLink(source, ref = EVIDENCE_REF) {
    const link = document.createElement("a");
    link.href = evidenceUrl(source.path, ref);
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `${source.label} ↗`;
    return link;
  }

  function applyTheme(theme, persist) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;
    if (themeToggle) {
      const darkMode = nextTheme === "dark";
      themeToggle.setAttribute("aria-pressed", String(darkMode));
      themeToggle.setAttribute(
        "aria-label",
        darkMode ? "切换到浅色模式" : "切换到深色模式",
      );
    }
    if (themeLabel) {
      themeLabel.textContent = nextTheme === "dark" ? "浅色" : "深色";
    }
    if (persist) {
      try {
        window.localStorage.setItem(themeStorageKey, nextTheme);
      } catch {
        // 本地存储不可用时，当前页面仍可切换主题。
      }
    }
  }

  function bindTheme() {
    applyTheme(document.documentElement.dataset.theme, false);
    themeToggle?.addEventListener("click", () => {
      const next =
        document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next, true);
    });
  }

  function currentScenario() {
    return playback.replay?.scenarios?.[playback.scenarioIndex] ?? null;
  }

  function sourceMap() {
    return new Map(
      (playback.replay?.source_links ?? []).map((source) => [
        source.kind,
        source,
      ]),
    );
  }

  function renderPlayerTabs() {
    if (!player.tabs || !playback.replay) {
      return;
    }
    const fragment = document.createDocumentFragment();
    playback.replay.scenarios.forEach((scenario, index) => {
      const button = document.createElement("button");
      const active = index === playback.scenarioIndex;
      button.className = `player-tab${active ? " is-active" : ""}`;
      button.id = `agent-player-tab-${scenario.id}`;
      button.type = "button";
      button.role = "tab";
      button.dataset.scenarioId = scenario.id;
      button.setAttribute("aria-selected", String(active));
      button.setAttribute("aria-controls", "agent-player-panel");
      button.tabIndex = active ? 0 : -1;

      const order = document.createElement("span");
      order.textContent = String(scenario.index).padStart(2, "0");
      const title = document.createElement("strong");
      title.textContent = scenario.label;
      const result = document.createElement("small");
      result.textContent = scenario.result;
      button.append(order, title, result);

      button.addEventListener("click", () => selectScenario(index));
      button.addEventListener("keydown", (event) => {
        let nextIndex = index;
        if (event.key === "ArrowRight") {
          nextIndex = (index + 1) % playback.replay.scenarios.length;
        } else if (event.key === "ArrowLeft") {
          nextIndex =
            (index - 1 + playback.replay.scenarios.length) %
            playback.replay.scenarios.length;
        } else if (event.key === "Home") {
          nextIndex = 0;
        } else if (event.key === "End") {
          nextIndex = playback.replay.scenarios.length - 1;
        } else {
          return;
        }
        event.preventDefault();
        selectScenario(nextIndex, true);
      });
      fragment.append(button);
    });
    player.tabs.replaceChildren(fragment);
  }

  function updatePlayerTabs() {
    player.tabs?.querySelectorAll("[role='tab']").forEach((button, index) => {
      const active = index === playback.scenarioIndex;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
  }

  function waitingStatus(scenario) {
    return {
      phase: "等待播放",
      work_item: "单项任务",
      worker: "等待事件",
      workspace: "等待事件",
      checkpoint: "等待事件",
      verification: "等待事件",
      risk: "等待事件",
      reviewer: "等待事件",
      allowed_actions: [],
      next_step: `播放“${scenario.label}”查看状态变化。`,
      finish: "等待事件",
    };
  }

  function renderStatus(card) {
    const status = card ?? waitingStatus(currentScenario());
    for (const [key, node] of statusNodes) {
      const value =
        key === "allowed_actions" && Array.isArray(status[key])
          ? status[key].join(" · ") || "无"
          : status[key];
      node.textContent = String(value ?? "未知");
    }
    const phase = status.phase ?? "未知";
    const board = document.querySelector("[data-player-status]");
    if (board) {
      board.dataset.phase = phase;
    }
  }

  function renderEvent(event, isCurrent) {
    const article = document.createElement("article");
    article.className = `event-log__entry tone-${event.tone}${
      isCurrent ? " is-current" : ""
    }`;

    const stamp = document.createElement("div");
    stamp.className = "event-log__stamp";
    const time = document.createElement("code");
    time.textContent = formatClock(event.at_ms);
    const type = document.createElement("span");
    type.textContent = event.type.toUpperCase();
    stamp.append(time, type);

    const body = document.createElement("div");
    const heading = document.createElement("h5");
    heading.textContent = event.message;
    const detail = document.createElement("p");
    detail.textContent = event.detail;
    body.append(heading, detail);

    const sources = document.createElement("div");
    sources.className = "event-log__sources";
    const knownSources = sourceMap();
    event.source_refs.forEach((sourceRef) => {
      const source = knownSources.get(sourceRef);
      if (source) {
        sources.append(
          makeEvidenceLink(source),
        );
      }
    });
    if (!sources.childElementCount) {
      const note = document.createElement("span");
      note.className = "event-log__source-note";
      note.textContent = "来源见发布验收";
      sources.append(note);
    }
    body.append(sources);
    article.append(stamp, body);
    return article;
  }

  function renderTimeline() {
    const scenario = currentScenario();
    if (!scenario || !player.log) {
      return;
    }
    if (playback.visibleCount === 0) {
      const waiting = document.createElement("div");
      waiting.className = "event-log__waiting";
      const time = document.createElement("code");
      time.textContent = "00:00.000";
      const text = document.createElement("p");
      text.textContent = playback.reducedMotion
        ? "已按系统偏好展开全部事件。"
        : "等待播放。这里只显示主会话可见的低频事件。";
      waiting.append(time, text);
      player.log.replaceChildren(waiting);
      renderStatus(waitingStatus(scenario));
      return;
    }

    const visible = scenario.events.slice(0, playback.visibleCount);
    player.log.replaceChildren(
      ...visible.map((event, index) =>
        renderEvent(event, index === visible.length - 1),
      ),
    );
    const latest = visible.at(-1);
    renderStatus(latest.status_card);
    if (typeof player.log.scrollTo === "function") {
      player.log.scrollTo({
        top: player.log.scrollHeight,
        behavior: playback.reducedMotion ? "auto" : "smooth",
      });
    } else {
      player.log.scrollTop = player.log.scrollHeight;
    }
  }

  function renderProgress() {
    const scenario = currentScenario();
    if (!scenario) {
      return;
    }
    const latest =
      playback.visibleCount > 0
        ? scenario.events[playback.visibleCount - 1]
        : null;
    const elapsed = latest?.at_ms ?? 0;
    const progress =
      scenario.duration_ms > 0
        ? Math.min(100, (elapsed / scenario.duration_ms) * 100)
        : 0;
    if (player.fill) {
      player.fill.style.width = `${progress}%`;
    }
    if (player.current) {
      player.current.textContent = formatClock(elapsed);
    }
    if (player.total) {
      player.total.textContent = formatClock(scenario.duration_ms);
    }
    if (player.progress) {
      player.progress.setAttribute("aria-valuemax", String(scenario.duration_ms));
      player.progress.setAttribute("aria-valuenow", String(elapsed));
      player.progress.setAttribute(
        "aria-valuetext",
        `${formatClock(elapsed)} / ${formatClock(scenario.duration_ms)}`,
      );
    }
  }

  function renderPlaybackButton() {
    if (!player.play || !player.playLabel) {
      return;
    }
    const scenario = currentScenario();
    const complete =
      scenario && playback.visibleCount >= scenario.events.length;
    player.play.setAttribute("aria-pressed", String(playback.playing));
    const icon = player.play.querySelector("span");
    if (icon) {
      icon.textContent = playback.playing ? "❚❚" : "▶";
    }
    player.playLabel.textContent = playback.playing
      ? "暂停回放"
      : complete
        ? "从头重播"
        : "播放回放";
  }

  function stopPlayback() {
    playback.playing = false;
    if (playback.timer !== null) {
      window.clearTimeout(playback.timer);
      playback.timer = null;
    }
    renderPlaybackButton();
  }

  function scheduleNextEvent() {
    const scenario = currentScenario();
    if (!scenario || !playback.playing) {
      return;
    }
    if (playback.visibleCount >= scenario.events.length) {
      stopPlayback();
      return;
    }

    const previousTime =
      playback.visibleCount === 0
        ? 0
        : scenario.events[playback.visibleCount - 1].at_ms;
    const nextTime = scenario.events[playback.visibleCount].at_ms;
    const virtualGap = Math.max(0, nextTime - previousTime);
    const delay =
      playback.visibleCount === 0
        ? MIN_EVENT_DELAY_MS
        : Math.min(
            MAX_EVENT_DELAY_MS,
            Math.max(MIN_EVENT_DELAY_MS, virtualGap / PLAYBACK_RATE),
          );

    playback.timer = window.setTimeout(() => {
      playback.visibleCount += 1;
      renderTimeline();
      renderProgress();
      renderPlaybackButton();
      scheduleNextEvent();
    }, delay);
  }

  function startPlayback() {
    const scenario = currentScenario();
    if (!scenario || playback.reducedMotion) {
      if (scenario) {
        playback.visibleCount = scenario.events.length;
        renderTimeline();
        renderProgress();
        renderPlaybackButton();
      }
      return;
    }
    if (playback.playing) {
      stopPlayback();
      return;
    }
    if (playback.visibleCount >= scenario.events.length) {
      playback.visibleCount = 0;
      renderTimeline();
      renderProgress();
    }
    playback.playing = true;
    renderPlaybackButton();
    scheduleNextEvent();
  }

  function resetPlayback() {
    stopPlayback();
    const scenario = currentScenario();
    playback.visibleCount =
      playback.reducedMotion && scenario ? scenario.events.length : 0;
    renderTimeline();
    renderProgress();
    renderPlaybackButton();
  }

  function renderScenario() {
    const scenario = currentScenario();
    if (!scenario || !player.panel) {
      return;
    }
    setText(playerFields, "index", String(scenario.index).padStart(2, "0"));
    setText(playerFields, "label", scenario.label);
    setText(playerFields, "title", scenario.title);
    setText(playerFields, "summary", scenario.summary);
    setText(playerFields, "run_ids", scenario.run_ids.join(" · "));
    setText(playerFields, "result", scenario.result);
    player.panel.dataset.result = scenario.result;
    player.panel.setAttribute(
      "aria-labelledby",
      `agent-player-tab-${scenario.id}`,
    );
    updatePlayerTabs();
    resetPlayback();
  }

  function selectScenario(index, focus = false) {
    if (
      !playback.replay ||
      index < 0 ||
      index >= playback.replay.scenarios.length
    ) {
      return;
    }
    playback.scenarioIndex = index;
    renderScenario();
    if (focus) {
      player.tabs
        ?.querySelectorAll("[role='tab']")
        .item(index)
        ?.focus();
    }
  }

  function renderEvidence() {
    const replay = playback.replay;
    if (!replay) {
      return;
    }
    setText(evidenceNodes, "tag", replay.release.tag);
    setText(evidenceNodes, "commit", replay.release.commit.slice(0, 8));
    setText(evidenceNodes, "final_run_id", replay.final_run_id);
    setText(
      evidenceNodes,
      "related_run_ids",
      replay.related_run_ids.join(" · "),
    );
    setText(evidenceNodes, "event_count", replay.proof.event_count);
    setText(evidenceNodes, "sha256", replay.proof.sha256);

    replaySources?.replaceChildren(
      ...replay.source_links.map((source) => makeEvidenceLink(source)),
    );
    replayLimits?.replaceChildren(
      ...replay.limitations.map((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        return item;
      }),
    );
    const disclaimer = document.querySelector(".evidence-disclaimer");
    if (disclaimer) {
      disclaimer.textContent = `${replay.proof.disclosure} 不展示隐藏推理、完整聊天或原始命令参数。`;
    }
  }

  function renderPrimaryCase(caseData) {
    if (!caseData) {
      return;
    }
    setText(primaryNodes, "kind", caseData.kind);
    setText(primaryNodes, "title", caseData.title);
    setText(primaryNodes, "status_label", caseData.status_label);
    setText(primaryNodes, "summary", caseData.summary);
    setText(primaryNodes, "verification", caseData.verification.headline);
    setText(
      primaryNodes,
      "review_verdict",
      `${caseData.review.verdict} · ${caseData.review.severity} finding`,
    );
    setText(primaryNodes, "review_title", caseData.review.title);
    setText(primaryNodes, "finish", caseData.gates.finish);
    primarySources?.replaceChildren(
      ...caseData.source_links
        .filter((source) => ["verification", "review", "finish"].includes(source.kind))
        .map((source) => makeEvidenceLink(source)),
    );
  }

  function renderSecondaryCase(caseData, button) {
    if (!caseData || !secondaryPanel) {
      return;
    }
    const alreadyOpen =
      !secondaryPanel.hidden && secondaryPanel.dataset.caseId === caseData.id;
    moreCases?.querySelectorAll("button").forEach((candidate) => {
      candidate.setAttribute(
        "aria-pressed",
        String(!alreadyOpen && candidate === button),
      );
    });
    if (alreadyOpen) {
      secondaryPanel.hidden = true;
      secondaryPanel.dataset.caseId = "";
      return;
    }
    secondaryPanel.hidden = false;
    secondaryPanel.dataset.caseId = caseData.id;
    setText(secondaryNodes, "kind", caseData.kind);
    setText(secondaryNodes, "title", caseData.title);
    setText(secondaryNodes, "summary", caseData.summary);
    secondarySources?.replaceChildren(
      ...caseData.source_links
        .filter((source) => ["verification", "review", "finish"].includes(source.kind))
        .map((source) => makeEvidenceLink(source)),
    );
  }

  function bindAdditionalEvidence() {
    moreCases?.querySelectorAll("button").forEach((button) => {
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        const caseData = playback.cases.find(
          (item) => item.id === button.dataset.fallbackCase,
        );
        renderSecondaryCase(caseData, button);
      });
    });
  }

  function isNonEmptyString(value) {
    return typeof value === "string" && value.trim().length > 0;
  }

  function isPublicSource(source) {
    return (
      source &&
      isNonEmptyString(source.kind) &&
      isNonEmptyString(source.label) &&
      isNonEmptyString(source.path)
    );
  }

  function isStatusCard(card) {
    const stringFields = [
      "phase",
      "work_item",
      "worker",
      "workspace",
      "checkpoint",
      "verification",
      "risk",
      "reviewer",
      "next_step",
      "finish",
    ];
    return (
      card &&
      stringFields.every((field) => isNonEmptyString(card[field])) &&
      Array.isArray(card.allowed_actions) &&
      card.allowed_actions.every(isNonEmptyString)
    );
  }

  function isReplayScenario(scenario, sourceKinds) {
    return (
      scenario &&
      ["id", "label", "title", "summary", "result"].every((field) =>
        isNonEmptyString(scenario[field]),
      ) &&
      Number.isInteger(scenario.index) &&
      Number.isInteger(scenario.duration_ms) &&
      scenario.duration_ms > 0 &&
      Array.isArray(scenario.run_ids) &&
      scenario.run_ids.length > 0 &&
      scenario.run_ids.every(isNonEmptyString) &&
      Array.isArray(scenario.events) &&
      scenario.events.length > 0 &&
      scenario.events.every(
        (event) =>
          event &&
          ["id", "type", "tone", "message", "detail"].every((field) =>
            isNonEmptyString(event[field]),
          ) &&
          Number.isInteger(event.at_ms) &&
          event.at_ms >= 0 &&
          Array.isArray(event.source_refs) &&
          event.source_refs.length > 0 &&
          event.source_refs.every((sourceRef) => sourceKinds.has(sourceRef)) &&
          isStatusCard(event.status_card),
      )
    );
  }

  function isReviewCase(caseData) {
    return (
      caseData &&
      ["id", "kind", "title", "status_label", "summary"].every((field) =>
        isNonEmptyString(caseData[field]),
      ) &&
      isNonEmptyString(caseData.verification?.headline) &&
      ["verdict", "severity", "title"].every((field) =>
        isNonEmptyString(caseData.review?.[field]),
      ) &&
      isNonEmptyString(caseData.gates?.finish) &&
      Array.isArray(caseData.source_links) &&
      caseData.source_links.every(isPublicSource)
    );
  }

  function validatePublicPayload(payload) {
    const replay = payload?.agent_replay;
    const sourceKinds = new Set(
      Array.isArray(replay?.source_links)
        ? replay.source_links.map((source) => source?.kind)
        : [],
    );
    if (
      !payload ||
      payload.schema_version !== 4 ||
      !replay ||
      replay.release?.tag !== EVIDENCE_REF ||
      replay.release?.commit !== EVIDENCE_COMMIT ||
      replay.final_run_id !== FINAL_RUN_ID ||
      JSON.stringify(replay.related_run_ids) !==
        JSON.stringify([RELATED_RUN_ID]) ||
      !Array.isArray(replay.source_links) ||
      replay.source_links.length === 0 ||
      !replay.source_links.every(isPublicSource) ||
      sourceKinds.size !== replay.source_links.length ||
      !Array.isArray(replay.limitations) ||
      replay.limitations.length === 0 ||
      !replay.limitations.every(isNonEmptyString) ||
      !Number.isInteger(replay.proof?.event_count) ||
      !Number.isInteger(replay.proof?.duration_ms) ||
      !/^[0-9a-f]{64}$/.test(replay.proof?.sha256 ?? "") ||
      !isNonEmptyString(replay.proof?.disclosure) ||
      !Array.isArray(replay.scenarios) ||
      replay.scenarios.length !== 3 ||
      !replay.scenarios.every((scenario) =>
        isReplayScenario(scenario, sourceKinds),
      ) ||
      !Array.isArray(payload.cases) ||
      payload.cases.length < 3 ||
      !payload.cases.every(isReviewCase)
    ) {
      throw new Error("展示数据格式不受支持");
    }
  }

  function setInteractiveControlsDisabled(disabled) {
    player.tabs?.querySelectorAll("button").forEach((button) => {
      button.disabled = disabled;
    });
    if (player.play) {
      player.play.disabled = disabled;
    }
    if (player.rewind) {
      player.rewind.disabled = disabled;
    }
    moreCases?.querySelectorAll("button").forEach((button) => {
      button.disabled = disabled;
    });
  }

  function showStaticFallback(error) {
    console.warn("展示数据读取失败，已保留页面内置摘要。", error);
    stopPlayback();
    setInteractiveControlsDisabled(true);
    if (player.notice) {
      player.notice.textContent =
        "结构化数据暂不可用；当前只显示页面内置摘要，请通过下方链接核对发布证据。";
    }
  }

  async function loadData() {
    let payload;
    try {
      const response = await fetch("data/cases.json", {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      payload = await response.json();
      validatePublicPayload(payload);
    } catch (error) {
      showStaticFallback(error);
      return;
    }

    try {
      playback.replay = payload.agent_replay;
      playback.cases = payload.cases;
      playback.scenarioIndex = 0;
      renderEvidence();
      renderPlayerTabs();
      renderScenario();

      const primary =
        playback.cases.find(
          (item) => item.id === "pycodestyle-1187-rejection",
        ) ?? null;
      if (primary) {
        renderPrimaryCase(primary);
      }
      setInteractiveControlsDisabled(false);
      if (player.notice) {
        player.notice.textContent =
          "4× 虚拟 timecode；只重放低频状态事件，不连接实时终端。";
      }
    } catch (error) {
      showStaticFallback(error);
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

  player.play?.addEventListener("click", startPlayback);
  player.rewind?.addEventListener("click", resetPlayback);
  reducedMotionQuery?.addEventListener("change", (event) => {
    playback.reducedMotion = event.matches;
    resetPlayback();
  });

  bindTheme();
  bindAdditionalEvidence();
  bindCopyButton();
  loadData();
})();
