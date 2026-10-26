// treehawk HTML report: reading values off the charts, and sorting tables.
// The page is complete without this; it only adds the hover readout and sorting.
(function () {
  "use strict";

  var charts = [];

  function nearest(xs, f) {
    var lo = 0;
    var hi = xs.length - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (xs[mid] < f) lo = mid; else hi = mid;
    }
    return Math.abs(xs[lo] - f) <= Math.abs(xs[hi] - f) ? lo : hi;
  }

  function setup(figure) {
    var data;
    try { data = JSON.parse(figure.getAttribute("data-series")); } catch (e) { return null; }
    if (!data || !data.x || !data.x.length) return null;
    var area = figure.querySelector(".area");
    var cursor = figure.querySelector(".cursor");
    var when = figure.querySelector(".legend .when");
    var values = figure.querySelectorAll(".legend .key b");
    var chart = {
      group: figure.getAttribute("data-group") || "",
      area: area,
      show: function (f) {
        var i = nearest(data.x, f);
        cursor.style.left = (data.x[i] * 100) + "%";
        cursor.hidden = false;
        when.textContent = data.t[i];
        for (var k = 0; k < values.length; k++) values[k].textContent = data.v[k][i];
      },
      hide: function () {
        cursor.hidden = true;
        when.textContent = "";
        for (var k = 0; k < values.length; k++) values[k].textContent = values[k].getAttribute("data-rest");
      }
    };
    function move(event) {
      var box = area.getBoundingClientRect();
      var f = Math.min(Math.max((event.clientX - box.left) / box.width, 0), 1);
      charts.forEach(function (other) { if (other.group === chart.group) other.show(f); });
    }
    area.addEventListener("pointermove", move);
    area.addEventListener("pointerdown", move);
    area.addEventListener("pointerleave", function () {
      charts.forEach(function (other) { if (other.group === chart.group) other.hide(); });
    });
    return chart;
  }

  document.querySelectorAll(".chart[data-series]").forEach(function (figure) {
    var chart = setup(figure);
    if (chart) charts.push(chart);
  });

  function sortBy(table, column, header) {
    var body = table.tBodies[0];
    var ascending = header.getAttribute("aria-sort") !== "ascending";
    if (!header.hasAttribute("aria-sort") && header.classList.contains("num")) ascending = false;
    table.querySelectorAll("th[aria-sort]").forEach(function (th) { th.removeAttribute("aria-sort"); });
    header.setAttribute("aria-sort", ascending ? "ascending" : "descending");
    var numeric = header.classList.contains("num") || header.classList.contains("life");
    var rows = Array.prototype.slice.call(body.rows);
    rows.sort(function (a, b) {
      var x = a.cells[column].getAttribute("data-sort");
      var y = b.cells[column].getAttribute("data-sort");
      var order = numeric ? parseFloat(x) - parseFloat(y) : x.localeCompare(y);
      return ascending ? order : -order;
    });
    rows.forEach(function (row) { body.appendChild(row); });
  }

  document.querySelectorAll("table[data-sortable]").forEach(function (table) {
    table.querySelectorAll("thead th").forEach(function (header, column) {
      var button = header.querySelector("button");
      if (button) button.addEventListener("click", function () { sortBy(table, column, header); });
    });
  });
})();
