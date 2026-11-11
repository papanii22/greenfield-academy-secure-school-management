(() => {
  if (typeof Chart === "undefined") {
    console.error("The local Chart.js asset did not load.");
    return;
  }

  const charts = [];
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function themeColors() {
    const styles = getComputedStyle(document.documentElement);
    return {
      text: styles.getPropertyValue("--text").trim(),
      muted: styles.getPropertyValue("--muted").trim(),
      primary: styles.getPropertyValue("--primary").trim(),
      success: styles.getPropertyValue("--success").trim(),
      warning: styles.getPropertyValue("--warning").trim(),
      danger: styles.getPropertyValue("--danger").trim(),
      info: styles.getPropertyValue("--info").trim(),
      grid: styles.getPropertyValue("--surface-border").trim(),
      chart: [1, 2, 3, 4, 5].map((n) => styles.getPropertyValue(`--chart-${n}`).trim()),
    };
  }

  function createChart(canvas, colors) {
    const chartData = JSON.parse(canvas.dataset.chart);
    // An optional "palette" picks chart colours by index, e.g. [1, 0].
    const palette = Array.isArray(chartData.palette)
      ? [
          ...chartData.palette.map((n) => colors.chart[n % colors.chart.length]),
          ...colors.chart,
        ]
      : colors.chart;
    const isLine = chartData.type === "line";
    const isDoughnut = chartData.type === "doughnut";
    const horizontal = chartData.indexAxis === "y";
    const datasets = chartData.datasets.map((dataset, index) => {
      // Lines lead with yellow and bars with sky, as in the dashboard design.
      const offset = isLine && !chartData.palette ? 1 : 0;
      const color = palette[(index + offset) % palette.length];
      return {
        ...dataset,
        borderColor: dataset.borderColor || color,
        backgroundColor: isDoughnut ? palette : isLine ? `${color}33` : color,
        fill: isLine,
        borderWidth: isLine ? 3 : 0,
        borderRadius: chartData.type === "bar" ? 8 : undefined,
        maxBarThickness: chartData.type === "bar" ? 36 : undefined,
        tension: chartData.type === "line" ? 0.4 : undefined,
        pointRadius: chartData.type === "line" ? 2 : undefined,
        pointHoverRadius: chartData.type === "line" ? 4 : undefined,
      };
    });

    const valueAxis = {
      beginAtZero: true,
      max: chartData.max,
      ticks: { color: colors.muted },
      grid: { color: colors.grid },
    };
    const categoryAxis = {
      ticks: { color: colors.muted },
      grid: { color: colors.grid, display: !horizontal },
    };
    const doughnutOptions = isDoughnut
      ? {
          cutout: chartData.cutout || "50%",
          ...(chartData.half ? { circumference: 180, rotation: -90 } : {}),
        }
      : {};

    return new Chart(canvas, {
      type: chartData.type,
      data: {
        labels: chartData.labels,
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: horizontal ? "y" : "x",
        animation: { duration: reducedMotion ? 0 : 450 },
        ...doughnutOptions,
        plugins: {
          legend: {
            display:
              chartData.legend === false
                ? false
                : chartData.type === "doughnut" || datasets.length > 1,
            labels: { color: colors.text, usePointStyle: true, padding: 18 },
          },
          tooltip: { enabled: true },
        },
        scales: isDoughnut
          ? {}
          : horizontal
            ? { x: valueAxis, y: categoryAxis }
            : { x: categoryAxis, y: valueAxis },
      },
    });
  }

  function initializeCharts() {
    const colors = themeColors();
    Chart.defaults.color = colors.text;
    document.querySelectorAll("canvas[data-chart]").forEach((canvas) => {
      charts.push(createChart(canvas, colors));
    });
  }

  initializeCharts();

  document.addEventListener("greenfield:theme-change", () => {
    const colors = themeColors();
    Chart.defaults.color = colors.text;
    charts.forEach((chart) => {
      chart.options.plugins.legend.labels.color = colors.text;
      if (chart.options.scales.x) {
        chart.options.scales.x.ticks.color = colors.muted;
        chart.options.scales.x.grid.color = colors.grid;
        chart.options.scales.y.ticks.color = colors.muted;
        chart.options.scales.y.grid.color = colors.grid;
      }
      chart.update("none");
    });
  });
})();
