(function () {
  "use strict";

  const POLL_INTERVAL_MS = 5000;
  const HISTORY_LIMIT = 250;
  const DEFAULT_API_URL = "http://localhost:8000";
  const numberFormat = new Intl.NumberFormat("vi-VN");
  let stepsChart;
  let gaitChart;
  let polling = false;

  function formatMetric(value, digits) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
      return "—";
    }
    return new Intl.NumberFormat("vi-VN", {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    }).format(Number(value));
  }

  function toDateLabel(record) {
    if (!record.timestamp_utc) {
      return "Không rõ";
    }
    const date = new Date(record.timestamp_utc);
    return Number.isNaN(date.getTime())
      ? "Không rõ"
      : date.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
  }

  function hasGaitData(record) {
    const gait = record.gait || {};
    return [
      gait.cadence_steps_per_minute,
      gait.mean_step_interval_ms,
      gait.variability_cv_percent,
    ].some(function (value) {
      return value !== null && value !== undefined && Number.isFinite(Number(value));
    });
  }

  function setEmptyState(id, isEmpty) {
    document.getElementById(id).hidden = !isEmpty;
  }

  function updateStats(records) {
    const latest = records.length ? records[records.length - 1] : null;
    const gait = latest ? latest.gait || {} : {};
    document.getElementById("steps-value").textContent = latest
      ? numberFormat.format(latest.steps)
      : "—";
    document.getElementById("cadence-value").textContent = formatMetric(
      gait.cadence_steps_per_minute,
      1
    );
    document.getElementById("interval-value").textContent = formatMetric(
      gait.mean_step_interval_ms,
      0
    );
    document.getElementById("variability-value").textContent = formatMetric(
      gait.variability_cv_percent,
      1
    );
    document.getElementById("current-date").textContent =
      new Date().toLocaleDateString("vi-VN", {
        day: "2-digit",
        month: "long",
        year: "numeric",
      });

    const hasFall = records.some(function (record) {
      return record.event === "FALL_DETECTED";
    });
    const status = document.getElementById("fall-status");
    if (!hasFall) {
      status.classList.remove("alert");
      status.innerHTML = "<i></i> Chưa có sự kiện";
    }
  }

  function drawStepsChart(records) {
    if (!stepsChart) {
      return;
    }
    const labels = records.map(toDateLabel);
    stepsChart.data.labels = labels;
    stepsChart.data.datasets[0].data = records.map(function (record) {
      return Number(record.steps) || 0;
    });
    stepsChart.update("none");
    setEmptyState("steps-empty", records.length === 0);
  }

  function drawGaitChart(records) {
    if (!gaitChart) {
      return;
    }
    const measured = records.filter(hasGaitData);
    gaitChart.data.labels = measured.map(toDateLabel);
    gaitChart.data.datasets[0].data = measured.map(function (record) {
      return record.gait.cadence_steps_per_minute ?? null;
    });
    gaitChart.data.datasets[1].data = measured.map(function (record) {
      return record.gait.mean_step_interval_ms ?? null;
    });
    gaitChart.data.datasets[2].data = measured.map(function (record) {
      return record.gait.variability_cv_percent ?? null;
    });
    gaitChart.update("none");
    setEmptyState("gait-empty", measured.length === 0);
  }

  function renderTelemetry(records) {
    const ordered = records.slice().sort(function (left, right) {
      return new Date(left.timestamp_utc || 0) - new Date(right.timestamp_utc || 0);
    });
    updateStats(ordered);
    drawStepsChart(ordered);
    drawGaitChart(ordered);
    window.FallMap.renderLocations(ordered);
  }

  function setConnectionState(state, message) {
    const dot = document.getElementById("connection-dot");
    dot.classList.toggle("connected", state === "connected");
    dot.classList.toggle("error", state === "error");
    document.getElementById("connection-label").textContent = message;
  }

  function getApiUrl() {
    const input = document.getElementById("api-url");
    const saved = localStorage.getItem("gaitwatch-api-url");
    const url = (saved || input.value || DEFAULT_API_URL).trim();
    return url.replace(/\/+$/, "");
  }

  async function loadTelemetry() {
    if (polling) {
      return;
    }
    polling = true;
    try {
      const response = await fetch(
        getApiUrl() + "/api/telemetry/history?limit=" + HISTORY_LIMIT,
        { headers: { Accept: "application/json" }, cache: "no-store" }
      );
      if (!response.ok) {
        throw new Error("Backend trả về HTTP " + response.status);
      }
      const records = await response.json();
      if (!Array.isArray(records)) {
        throw new Error("Phản hồi lịch sử telemetry không hợp lệ.");
      }
      renderTelemetry(records);
      setConnectionState("connected", "Đã kết nối · cập nhật mỗi 5 giây");
      document.getElementById("last-updated").textContent =
        "Cập nhật lúc " +
        new Date().toLocaleTimeString("vi-VN", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        });
    } catch (error) {
      setConnectionState("error", error.message || "Không thể kết nối backend");
    } finally {
      polling = false;
    }
  }

  function createCharts() {
    if (!window.Chart) {
      setConnectionState("error", "Không tải được thư viện biểu đồ Chart.js");
      return;
    }
    const commonOptions = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { intersect: false, mode: "index" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#202b3d",
          padding: 10,
          titleFont: { family: "DM Sans", size: 10 },
          bodyFont: { family: "DM Sans", size: 10 },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { display: false },
          ticks: { color: "#9aa5b5", font: { family: "DM Sans", size: 9 }, maxTicksLimit: 7 },
        },
        y: {
          beginAtZero: true,
          border: { display: false, dash: [3, 4] },
          grid: { color: "#edf0f5", drawTicks: false },
          ticks: { color: "#9aa5b5", font: { family: "DM Sans", size: 9 }, maxTicksLimit: 5 },
        },
      },
    };

    stepsChart = new Chart(document.getElementById("steps-chart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [{
          label: "Tổng số bước",
          data: [],
          borderColor: "#5278f4",
          backgroundColor: "rgba(82, 120, 244, 0.11)",
          borderWidth: 2,
          fill: true,
          tension: 0.35,
          pointRadius: 2,
          pointHoverRadius: 4,
          pointBackgroundColor: "#ffffff",
          pointBorderWidth: 2,
        }],
      },
      options: commonOptions,
    });

    gaitChart = new Chart(document.getElementById("gait-chart"), {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Cadence (bước/phút)",
            data: [],
            borderColor: "#2daf87",
            backgroundColor: "#2daf87",
            yAxisID: "y",
            borderWidth: 2,
            tension: 0.35,
            pointRadius: 2,
          },
          {
            label: "Khoảng bước (ms)",
            data: [],
            borderColor: "#e9a23b",
            backgroundColor: "#e9a23b",
            yAxisID: "y1",
            borderWidth: 2,
            tension: 0.35,
            pointRadius: 2,
          },
          {
            label: "Biến thiên CV (%)",
            data: [],
            borderColor: "#9670e8",
            backgroundColor: "#9670e8",
            yAxisID: "y2",
            borderWidth: 2,
            tension: 0.35,
            pointRadius: 2,
          },
        ],
      },
      options: {
        ...commonOptions,
        scales: {
          ...commonOptions.scales,
          y: { ...commonOptions.scales.y, position: "left" },
          y1: {
            ...commonOptions.scales.y,
            position: "right",
            grid: { drawOnChartArea: false },
          },
          y2: {
            display: false,
            beginAtZero: true,
            position: "right",
          },
        },
      },
    });
  }

  function initialize() {
    const input = document.getElementById("api-url");
    input.value = localStorage.getItem("gaitwatch-api-url") || DEFAULT_API_URL;
    document.getElementById("api-config-form").addEventListener("submit", function (event) {
      event.preventDefault();
      localStorage.setItem("gaitwatch-api-url", input.value.trim().replace(/\/+$/, ""));
      loadTelemetry();
    });
    document.getElementById("refresh-button").addEventListener("click", loadTelemetry);

    if (window.FallMap) {
      window.FallMap.initialize();
    } else {
      document.getElementById("map-empty").textContent =
        "Không tải được thư viện bản đồ Leaflet.";
    }
    createCharts();
    loadTelemetry();
    window.setInterval(loadTelemetry, POLL_INTERVAL_MS);
  }

  document.addEventListener("DOMContentLoaded", initialize);
})();
