/* FCU-MIS charts (Chart.js). Each <canvas data-fcu-chart="kind" data-source="json-script-id">
   is drawn from the data the page embeds. Thin marks, rounded bar ends, recessive grid,
   a legend for two or more series, tooltips on hover, and a "view as table" fallback. */
(function () {
  "use strict";

  if (typeof Chart === "undefined") { return; }

  var css = getComputedStyle(document.documentElement);
  var token = function (name) { return css.getPropertyValue(name).trim(); };

  var SURFACE = token("--fcu-chart-surface") || "#ffffff";
  var GRID = token("--fcu-chart-grid") || "#e6e9e8";
  var AXIS = token("--fcu-chart-axis") || "#5b6c6a";
  var SERIES = [token("--fcu-series-1"), token("--fcu-series-2"), token("--fcu-series-3")];
  var OTHER = token("--fcu-series-other");

  var ugx = function (v) { return "UGX " + Math.round(v).toLocaleString("en-US"); };
  var short = function (v) {
    if (v >= 1e6) { return (v / 1e6).toFixed(v % 1e6 ? 1 : 0) + "M"; }
    if (v >= 1e3) { return Math.round(v / 1e3) + "K"; }
    return String(v);
  };

  Chart.defaults.font.family = '"Inter", -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 12;
  Chart.defaults.color = AXIS;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.animation.duration = 400;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.tooltip.backgroundColor = "#1f2a29";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 6;

  var moneyTooltip = {
    callbacks: {
      label: function (ctx) {
        var value = ctx.parsed.y !== undefined && ctx.chart.options.indexAxis !== "y" ? ctx.parsed.y : ctx.parsed.x;
        if (ctx.chart.config.type === "doughnut") { value = ctx.parsed; }
        return " " + ctx.dataset.label + ": " + ugx(value);
      }
    }
  };

  var moneyAxis = function (axis) {
    return {
      grid: { color: GRID, drawTicks: false },
      border: { display: false },
      ticks: { callback: function (v) { return short(v); }, padding: 8 },
      beginAtZero: true,
      axis: axis
    };
  };

  var quietAxis = function () {
    return { grid: { display: false }, border: { color: GRID }, ticks: { padding: 6 } };
  };

  var builders = {

    /* Deposits collected each month against the monthly target (bars + a dashed target line). */
    monthly: function (data) {
      var datasets = [{
        type: "bar", label: data.label || "Deposits collected", data: data.collected,
        backgroundColor: SERIES[0], borderRadius: { topLeft: 4, topRight: 4 }, maxBarThickness: 34,
        order: 2
      }];
      if (data.target) {
        datasets.push({
          type: "line", label: data.targetLabel || "Monthly target", data: data.target,
          borderColor: SERIES[1], borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 5,
          pointBackgroundColor: SERIES[1], pointBorderColor: SURFACE, pointBorderWidth: 2, order: 1
        });
      }
      return {
        data: { labels: data.labels, datasets: datasets },
        options: {
          interaction: { mode: "index", intersect: false },
          scales: { x: quietAxis(), y: moneyAxis("y") },
          plugins: { tooltip: moneyTooltip, legend: { display: datasets.length > 1, position: "bottom" } }
        },
        type: "bar"
      };
    },

    /* A few categories that add up to a whole, drawn as a ring with a 2px surface gap. */
    status: function (data) {
      return {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{
            label: data.measure || "Members", data: data.values, backgroundColor: data.colors,
            borderColor: SURFACE, borderWidth: 2, hoverOffset: 6
          }]
        },
        options: {
          cutout: "62%",
          plugins: {
            legend: { position: "bottom" },
            tooltip: { callbacks: { label: function (ctx) {
              var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
              var pct = total ? Math.round(ctx.parsed / total * 100) : 0;
              return " " + ctx.label + ": " + ctx.parsed + " (" + pct + "%)";
            } } }
          }
        }
      };
    },

    /* Money shares of a portfolio. Colours follow the entity in fixed order; the rest fold into Other. */
    allocation: function (data) {
      var colors = data.labels.map(function (_, i) {
        return i < SERIES.length && data.labels[i] !== "Other" ? SERIES[i] : OTHER;
      });
      return {
        type: "doughnut",
        data: {
          labels: data.labels,
          datasets: [{ label: "Worth now", data: data.values, backgroundColor: colors, borderColor: SURFACE, borderWidth: 2, hoverOffset: 6 }]
        },
        options: {
          cutout: "62%",
          plugins: {
            legend: { position: "bottom" },
            tooltip: { callbacks: { label: function (ctx) {
              var total = ctx.dataset.data.reduce(function (a, b) { return a + b; }, 0);
              return " " + ctx.label + ": " + ugx(ctx.parsed) + " (" + (total ? Math.round(ctx.parsed / total * 100) : 0) + "%)";
            } } }
          }
        }
      };
    },

    /* One horizontal bar per member: paid + still to pay = the target. Click a bar to open the member. */
    progress: function (data) {
      return {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: [
            { label: "Paid", data: data.paid, backgroundColor: SERIES[0], borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 0, bottomRight: 0 }, stack: "t", maxBarThickness: 16 },
            { label: "Still to pay", data: data.outstanding, backgroundColor: "#d9dfde", borderRadius: { topRight: 4, bottomRight: 4 }, stack: "t", maxBarThickness: 16 }
          ]
        },
        options: {
          indexAxis: "y",
          interaction: { mode: "index", intersect: false },
          scales: { x: Object.assign(moneyAxis("x"), { stacked: true }), y: Object.assign(quietAxis(), { stacked: true }) },
          plugins: { tooltip: moneyTooltip, legend: { position: "bottom" } },
          onClick: function (evt, items) {
            if (items.length && data.links && data.links[items[0].index]) { window.location = data.links[items[0].index]; }
          },
          onHover: function (evt, items) {
            evt.native.target.style.cursor = items.length && data.links ? "pointer" : "default";
          }
        }
      };
    },

    /* Return on each investment, in percent. One series, so no legend box. */
    returns: function (data) {
      return {
        type: "bar",
        data: {
          labels: data.labels,
          datasets: [{ label: "Total return", data: data.values, backgroundColor: SERIES[0], borderRadius: { topLeft: 4, topRight: 4 }, maxBarThickness: 40 }]
        },
        options: {
          scales: { x: quietAxis(), y: { grid: { color: GRID, drawTicks: false }, border: { display: false }, ticks: { callback: function (v) { return v + "%"; }, padding: 8 } } },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: function (ctx) { return " Total return: " + ctx.parsed.y + "%"; } } } }
        }
      };
    }
  };

  /* The same numbers as a table, for screen readers and for anyone who prefers figures. */
  function addTable(canvas, config, kind) {
    var wrap = canvas.closest(".fcu-chart-card") || canvas.parentElement;
    var host = wrap.querySelector("[data-fcu-table]");
    if (!host) { return; }

    var labels = config.data.labels;
    var sets = config.data.datasets;
    var money = kind !== "status" && kind !== "returns";
    var fmt = function (v) {
      if (kind === "returns") { return v + "%"; }
      return money ? ugx(v) : v;
    };

    var html = "<details class='fcu-chart-table'><summary>View as table</summary><table><thead><tr><th></th>";
    sets.forEach(function (s) { html += "<th>" + s.label + "</th>"; });
    html += "</tr></thead><tbody>";
    labels.forEach(function (label, i) {
      html += "<tr><td>" + label + "</td>";
      sets.forEach(function (s) { html += "<td>" + fmt(s.data[i]) + "</td>"; });
      html += "</tr>";
    });
    html += "</tbody></table></details>";
    host.innerHTML = html;
  }

  document.querySelectorAll("canvas[data-fcu-chart]").forEach(function (canvas) {
    var kind = canvas.dataset.fcuChart;
    var source = document.getElementById(canvas.dataset.source);

    if (!builders[kind] || !source) { return; }

    var data = JSON.parse(source.textContent);
    var config = builders[kind](data);

    if (!config.type) { config.type = "bar"; }
    config.options = config.options || {};
    config.options.responsive = true;

    new Chart(canvas, config);
    addTable(canvas, config, kind);
  });
})();
