(() => {
  "use strict";

  document.documentElement.classList.add("js");

  const caseButtons = Array.from(document.querySelectorAll("[data-case-id]"));
  const finishPanel = document.querySelector("#finish-panel");
  const fieldNodes = new Map(
    Array.from(document.querySelectorAll("[data-case-field]")).map((node) => [
      node.dataset.caseField,
      node,
    ]),
  );

  function setText(field, value) {
    const node = fieldNodes.get(field);
    if (node && typeof value === "string") {
      node.textContent = value;
    }
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
      setText("change_summary", caseData.change_summary);
      setText("gate_summary", caseData.gate_summary);
      setText("verification_summary", caseData.verification_summary);
      setText("reviewer_summary", caseData.reviewer_summary);
      setText("evidence_limit", caseData.evidence_limit);

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
      if (payload.schema_version !== 1 || !Array.isArray(payload.cases)) {
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

  loadCases();
  bindCopyButton();
})();
