(function (root) {
  'use strict';
  const messages = {
    PASS: '数值相符，来源已人工核验', MISSING: '未提供必填值，请补录对应原件字段',
    BASELINE_UNCONFIRMED: '业务基准或计算依赖尚未确认', SOURCE_MISSING: '缺少来源文件或页码 / 单元格定位',
    INVALID: '数值格式或单位不受支持，请核对原件', MISMATCH: '原件抄录值与当前确认基准不同',
    NEEDS_REVIEW: '值相符，仍需人工核验来源'
  };
  const labels = {PASS:'已核验', MISSING:'缺少字段', BASELINE_UNCONFIRMED:'基准未确认', SOURCE_MISSING:'来源缺项', INVALID:'格式 / 单位错误', MISMATCH:'存在差异', NEEDS_REVIEW:'待人工核验'};
  const text = value => value == null ? '' : String(value).trim().replace(/\s+/g, ' ');
  function normalizeNumber(value, sourceUnit = null, baseUnit = null) {
    const raw = String(value).trim();
    if (raw.length > 80 || !/^\+?\d+(?:\.\d+)?$/.test(raw)) throw new Error('INVALID_DECIMAL');
    const pieces = raw.replace(/^\+/, '').split('.');
    let digits = BigInt(pieces.join('')), scale = (pieces[1] || '').length;
    if (sourceUnit === baseUnit || !sourceUnit && !baseUnit) { /* same unit */ }
    else if (sourceUnit === 'kg' && baseUnit === 'g') scale -= 3;
    else throw new Error('UNIT_MISMATCH');
    if (scale < 0) { digits *= 10n ** BigInt(-scale); scale = 0; }
    while (scale > 0 && digits % 10n === 0n) { digits /= 10n; scale--; }
    return digits.toString() + ':' + scale;
  }
  function evaluateCheck(check) {
    let status;
    if (!text(check.value)) status = 'MISSING';
    else if (!check.input_confirmed) status = 'BASELINE_UNCONFIRMED';
    else if (!text(check.source_file) || !text(check.source_locator)) status = 'SOURCE_MISSING';
    else {
      try {
        const equal = check.value_type === 'number'
          ? normalizeNumber(check.expected, check.base_unit, check.base_unit) === normalizeNumber(check.value, check.source_unit, check.base_unit)
          : text(check.expected) === text(check.value);
        status = !equal ? 'MISMATCH' : check.reviewed === true ? 'PASS' : 'NEEDS_REVIEW';
      } catch (_) { status = 'INVALID'; }
    }
    return {...check, status, message: messages[status]};
  }
  function evaluateChecks(checks) { return checks.map(evaluateCheck); }
  const api = {evaluateChecks, normalizeNumber};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.PreflightDemo = api;
  if (typeof document === 'undefined') return;

  function start() {
    const data = root.PREFLIGHT_DEMO;
    if (!data || !Array.isArray(data.checks)) {
      document.getElementById('documents').textContent = '演示数据未能加载。请确认 data.js 和页面位于同一目录。';
      return;
    }
    let checks = [], rowElements = new Map(), scenario = 'problem';
    const $ = id => document.getElementById(id);
    const clone = value => JSON.parse(JSON.stringify(value));
    function element(tag, className, content) {
      const el = document.createElement(tag);
      if (className) el.className = className;
      if (content != null) el.textContent = content;
      return el;
    }
    function unitLabel(unit) { return !unit || unit === 'amount' ? '' : ' ' + unit; }
    function refresh() {
      const results = evaluateChecks(checks);
      const count = status => results.filter(row => row.status === status).length;
      $('count-pass').textContent = count('PASS');
      $('count-mismatch').textContent = count('MISMATCH');
      $('count-review').textContent = count('NEEDS_REVIEW');
      $('count-missing').textContent = results.length - count('PASS') - count('MISMATCH') - count('NEEDS_REVIEW');
      const pending = results.length - count('PASS');
      $('review-state').className = 'review-state ' + (pending ? 'pending' : 'complete');
      $('review-state').textContent = pending ? `${pending} 项仍需处理 · 当前样例尚未完成核对` : '10 项样例核对完成 · 仍须经办人审核，不代表正式出运放行';
      results.forEach(row => {
        const refs = rowElements.get(row.id);
        refs.badge.className = 'badge ' + row.status.toLowerCase();
        refs.badge.textContent = labels[row.status];
        refs.badge.title = row.message;
        refs.tr.dataset.status = row.status;
        refs.input.setAttribute('aria-invalid', ['MISMATCH', 'INVALID', 'MISSING'].includes(row.status) ? 'true' : 'false');
      });
    }
    function render() {
      $('documents').replaceChildren(); rowElements = new Map();
      ['invoice', 'packing', 'carrier'].forEach((doc, index) => {
        const rows = checks.filter(row => row.document_id === doc);
        const card = element('article', 'document-card');
        const heading = element('div', 'document-heading');
        const sourceLink = element('a', 'document-file', rows[0].source_file + ' ↗');
        sourceLink.href = 'sources/' + scenario + '/' + rows[0].source_file;
        sourceLink.target = '_blank'; sourceLink.rel = 'noopener'; sourceLink.title = '查看可复核的合成文本原件';
        heading.append(element('span', 'document-number', '0' + (index + 1)), element('h3', '', rows[0].document_label), sourceLink);
        card.append(heading);
        const wrap = element('div', 'table-wrap');
        const table = element('table');
        const head = element('thead'), titles = element('tr');
        ['字段', '当前确认基准', '原件抄录值', '来源定位', '人工核验', '检查结果'].forEach(t => titles.append(element('th', '', t)));
        head.append(titles); table.append(head);
        const body = element('tbody');
        rows.forEach(row => {
          const tr = element('tr');
          const field = element('td', 'field-name', row.field_label.split(' · ')[0]);
          if (row.field_label.includes(' · ')) field.append(element('small', '', '合成测试片剂甲'));
          tr.append(field, element('td', 'expected', row.expected + unitLabel(row.base_unit)));
          const valueCell = element('td', 'value-cell');
          const input = element('input', 'value-input'); input.type = 'text'; input.value = row.value || '';
          input.placeholder = '未提供'; input.setAttribute('aria-label', row.document_label + ' ' + row.field_label + ' 原件抄录值');
          input.addEventListener('input', () => { row.value = input.value; row.reviewed = false; checkbox.checked = false; refresh(); });
          valueCell.append(input, element('span', 'unit', unitLabel(row.source_unit))); tr.append(valueCell);
          const sourceCell = element('td');
          const location = element('input', 'location-input'); location.type = 'text'; location.value = row.source_locator;
          location.setAttribute('aria-label', row.document_label + ' ' + row.field_label + ' 来源定位');
          location.addEventListener('input', () => { row.source_locator = location.value; row.reviewed = false; checkbox.checked = false; refresh(); });
          sourceCell.append(location); tr.append(sourceCell);
          const reviewCell = element('td', 'review-cell'); const label = element('label', 'review-toggle');
          const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.checked = row.reviewed;
          checkbox.setAttribute('aria-label', row.document_label + ' ' + row.field_label + ' 来源已核验');
          checkbox.addEventListener('change', () => { row.reviewed = checkbox.checked; refresh(); });
          label.append(checkbox, element('span', '', '已核验')); reviewCell.append(label); tr.append(reviewCell);
          const resultCell = element('td'), badge = element('span', 'badge'); resultCell.append(badge); tr.append(resultCell);
          body.append(tr); rowElements.set(row.id, {tr, badge, input});
        });
        table.append(body); wrap.append(table); card.append(wrap); $('documents').append(card);
      });
      refresh();
    }
    function load(corrected) {
      scenario = corrected ? 'corrected' : 'problem';
      checks = clone(corrected ? data.corrected_checks : data.checks);
      $('load-problem').classList.toggle('selected', !corrected);
      $('load-corrected').classList.toggle('selected', corrected);
      $('toast').textContent = corrected ? '已载入另一份合成样例；没有修改原始业务数据。' : '';
      render();
    }
    function canonical(value) {
      if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
      if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
      return JSON.stringify(value);
    }
    $('download').addEventListener('click', async () => {
      try {
        const clean = checks.map(({status, message, ...row}) => row);
        const payload = {rules_version: data.rules_version, checks: clean};
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical(payload)));
        const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
        const results = evaluateChecks(clean), passed = results.filter(row => row.status === 'PASS').length;
        const report = {schema_version: 1, rules_version: data.rules_version, synthetic_only: true,
          notice: data.notice, evidence_boundary: 'browser_precomputed_baseline_and_synthetic_transcriptions_only',
          created_at: new Date().toISOString(), input_fingerprint: fingerprint, fingerprint_scope: 'browser-checks',
          status: passed === results.length ? 'READY_FOR_HUMAN_REVIEW' : 'BLOCKED',
          summary: {required: results.length, passed, unresolved: results.length - passed}, checks: results};
        const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}));
        const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'SYNTHETIC-preflight-report.json';
        anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        $('toast').textContent = '报告已生成，包含当前抄录值、核验状态和输入指纹。';
      } catch (_) { $('toast').textContent = '无法生成输入指纹，请通过 localhost 或 HTTPS 打开此演示。'; }
    });
    $('load-problem').addEventListener('click', () => load(false));
    $('load-corrected').addEventListener('click', () => load(true));
    $('notice').textContent = data.notice;
    load(false);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})(typeof window === 'undefined' ? null : window);
