import assert from 'node:assert/strict';
import {onRequest} from '../../functions/_middleware.js';
const routes={players:{'8602980':'/players/fide-8602980'},events:{'1234567':'/events/1234567'}};
const run=async(url,method='GET')=>onRequest({request:new Request(url,{method}),env:{ASSETS:{fetch:async()=>Response.json(routes)}},next:async()=>new Response('original',{headers:{'Content-Type':'text/html'}})});
let r=await run('https://chessdb.aigclabs.cc/?fideID=8602980');assert.equal(r.status,308);assert.equal(r.headers.get('location'),'https://chessdb.aigclabs.cc/players/fide-8602980');
r=await run('https://chessdb.aigclabs.cc/?fideID=unknown');assert.equal(r.status,200);assert.equal(await r.text(),'original');
r=await run('https://chessdb.aigclabs.cc/?fideID=8602980&view=interactive');assert.equal(r.status,200);assert.match(r.headers.get('x-robots-tag'),/noindex/);
r=await run('https://4chess.cc/events');assert.equal(r.status,308);
r=await run('https://4chess.cc/api/v1/manifest.json');assert.equal(r.status,200);
r=await run('https://preview.china-chess-player-pgn.pages.dev/');assert.equal(r.status,200);assert.match(r.headers.get('x-robots-tag'),/noindex/);
r=await run('https://chessdb.aigclabs.cc/');assert.equal(r.headers.get('x-robots-tag'),null);
r=await run('https://chessdb.aigclabs.cc/?event=1234567&round=2');assert.equal(r.status,200);assert.equal(await r.text(),'original');
console.log('SEO middleware: redirects, preview, query exclusion and API compatibility passed');

r=await run('https://4chess.cc/names/name-abc');assert.equal(r.status,308);assert.equal(r.headers.get('location'),'https://chessdb.aigclabs.cc/names/name-abc');
r=await run('https://preview.china-chess-player-pgn.pages.dev/players');assert.match(r.headers.get('x-robots-tag'),/noindex/);
