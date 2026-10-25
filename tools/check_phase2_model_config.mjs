import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from '../web/node_modules/esbuild/lib/main.js';
const entry = fileURLToPath(new URL('../web/src/services/modelConfig.ts', import.meta.url));
const bundle = await build({ entryPoints: [entry], bundle: true, write: false, format: 'esm', platform: 'node' });
const config = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const declaration = { rawX: false, rawSU: false };
let checks = 0;
for (const method of ['cellrank', 'auto']) {
  const params = { cellrank_terminal: 'Beta,Alpha', cellrank_n_states: 5 };
  const payload = config.buildSubmitPayload(method, params, declaration);
  assert.equal(payload.cellrank_terminal, 'Beta,Alpha'); checks++;
  const exported = config.buildConfigFile({ method, methodName: method, config: params, declaration,
    precheck: { ready: true, missing: [], blockers: [] } });
  assert.equal(exported.params.cellrank_terminal, 'Beta,Alpha'); checks++;
  const parsed = config.parseConfigFile(JSON.stringify(exported), { knownMethods: [method] });
  assert.equal(parsed.ok, true); assert.equal(parsed.params.cellrank_terminal, 'Beta,Alpha'); checks++;
  for (const invalid of ['Beta,', 'Beta,Beta', ' , ', ' ']) {
    const bad = structuredClone(exported); bad.params.cellrank_terminal = invalid;
    assert.equal(config.parseConfigFile(JSON.stringify(bad), { knownMethods: [method] }).ok, false); checks++;
    assert.throws(() => config.buildSubmitPayload(method, { cellrank_terminal: invalid }, declaration)); checks++;
  }
}
console.log(JSON.stringify({ ok: true, checks, scope: 'fixed terminal payload/export/import; invalid values never silently become automatic mode' }));
