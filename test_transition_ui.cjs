// Run: node test_transition_ui.cjs (stdlib only; no browser or network).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const reason = '缺少可核验的调用量数据，暂不计分';
const fixture = {
  temperature: 55.6, emoji: '🌤️', band: '温和区', description: '测试', available_coverage: 0.94,
  lineScores: Object.fromEntries([1, 2, 3, 4].map(n => [`line${n}`, {name: `主线${n}`, score: 60, weight: [30, 15, 25, 30][n - 1]}])),
  indicators: {
    pmi: {name: '制造业PMI', line: 1, score: 40, value: 49.3, unit: '', reference_range: '48~55', included: true, auto: true, weight: 0.05, date: '2020-09', source: '测试'},
    new_energy_penetration: {name: '乘联会狭义乘用车新能源零售渗透率', line: 4, score: 100, value: 67, unit: '%', included: true, auto: true, weight: 0.05, date: '2020-09', source: '乘联会'},
    export: {name: '出口', line: 4, score: 70, value: 7, unit: '%', included: true, auto: true, weight: 0.0625, date: '2020-09', source: '测试'},
    ai_market_share: {name: '中国AI模型OpenRouter调用份额', line: 4, score: null, value: null, date: null, included: false, weight: 0, configured_weight: 0.06, exclusion_reason: reason, auto: false, stale: true},
  },
};
Object.values(fixture.indicators).forEach(s => s.label = s.included ? '正常' : '暂不计分');
for (const file of ['a500_template.html', 'index.html']) {
  const source = fs.readFileSync(file, 'utf8');
  for (const script of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) new vm.Script(script[1], {filename: file});
  const render = source.slice(source.indexOf('function renderTrans(d){'), source.indexOf('\nsetTimeout(function(){', source.indexOf('function renderTrans(d){')));
  const nodes = {};
  const context = vm.createContext({window: {}, document: {getElementById: id => nodes[id] ||= {style: {}, innerHTML: ''}}});
  vm.runInContext(render, context);
  context.renderTrans(structuredClone(fixture));
  const html = () => Object.values(nodes).map(n => n.innerHTML).join('\n');
  assert.doesNotMatch(html(), /\b(?:null|undefined|NaN)\b/);
  assert.match(nodes.transDesc.innerHTML, /94%/);
  assert.match(nodes.transGroup4.innerHTML, new RegExp(reason));
  assert.match(nodes.transIndicatorsList.innerHTML, /实际权重0% · 配置权重6%/);
  assert.doesNotMatch(nodes.transLines.innerHTML, /OpenRouter/);
  assert.equal(nodes.transWarn.style.display, 'none');
  assert.match(nodes.transGroup1.innerHTML, /48~55/);
  assert.doesNotMatch(nodes.transGroup1.innerHTML, /49\.3%/);
  const missing = structuredClone(fixture);
  missing.temperature = null;
  missing.available_coverage = 0;
  Object.values(missing.lineScores).forEach(s => s.score = null);
  Object.values(missing.indicators).forEach(s => Object.assign(s, {included: false, value: null, score: null, date: null}));
  context.renderTrans(missing);
  assert.doesNotMatch(html(), /\b(?:null|undefined|NaN)\b/);
  assert.match(nodes.transDesc.innerHTML, /暂无数据/);
  assert.match(nodes.transLines.innerHTML, /暂不计分/);
  assert.doesNotMatch(nodes.transIndicatorsList.innerHTML, />0分/);
  assert.ok(Number.isFinite(nodes.transArc.style.strokeDashoffset));
  const legacy = structuredClone(fixture);
  delete legacy.available_coverage;
  delete legacy.indicators.ai_market_share;
  Object.values(legacy.indicators).forEach(s => {delete s.included; delete s.unit; delete s.reference_range;});
  context.renderTrans(legacy);
  assert.doesNotMatch(html(), /\b(?:null|undefined|NaN)\b/);
  assert.match(nodes.transGroup4.innerHTML, /67/);
  console.log(`${file}: script syntax + included/null/legacy rendering passed`);
}
