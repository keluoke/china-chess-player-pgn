// Editions are the navigation unit; original section links remain valid.
(async function () {
  const $ = s => document.querySelector(s);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const state = {tab:'all',scope:'pgn',series:'',year:'',search:'',page:0};
  state.scope=new URLSearchParams(location.search).get('scope')==='all'?'all':'pgn';
  $('#scopeFilter').value=state.scope;
  const PAGE_SIZE=30;
  let rows, sections, series;
  try {
    const response=await fetch('./data/index/public-events.json');
    if (!response.ok) throw new Error('请稍后重试');
    const payload=await response.json();
    rows=payload.editions || payload.events || [];
    sections=new Map((payload.events || []).map(e=>[e.id,e]));series=payload.series || {};
  } catch(error) {$('#eventsStatus').textContent='赛事目录加载失败：'+error.message;return;}
  const today=new Date().toISOString().slice(0,10);
  const children=e=>(e.sections || []).map(id=>sections.get(id)).filter(Boolean);
  for (const e of rows) e._bucket=!e.date?'unknown':e.date>today?'future':'past';
  rows.sort((a,b)=>String(b.date || '').localeCompare(String(a.date || '')) || (b.gameCount||0)-(a.gameCount||0));
  $('#seriesFilter').innerHTML+=Object.entries(series).map(([k,v])=>`<option value="${esc(k)}">${esc(v)}</option>`).join('');
  $('#yearFilter').innerHTML+=[...new Set(rows.map(e=>e.year).filter(Boolean))].sort().reverse().map(y=>`<option>${esc(y)}</option>`).join('');
  const link=e=>{const routeID = e.tournamentID || e.id;return `./?event=${encodeURIComponent(routeID)}`;};
  function render() {
    const term=state.search.trim().toLocaleLowerCase();
    const matched=rows.filter(e=>(state.tab==='all'||e._bucket===state.tab) && (state.scope!=='pgn'||e.pgnPath)
      &&(state.scope!=='results'||!e.pgnPath)&&(state.series===''||e.series===state.series)
      &&(state.year===''||String(e.year)===state.year)
      &&(!term||[e,...children(e)].some(r=>`${r.displayName} ${r.name} ${r.tournamentID || ''} ${r.groupLabel || ''} ${(r.aliases || []).join(' ')}`.toLowerCase().includes(term))));
    const pages=Math.max(1,Math.ceil(matched.length/PAGE_SIZE));state.page=Math.min(state.page,pages-1);
    $('#eventsBody').innerHTML=matched.slice(state.page*PAGE_SIZE,(state.page+1)*PAGE_SIZE).map(e=>{
      const groups=children(e);
      return `<tr><td>${esc(e.dateBegin && e.dateBegin!==e.date?e.dateBegin+' — '+e.date:e.date || '日期未记录')}</td>
        <td><a href="${link(e)}"><strong>${esc(e.displayName || e.name)}</strong></a>
        ${groups.length?`<details class="event-sections"><summary>已收录 ${e.sectionCount || groups.length} 个组别</summary><div>${groups.map(g=>`<a class="section-link" href="${link(g)}">${esc(g.groupLabel || g.displayName)}${g.timeControl==='rapid'?' · 快棋':g.timeControl==='blitz'?' · 超快棋':''} · ${g.gameCount || 0} 局</a>`).join('')}</div></details>`:''}</td>
        <td>${esc(series[e.series] || e.seriesLabel || '')}</td><td>${e.pgnPath?`<a class="tag full" href="${link(e)}">查看 ${e.gameCount} 局棋谱</a>`:'暂无可查看棋谱'}</td></tr>`;
    }).join('')||'<tr><td colspan="4">暂无符合条件的赛事，可切换“全部赛事”。</td></tr>';
    $('#countInfo').textContent=`${matched.length} 项赛事`;
    $('#pageInfo').textContent=`第 ${state.page+1} / ${pages} 页`;
    $('#prevPage').disabled=state.page===0;$('#nextPage').disabled=state.page>=pages-1;
    $('#eventsStatus').hidden=true;$('#eventsTable').hidden=false;$('#pager').hidden=false;
  }
  document.querySelectorAll('#timeTabs button').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('#timeTabs button').forEach(b=>b.classList.toggle('active',b===button));state.tab=button.dataset.tab;state.page=0;render();
  }));
  for (const [id,key] of [['scopeFilter','scope'],['seriesFilter','series'],['yearFilter','year'],['searchBox','search']])
    $('#'+id).addEventListener(id==='searchBox'?'input':'change',e=>{state[key]=e.target.value;state.page=0;render();});
  $('#prevPage').addEventListener('click',()=>{state.page--;render();});$('#nextPage').addEventListener('click',()=>{state.page++;render();});
  render();
})();
