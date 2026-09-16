import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import { replayCoverage, resultQualityLabel } from '../../docs/data-status.js';

const source = fs.readFileSync(new URL('../../docs/app.js', import.meta.url), 'utf8');
function loadFunction(name, nextName, globals = {}) {
  const code = source.slice(source.indexOf(`function ${name}(`), source.indexOf(`function ${nextName}(`));
  const context = vm.createContext(globals);
  vm.runInContext(code, context);
  return context[name];
}
test('detail coverage takes precedence and unknown contract cannot claim complete', () => {
  const event = {replayCoverage:{version:1, code:'full',label:'full'}};
  assert.equal(replayCoverage(event,{completeness:{replayCoverage:{version:1,code:'partial',label:'partial'}}}).code,'partial');
  assert.equal(replayCoverage({replayCoverage:{version:99,code:'full'}}).code,'unknown');
  assert.match(resultQualityLabel({resultStatus:'disputed',replayable:true}),/不计入胜负统计/);
});
test('player detail keeps archive and playable counts separate, including zero', () => {
  const staticPlayerCache = new Map([['1',{totals:{games:2,archivedGames:2,playableGames:0,excludedGames:2},packages:[{id:'all',gameCount:0}],games:[]}]]);
  const info = loadFunction('staticPlayerInfo','staticPlayerHitBlock',{staticPlayerCache})({fideID:'1',gameCount:2});
  assert.equal(info.archivedGameCount,2);
  assert.equal(info.playableGameCount,0);
  assert.equal(info.excludedGameCount,2);
});
test('raw archive with illegal games is never a default replay fallback', () => {
  const code = source.slice(source.indexOf('function eventPGNArchive('), source.indexOf('\nfunction ',source.indexOf('function eventPGNArchive(')+1));
  const context = vm.createContext({}); vm.runInContext(code,context);
  assert.equal(context.eventPGNArchive({completeness:{counts:{excludedArchivedGames:1}},rounds:[{pairings:[{localGame:{pgnPath:'raw.pgn'}}]}]}),null);
});
