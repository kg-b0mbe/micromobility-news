// Dependency-free DOM harness: exercises the real inline application script.
// Run: node --test tests/news-filters.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const currentData = JSON.parse(fs.readFileSync(path.join(__dirname, '../data.json'), 'utf8'));

class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {};
    this.className = ''; this.hidden = false; this._text = '';
    this.classList = { toggle: (name, enabled) => {
      const classes = new Set(this.className.split(' ').filter(Boolean));
      enabled ? classes.add(name) : classes.delete(name);
      this.className = [...classes].join(' ');
    }};
  }
  set textContent(text) { this._text = text; this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  setAttribute(name, value) { this.attributes[name] = value; }
  focus() { this.focused = true; }
  scrollIntoView() { this.scrolled = true; }
}
function app() {
  const ids = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
  const buttons = [...html.matchAll(/<button[^>]+data-f="([^"]+)"[^>]*>(.*?)<\/button>/g)].map(m => {
    const b = new Element('button'); b.dataset.f = m[1]; b.textContent = m[2]; return b;
  });
  const document = {
    getElementById: id => { assert.ok(ids[id], `Missing HTML element: ${id}`); return ids[id]; },
    createElement: tag => new Element(tag), createTextNode: text => { const n = new Element(); n.textContent = text; return n; },
    querySelectorAll: selector => { assert.equal(selector, '.filters [data-f]'); return buttons; }
  };
  const context = vm.createContext({ document, fetch: () => new Promise(() => {}) });
  vm.runInContext(script, context);
  return { context, ids, buttons, render: data => context.render(data),
    clickRegion: region => buttons.find(b => b.dataset.f === region).onclick(),
    month: value => { ids['month-filter'].value = value; ids['month-filter'].onchange(); },
    visible: () => ids['news-grid'].children.filter(c => !c.hidden),
    months: () => ids['month-filter'].children.map(o => o.value) };
}
const article = (date, region, title) => ({ date, region, title, text: 'body', url: 'https://example.com/' });
const fixture = () => ({ ...currentData, news: [
  article('2026.09.30', 'jp', 'September Japan'), article('2025.12.31', 'na', 'Last year'),
  article('2026.10.02', 'eu', 'October Europe'), article('2026.10.02', 'jp', 'October Japan'),
  article('2026.10.01', 'na', 'October America')
], archive: ['1999.01', '2026.08'] });
const titles = a => a.visible().map(c => c.children.find(n => n.tagName === 'h3').textContent);

test('sorts newest first, keeps ties stable, and never mutates source articles', () => {
  const a = app(), data = fixture(), original = JSON.stringify(data);
  a.render(data);
  assert.deepEqual(titles(a), ['October Europe', 'October Japan', 'October America', 'September Japan', 'Last year']);
  assert.equal(JSON.stringify(data), original);
});
test('offers only actual article months, newest first, ignoring stale archive metadata', () => {
  const a = app(); a.render(fixture());
  assert.deepEqual(a.months(), ['all', '2026.10', '2026.09', '2025.12']);
  assert.deepEqual(a.ids['arch-list'].children.map(b => b.dataset.month), a.months());
});
test('region and month compose in either order; regional ALL retains the month', () => {
  const a = app(); a.render(fixture()); a.clickRegion('jp'); a.month('2026.10');
  assert.deepEqual(titles(a), ['October Japan']);
  a.ids['reset-filters'].onclick(); a.month('2026.10'); a.clickRegion('jp');
  assert.deepEqual(titles(a), ['October Japan']);
  a.clickRegion('all'); assert.equal(a.visible().length, 3);
  assert.equal(a.ids['month-filter'].value, '2026.10');
});
test('regional counts reflect selected month, independently of selected region', () => {
  const a = app(); a.render(fixture()); a.month('2026.09'); a.clickRegion('jp');
  assert.deepEqual(a.buttons.map(b => b.textContent), ['ALL (1)', '🇯🇵 日本 (1)', '🌎 北米 (0)', '🇪🇺 欧州 (0)', '🌏 アジア (0)', '🌐 世界 (0)']);
});
test('zero-count regions remain actionable and show an explicit empty state', () => {
  const a = app(); a.render(fixture()); a.clickRegion('as');
  assert.equal(a.visible().length, 0); assert.equal(a.ids['news-empty'].hidden, false);
  assert.match(a.ids['news-summary'].textContent, /アジア.*0件/);
  a.clickRegion('gl'); assert.equal(a.ids['news-empty'].hidden, false);
});
test('reset restores every article, month controls, counts and accessible pressed states', () => {
  const a = app(); a.render(fixture()); a.month('2026.09'); a.clickRegion('eu');
  a.ids['reset-filters'].onclick(); a.ids['reset-filters'].onclick();
  assert.equal(a.visible().length, 5); assert.equal(a.ids['news-empty'].hidden, true);
  assert.equal(a.ids['month-filter'].value, 'all');
  assert.deepEqual(a.buttons.map(b => b.attributes['aria-pressed']), ['true', 'false', 'false', 'false', 'false', 'false']);
  assert.equal(a.ids['arch-list'].children[0].attributes['aria-pressed'], 'true');
});
test('bottom month controls preserve region, synchronize select and move focus to results controls', () => {
  const a = app(); a.render(fixture()); a.clickRegion('jp');
  const button = a.ids['arch-list'].children.find(b => b.dataset.month === '2026.09');
  button.onclick(); button.onclick();
  assert.deepEqual(titles(a), ['September Japan']);
  assert.equal(a.ids['month-filter'].value, '2026.09'); assert.equal(button.attributes['aria-pressed'], 'true');
  assert.equal(a.ids['month-filter'].focused, true); assert.equal(a.ids.news.scrolled, true);
});
test('empty data and malformed dates do not create fictitious months or hide articles from ALL', () => {
  const a = app(); a.render({ ...fixture(), news: [] });
  assert.deepEqual(a.months(), ['all']); assert.equal(a.ids['news-empty'].hidden, false);
  a.render({ ...fixture(), news: [article('2026.02.30', 'jp', 'Invalid'), article(null, 'na', 'Missing'), article('2026.01.01', 'eu', 'Valid')] });
  assert.deepEqual(a.months(), ['all', '2026.01']);
  assert.deepEqual(titles(a), ['Valid', 'Invalid', 'Missing']);
});
test('current and offline fallback data both render with derived months and all facts retained', () => {
  const a = app();
  const fallback = vm.runInContext('FALLBACK_DATA', a.context);
  for (const data of [currentData, fallback]) {
    a.render(data); assert.equal(a.visible().length, data.news.length);
    const dates = a.visible().map(c => c.children[0].children[1].textContent);
    assert.deepEqual(dates, [...dates].sort().reverse());
    assert.deepEqual(a.months().slice(1), [...new Set(data.news.map(n => n.date.slice(0, 7)))].sort().reverse());
  }
});
test('HTML uses native controls, labelled groups and a polite live result summary', () => {
  assert.doesNotMatch(html, /role="tablist"/);
  assert.match(html, /<label for="month-filter">/);
  assert.match(html, /id="news-summary" role="status" aria-live="polite" aria-atomic="true"/);
  assert.match(html, /\[hidden\]\{display:none!important\}/);
});
