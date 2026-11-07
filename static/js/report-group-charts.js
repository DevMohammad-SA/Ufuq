/* Read-only enhancement of the complete, server-normalized group report. */
(function () {
  'use strict';

  var root = document.getElementById('report-group-comparison');
  if (!root) return;
  var data = document.getElementById('report-group-comparison-data');
  var controls = document.getElementById('rgc-controls');
  var metric = document.getElementById('rgc-metric');
  var sort = document.getElementById('rgc-sort');
  var figure = document.getElementById('rgc-figure');
  var fallback = document.getElementById('rgc-fallback');
  var title = document.getElementById('rgc-metric-title');
  var description = document.getElementById('rgc-description');
  var scaleNote = document.getElementById('rgc-scale-note');
  var axisLayout = document.getElementById('rgc-axis-layout');
  var axis = document.getElementById('rgc-axis');
  var axisUnit = document.getElementById('rgc-axis-unit');
  var rows = document.getElementById('rgc-rows');
  var message = document.getElementById('rgc-message');
  var status = document.getElementById('rgc-status');
  var stale = document.getElementById('rgc-stale');
  var pending = document.getElementById('rc-pending');
  var form = document.getElementById('report-center-form');
  var keys = ['meeting', 'circle', 'achievement', 'activity', 'submitters', 'net_average'];
  var series;
  var numberFormat;
  var nameOrder;

  function fail() {
    root.dataset.state = 'fallback';
    if (controls) controls.hidden = true;
    if (metric) metric.disabled = true;
    if (sort) sort.disabled = true;
    if (figure) figure.hidden = true;
    if (status) status.textContent = '';
    if (fallback) {
      fallback.hidden = false;
      fallback.textContent = 'تعذر عرض الرسم التفاعلي. يمكنك قراءة الأعداد ومقامات المقارنة من الجداول أدناه.';
    }
  }

  function validPayload(payload) {
    if (!payload || !Array.isArray(payload.series) || payload.series.length !== keys.length ||
        typeof payload.scope_note !== 'string') return false;
    var seen = [];
    return payload.series.every(function (item) {
      if (!item || keys.indexOf(item.key) === -1 || seen.indexOf(item.key) !== -1 ||
          typeof item.label !== 'string' || typeof item.description !== 'string' ||
          item.unit !== (item.key === 'net_average' ? 'points' : 'percent') ||
          !Array.isArray(item.points)) return false;
      seen.push(item.key);
      return item.points.every(function (point) {
        if (!point || typeof point.name !== 'string' ||
            !Number.isSafeInteger(point.numerator) || !Number.isSafeInteger(point.denominator) ||
            point.denominator < 0 || (item.unit === 'percent' && point.numerator < 0)) return false;
        if (point.value === null) return true;
        // Missing denominators must never masquerade as real zeros.
        return point.denominator > 0 && typeof point.value === 'number' && Number.isFinite(point.value) &&
          (item.unit !== 'percent' || (point.value >= 0 && point.value <= 100));
      });
    });
  }

  function formatNumber(value) {
    return numberFormat.format(Object.is(value, -0) ? 0 : value);
  }

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function compareNames(a, b) {
    var comparison = nameOrder.compare(a.name, b.name);
    // Locale-equivalent names still have a reproducible ordering.
    if (comparison) return comparison;
    return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
  }

  function sortedPoints(points) {
    return points.slice().sort(function (a, b) {
      if (sort.value === 'highest') {
        if (a.value === null && b.value !== null) return 1;
        if (a.value !== null && b.value === null) return -1;
        if (a.value !== null && b.value !== null && a.value !== b.value) return b.value - a.value;
      }
      return compareNames(a, b);
    });
  }

  function scaleFor(item) {
    var available = item.points.filter(function (point) { return point.value !== null; });
    var min = 0;
    var max = item.unit === 'percent' ? 100 : 0;
    if (item.unit === 'points') {
      available.forEach(function (point) {
        min = Math.min(min, point.value);
        max = Math.max(max, point.value);
      });
      if (min === max) max = 1;
    }
    if (!Number.isFinite(max - min)) throw new Error('Invalid scale');
    return {
      min: min,
      max: max,
      zero: (0 - min) / (max - min) * 100,
      available: available.length,
      allZero: available.length > 0 && available.every(function (point) { return point.value === 0; })
    };
  }

  function position(value, scale) {
    return (value - scale.min) / (scale.max - scale.min) * 100;
  }

  function drawAxis(item, scale) {
    axis.replaceChildren();
    axisLayout.hidden = !scale.available;
    var staggerZero = item.unit === 'points' && scale.min < 0 && scale.max > 0 &&
      (scale.zero < 20 || scale.zero > 80);
    axis.className = 'rgc-axis' + (staggerZero ? ' rgc-axis--staggered' : '');
    axisUnit.textContent = item.unit === 'percent' ? 'النسبة المئوية ٪' : 'متوسط النقاط لكل طالب';
    var ticks = item.unit === 'percent' ? [0, 50, 100] : [scale.min, 0, scale.max];
    ticks.filter(function (value, index) { return ticks.indexOf(value) === index; }).forEach(function (value) {
      var className = 'rgc-tick';
      if (value === scale.min) className += ' rgc-tick--start';
      else if (value === scale.max) className += ' rgc-tick--end';
      else if (value === 0 && staggerZero) className += ' rgc-tick--staggered';
      var tick = node('span', className, formatNumber(value));
      tick.style.setProperty('--rgc-position', position(value, scale) + '%');
      tick.setAttribute('data-value', String(value));
      axis.appendChild(tick);
    });
    if (item.unit === 'percent') {
      scaleNote.textContent = 'محور ثابت من ٠ إلى ١٠٠٪ لكل البيئات. المقام هو السجلات المسجلة، أو طلاب النطاق لمؤشر التسليم.';
    } else if (scale.allZero) {
      scaleNote.textContent = 'كل القيم المتاحة صفر؛ محور توضيحي من ٠ إلى ١ نقطة، ولا أشرطة ذات طول. غير المتاح لا يُعامل كصفر.';
    } else {
      scaleNote.textContent = 'محور مشترك لكل البيئات يشمل الصفر. الصافي السالب باللون التنبيهي يسار الصفر، والموجب باللون الفيروزي يمينه؛ ليس الرصيد الحالي.';
    }
    if (!scale.available) scaleNote.textContent = 'لا توجد قيم متاحة لهذا المؤشر، لذلك لا يُعرض محور أو أشرطة. المقام صفر لا يعني قيمة صفر.';
  }

  function countsFor(item, point) {
    var numerator = formatNumber(point.numerator);
    var denominator = formatNumber(point.denominator);
    if (item.key === 'net_average') return 'صافي الفترة: ' + numerator + ' نقطة ÷ ' + denominator + ' طالب في النطاق الحالي';
    if (item.key === 'submitters') return 'طلاب ذوو تسليم: ' + numerator + ' من ' + denominator + ' طالب في النطاق الحالي';
    var labels = {
      meeting: ['حضور اللقاء', 'سجل لقاء مسجل'],
      circle: ['حضور الحلقة', 'سجل حلقة مسجل'],
      achievement: ['إنجاز الحلقة', 'سجل حلقة مسجل'],
      activity: ['إنجاز الفعالية', 'سجل فعالية مسجل']
    };
    return labels[item.key][0] + ': ' + numerator + ' من ' + denominator + ' ' + labels[item.key][1];
  }

  function drawRow(item, point, scale) {
    var row = node('li', 'rgc-row');
    row.appendChild(node('span', 'rgc-name', point.name));
    var track = node('div', 'rgc-track' + (point.value === null ? ' rgc-track--unavailable' : ''));
    track.setAttribute('aria-hidden', 'true');
    if (point.value !== null) {
      track.style.setProperty('--rgc-zero', scale.zero + '%');
      track.appendChild(node('span', 'rgc-zero'));
      // Real zero remains a readable zero, never a minimum-length decorative bar.
      if (point.value !== 0) {
        var barClass = 'rgc-bar';
        if (item.unit === 'points') barClass += ' rgc-bar--points';
        if (point.value < 0) barClass += ' rgc-bar--negative';
        var bar = node('span', barClass);
        bar.style.setProperty('--rgc-left', position(Math.min(0, point.value), scale) + '%');
        bar.style.setProperty('--rgc-width', Math.abs(point.value) / (scale.max - scale.min) * 100 + '%');
        track.appendChild(bar);
      }
    }
    row.appendChild(track);
    var values = node('div', 'rgc-values');
    var valueClass = 'rgc-value';
    if (point.value === null) valueClass += ' rgc-value--unavailable';
    else if (point.value < 0) valueClass += ' rgc-value--negative';
    else if (item.unit === 'points') valueClass += ' rgc-value--points';
    var value = node('span', valueClass);
    if (point.value === null) {
      value.textContent = 'غير متاح' + (point.denominator === 0 ? ' — المقام صفر' : '');
    } else {
      var numeric = node('bdi', '', formatNumber(point.value));
      numeric.setAttribute('dir', 'ltr');
      value.appendChild(numeric);
      value.appendChild(node('span', '', item.unit === 'percent' ? ' ٪' : ' نقطة'));
    }
    values.appendChild(value);
    values.appendChild(node('small', 'rgc-counts', countsFor(item, point)));
    row.appendChild(values);
    return row;
  }

  function render() {
    try {
      var item = series.find(function (entry) { return entry.key === metric.value; });
      if (!item) throw new Error('Unknown metric');
      // Calculate before sorting and from the entire payload, never paginated tables.
      var scale = scaleFor(item);
      title.textContent = item.label;
      description.textContent = item.description;
      drawAxis(item, scale);
      var fragment = document.createDocumentFragment();
      sortedPoints(item.points).forEach(function (point) {
        fragment.appendChild(drawRow(item, point, scale));
      });
      rows.replaceChildren(fragment);
      message.hidden = item.points.length > 0 && scale.available > 0 && !scale.allZero;
      if (!item.points.length) message.textContent = 'لا توجد بيئات مطابقة للإعدادات المطبقة.';
      else if (!scale.available) message.textContent = 'لا تتوافر بيانات قابلة للمقارنة لهذا المؤشر. الأسماء والبسط والمقام موضحة أدناه دون تحويل غير المتاح إلى صفر.';
      else if (scale.allZero) message.textContent = 'جميع القيم المتاحة لهذا المؤشر تساوي صفرًا؛ لا توجد أشرطة ذات طول. البيئات غير المتاحة تبقى مميزة.';
      status.textContent = item.label + ' · ' + formatNumber(item.points.length) + ' بيئة مطابقة · ' +
        formatNumber(scale.available) + ' بقيمة متاحة · ' +
        (sort.value === 'highest' ? 'الأعلى أولًا؛ غير المتاح في النهاية.' : 'مرتبة حسب اسم البيئة.');
      return true;
    } catch (error) {
      fail();
      return false;
    }
  }

  function syncStale() {
    // report-center.js runs first and owns the applied/current settings comparison.
    var isStale = Boolean(pending && !pending.hidden);
    root.dataset.stale = String(isStale);
    if (stale) stale.hidden = !isStale;
  }

  try {
    if (!data || !controls || !metric || !sort || !figure || !fallback || !title || !description ||
        !scaleNote || !axisLayout || !axis || !axisUnit || !rows || !message || !status) {
      fail();
      return;
    }
    var payload = JSON.parse(data.textContent);
    if (!validPayload(payload)) {
      fail();
      return;
    }
    series = payload.series;
    numberFormat = new Intl.NumberFormat('ar-SA', {maximumFractionDigits: 2});
    nameOrder = new Intl.Collator('ar', {numeric: true});
    metric.replaceChildren();
    series.forEach(function (item) {
      var option = node('option', '', item.label);
      option.value = item.key;
      metric.appendChild(option);
    });
    metric.value = series[0].key;
    sort.value = 'name';
    if (!render()) return;
    metric.addEventListener('change', render);
    sort.addEventListener('change', render);
    syncStale();
    if (form) {
      form.addEventListener('input', syncStale);
      form.addEventListener('change', syncStale);
    }
    window.addEventListener('pageshow', syncStale);
    metric.disabled = false;
    sort.disabled = false;
    controls.hidden = false;
    figure.hidden = false;
    fallback.hidden = true;
    root.dataset.state = 'ready';
  } catch (error) {
    fail();
  }
})();
