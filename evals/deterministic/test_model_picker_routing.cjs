const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../pu/ops/static/js/models.js'), 'utf8');

function harness() {
  const nodes = new Map();
  const pending = [];
  const picks = [];
  const context = vm.createContext({
    document: {getElementById: id => nodes.get(id), addEventListener(){}, querySelectorAll: () => []},
    fetch: url => new Promise((resolve, reject) => pending.push({url, resolve, reject})),
    esc: s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    markEditing(){}, editing: false, D: {settings:{}, providers:[]},
    refresh: async () => {}, postJSON: async () => ({ok:true}),
  });
  vm.runInContext(source, context);
  context.setupModelPickers = (models, provider) => picks.push({models, provider});
  context.setModelPickerMeta = () => {};
  const reply = (i, provider, id) => pending[i].resolve({ok:true, json:async()=>({provider, listed:true, models:[{id}]})});
  return {context, nodes, pending, picks, reply};
}

test('modal ignores earlier provider response arriving last', async () => {
  const h = harness();
  const old = h.context.loadModalModels('anthropic');
  const current = h.context.loadModalModels('openrouter');
  h.reply(1, 'openrouter', 'aion-labs/aion-3.5'); await current;
  h.reply(0, 'anthropic', 'claude-example'); await old;
  assert.equal(h.picks.at(-1).provider, 'openrouter');
  assert.equal(h.picks.at(-1).models[0].id, 'aion-labs/aion-3.5');
});

test('closed modal cannot be repopulated by an old request', async () => {
  const h = harness();
  const request = h.context.loadModalModels('openrouter');
  h.context.closeProviderModal();
  h.reply(0, 'openrouter', 'vendor/model'); await request;
  assert.equal(h.picks.length, 1); // initial empty state only
});

test('opening inactive provider invalidates an old modal response', async () => {
  const h = harness();
  h.nodes.set('prov-modal-root', {innerHTML:''});
  h.context.D = {settings:{provider:'anthropic'}, providers:[{key:'openrouter', name:'OpenRouter'}]};
  const request = h.context.loadModalModels('anthropic');
  h.context.openProviderModal('openrouter');
  h.reply(0, 'anthropic', 'claude-example'); await request;
  assert.equal(h.picks.length, 1);
});

test('mismatched provider response is not offered as models', async () => {
  const h = harness();
  const request = h.context.loadModalModels('openrouter');
  h.reply(0, 'anthropic', 'wrong-model'); await request;
  assert.equal(h.picks.at(-1).models.length, 0);
});

test('add selector keeps newest provider models and exact IDs', async () => {
  const h = harness();
  const select = {innerHTML:''}; h.nodes.set('add-model', select);
  const old = h.context.loadAddModels('anthropic');
  const current = h.context.loadAddModels('openrouter');
  h.reply(1, 'openrouter', 'aion-labs/aion-3.5'); await current;
  h.reply(0, 'anthropic', 'claude-example'); await old;
  assert.match(select.innerHTML, /value="aion-labs\/aion-3.5"/);
  assert.doesNotMatch(select.innerHTML, /claude-example/);
});

test('stale failed request cannot erase the new list', async () => {
  const h = harness();
  const select = {innerHTML:''}; h.nodes.set('add-model', select);
  const old = h.context.loadAddModels('anthropic');
  const current = h.context.loadAddModels('openrouter');
  h.reply(1, 'openrouter', 'vendor/model'); await current;
  h.pending[0].reject(new Error('old request failed')); await old;
  assert.match(select.innerHTML, /vendor\/model/);
});

test('click selection stores the unmodified model ID', () => {
  const h = harness();
  const input = {value:'', focus(){}}; h.nodes.set('pm-model', input);
  h.context.selectModelPicker('pm-model', 'anthropic/claude-example');
  assert.equal(input.value, 'anthropic/claude-example');
});

test('saving provider invalidates cached catalog', async () => {
  const h = harness();
  vm.runInContext('modelCatalog = {provider:"old"}', h.context);
  await h.context.submitProviderModal('openrouter', {}, 'pm-save');
  assert.equal(vm.runInContext('modelCatalog', h.context), null);
});
