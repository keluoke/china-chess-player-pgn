#!/usr/bin/env python3
"""Offline public HTML projections; authority stays in registry and snapshot inputs."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import quote
from xml.etree.ElementTree import Element, SubElement, tostring

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = 'https://chessdb.aigclabs.cc'
MAX_PAGES = 14500
EVENT_PAGE_LIMIT = 1150
NAME_PAGE_LIMIT = 1000
CONTROLS = {'standard': '标准棋', 'rapid': '快棋', 'blitz': '超快棋'}
TEMPLATES = ('index.html', 'events.html', 'leaderboards.html', 'master-series.html')

def esc(value): return html.escape(str(value if value is not None else ''), quote=True)
def digest(raw): return hashlib.sha256(raw).hexdigest()
def read(path): return json.loads(path.read_text())
def dump(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
def anchor(url, label): return f'<a href="{esc(url)}">{esc(label)}</a>'
def route_file(route): return 'index.html' if route == '/' else route.strip('/') + '.html'
def table(headers, rows):
    return '<div class="seo-table-wrap"><table><thead><tr>' + ''.join(f'<th scope="col">{esc(h)}</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'
def external_url(value):
    value = str(value or '')
    if value.startswith('/') and not value.startswith('//'): return value
    if value.startswith('https://data.chessdb.aigclabs.cc/'): return value
    return ''
def event_key(event):
    value = str(event.get('tournamentID') or event.get('id') or event.get('eventID') or '')
    match = re.fullmatch(r'chess-results(?::|-tnr)(\d+)', value)
    return match[1] if match else value
def event_slug(key):
    return key if re.fullmatch(r'[a-zA-Z0-9_-]+', key) else 'event-' + digest(key.encode())[:20]

def metadata(route, title, description, graph):
    url = ORIGIN + route
    return f'''<title>{esc(title)} · ChessDB</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="website"><meta property="og:locale" content="zh_CN">
<meta property="og:site_name" content="ChessDB 中国国际象棋棋手数据库">
<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(url)}"><meta property="og:image" content="{ORIGIN}/assets/chessdb-logo-light.png">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{esc(title)}">
<script type="application/ld+json">{dump({'@context':'https://schema.org','@graph':graph})}</script>'''

def footer():
    return '<footer class="seo-footer"><p>包含 Lichess 广播棋谱的部分保留 <a href="https://lichess.org/broadcast">Lichess</a> 署名与 <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0</a> 许可。</p>' + ' · '.join(anchor(p,t) for p,t in [('/players','FIDE 棋手目录'),('/names','中文姓名目录'),('/about','关于本站'),('/methodology','数据与更新方法'),('/developers','数据接口'),('/contribute?type=privacy-request','勘误与隐私请求')]) + '</footer>'

def render_page(route, title, description, body, sid, graph=None):
    graph = list(graph or [])
    graph.append({'@type':'WebPage','@id':ORIGIN+route+'#page','url':ORIGIN+route,'name':title,'inLanguage':'zh-CN'})
    graph.append({'@type':'BreadcrumbList','itemListElement':[{'@type':'ListItem','position':1,'name':'首页','item':ORIGIN+'/'},{'@type':'ListItem','position':2,'name':title,'item':ORIGIN+route}]})
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{metadata(route,title,description,graph)}<meta name="chessdb-snapshot" content="{esc(sid)}">
<link rel="icon" href="/assets/chessdb-favicon-light.png"><script src="/theme.js?v=20260727-1"></script>
<link rel="stylesheet" href="/styles.css?v=20260916-quality"><link rel="stylesheet" href="/seo.css?v=20260918-1"></head>
<body class="coverage-page"><main class="seo-shell"><nav class="page-nav" aria-label="页面导航">{anchor('/','ChessDB')}<div class="seo-nav">{anchor('/events','查棋谱')}{anchor('/leaderboards','棋手排行榜')}{anchor('/master-series','棋协大师赛')}</div></nav>
<header><h1>{esc(title)}</h1><p>{esc(description)}</p></header>{body}{footer()}</main></body></html>'''

def build(root: Path, sid: str, now=None):
    now = now or dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    docs = root/'docs'; target = docs/'data/seo'
    previous=read(target/'manifest.json') if (target/'manifest.json').is_file() else {}
    players = {str(p['fideID']): p for p in read(docs/'data/registry/players.json')}
    registry = read(docs/'data/registry/manifest.json'); month = registry.get('listDate','')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',month): raise ValueError('SEO_REGISTRY_MONTH_MISSING')
    catalog = read(docs/'data/index/public-events.json'); master = read(docs/'data/master-series-summary.json')
    metrics = read(docs/'data/public-metrics.json'); ranks = read(docs/'data/leaderboards.json')
    for data in (catalog,master,metrics):
        if data.get('snapshotId') != sid: raise ValueError('SEO_INPUT_SNAPSHOT_MISMATCH')
    apis = {}
    for path in sorted((docs/'api/v1/player-buckets').glob('*.json')):
        data = read(path)
        if data.get('snapshotId') != sid: raise ValueError('SEO_API_SNAPSHOT_MISMATCH')
        apis.update(data.get('players',{}))
    if not apis: raise ValueError('SEO_PLAYER_BUCKETS_MISSING')
    selected = list(players.values())
    if any(not re.fullmatch(r'[0-9]+', ident) for ident in players): raise ValueError('SEO_INVALID_FIDE_ID')
    selected.sort(key=lambda p:(-int(p.get('standard') or 0),str(p['fideID'])))
    player_routes = {str(p['fideID']):'/players/fide-'+str(p['fideID']) for p in selected}
    def player_link(p):
        ident = str(p.get('fideID','')); authoritative = players.get(ident,p)
        return anchor(player_routes.get(ident,'/?fideID='+quote(ident)),authoritative.get('displayName') or authoritative.get('name') or ident)
    events = [e for e in catalog['events'] if e.get('detailStatus')=='published' or (e.get('pgnPath') and e.get('gameCount',0)>0)]
    events.sort(key=lambda e:(str(e.get('date') or ''),str(e.get('id') or '')),reverse=True)
    # Complete results first; retain the first-release eligible routes, then expand.
    legacy = [e for e in events if e.get('series')=='chess-association-master'][:60]
    ids = {event_key(e) for e in legacy}
    legacy += [e for e in events if e.get('pgnPath') and event_key(e) not in ids][:100]
    published_ids=set(previous.get('routes',{}).get('events',{}))
    ordered = [e for e in events if event_key(e) in published_ids] + legacy + [e for e in events if e.get('detailStatus')=='published'] + events
    # dict retains the FIRST occurrence order; reversing before dedup loses priority.
    unique_events = {event_key(e):e for e in ordered}
    selected_events = list(unique_events.values())[:EVENT_PAGE_LIMIT]
    event_routes = {event_key(e):'/events/'+event_slug(event_key(e)) for e in selected_events}
    def event_link(e):
        key=event_key(e);return anchor(event_routes.get(key,'/?event='+quote(key)),e.get('displayName') or e.get('name') or key)
    def coverage(e): return (e.get('replayCoverage') or {}).get('label') or e.get('pgnStatusLabel') or '棋谱覆盖待核验'
    def event_table(rows):
        return table(['日期','赛事 / 组别','可复盘棋谱','覆盖说明'],[[esc(e.get('date') or '日期未记录'),event_link(e),esc(e.get('gameCount',e.get('playableGames','未统计'))),esc(coverage(e))] for e in rows])
    outputs={}; page_specs={}
    def add(route,title,description,body,graph=None):
        outputs[route_file(route)] = render_page(route,title,description,body,sid,graph)
        page_specs[route]={'title':title}
    info = {
      '/about':('关于 ChessDB','社区维护的中国国际象棋棋手与棋谱数据库。','<h2>我们提供什么</h2><p>查找棋手、了解 FIDE 等级分、按赛事查看成绩并下载可复盘棋谱。本站是社区数据库，不是 FIDE 或中国国际象棋协会的官方网站。</p><h2>更正与反馈</h2><p>如发现姓名、身份或对局记录有误，请通过'+anchor('/contribute','贡献与勘误入口')+'提交线索。未核实的信息不会写成确定事实。</p>'),
      '/methodology':('数据与更新方法','了解等级分月份、棋谱覆盖、统计范围和数据许可。','<h2>姓名与等级分</h2><p>棋手姓名和等级分以已验证的 FIDE 注册表及审核勘误为准。标准棋、快棋、超快棋分别显示；缺失等级分不代表零分。官方榜单生效月与网站更新时间分别记录。排行榜由本站按注册表和自然年龄分组，不代表赛事名次。</p><h2>棋谱覆盖</h2><p>成绩完整不等于棋谱完整。“公开直播范围完整”仅指实际公开的直播台次均已匹配，不代表全台棋谱完整。未公开棋谱、尚待归档、部分棋谱无法复盘分别标注。</p><h2>统计口径</h2><p>独立可复盘棋局按去重后的公共可用棋谱计数；棋手与棋局的关联次数可能把同一局计入双方名下，不能当作独立局数。归档记录包含未进入默认复盘的记录。</p><h2>时间与范围</h2><p>赛事日期是比赛发生时间，榜单月份是等级分生效月份，数据快照时间是本站生成时间。本站收录范围不代表全国全部赛事。</p><h2>许可与署名</h2><p>社区原创审核数据按 CC BY 4.0 提供；Lichess Broadcast 派生数据按 CC BY-SA 4.0 保留署名。其他数据依各自许可与记录处理，不能将整个数据库视为统一授权。</p><p>'+anchor('/PUBLIC_METRICS.md','完整指标定义')+' · '+anchor('/contribute','提交勘误或隐私请求')+'</p>'),
      '/developers':('数据接口与 PGN 使用','公开只读 API、棋谱下载与版本说明。','<h2>开始使用</h2><p>先读取 '+anchor('/api/v1/manifest.json','API manifest')+'，再按棋手详情中的 packages 选择下载。优先使用内容寻址的 publicURL，保留许可和署名。</p><p>'+anchor('/API.md','API 字段与兼容性文档')+' · '+anchor('/data/public-metrics.json','机器可读指标')+'</p><h2>避免误读</h2><p>playableUniqueGames 是独立可复盘棋局；games 保留棋手关联次数口径。榜单生效月不能用文件生成时间替代。请利用缓存，勿因下载失败重新采集赛事来源。</p><h2>PGN 如何使用</h2><p>打开棋手或赛事页面，选择下载棋谱，将 PGN 导入支持国际象棋复盘的软件。可在本站交互式棋盘中先确认所需赛事与对局。</p>')}
    for route,(title,description,body) in info.items(): add(route,title,description,body)
    for p in selected:
        ident=str(p['fideID']); api=apis.get(ident,{}); name=p.get('displayName') or p.get('name') or ident
        count=api.get('playableGameCount',0); desc=f'{name}（FIDE ID {ident}）的官方等级分与本站可复盘棋谱。榜单生效月 {month[:7]}。'
        body=f'<p>已收录可复盘棋谱 <strong>{count}</strong> 局。联邦：{esc(p.get("federation") or "未提供")}。</p>'
        body+=table(['棋种','官方等级分'],[[esc(label),esc(p.get(control) if p.get(control) is not None else '未提供')] for control,label in CONTROLS.items()])
        if p.get('name') and p['name']!=name: body+=f'<p>注册表姓名：{esc(p["name"])}。</p>'
        body+='<p>'+anchor('/?fideID='+ident+'&view=interactive','打开交互式棋手看板与棋盘')+'</p><h2>棋谱下载</h2>'
        body+=('<p>本站暂未收录可复盘棋谱；这不代表该棋手没有参加比赛。</p>' if not count else '')+'<ul>'
        for pkg in api.get('packages',[]):
            url=external_url(pkg.get('publicURL')) or external_url(pkg.get('pgnPath'))
            if url and pkg.get('gameCount',0)>0: body+='<li>'+anchor(url,('全部棋谱' if pkg.get('id')=='all' else ('成年期' if pkg.get('id')=='adult' else str(pkg.get('id'))))+f' · {pkg["gameCount"]} 局 PGN')+'</li>'
        body+='</ul><h2>已收录参赛记录</h2>'
        historical=api.get('events',[])[:15]
        body+=event_table(historical) if historical else '<p>本站暂未提供该棋手的结构化参赛记录。</p>'
        body+='<p>'+anchor('/players','浏览全部 FIDE 棋手')+' · '+anchor('/methodology','了解统计范围与棋谱许可')+'</p>'
        add(player_routes[ident],name+'棋谱与 FIDE 等级分',desc,body,[{'@type':'ProfilePage','mainEntity':{'@type':'Person','@id':ORIGIN+player_routes[ident]+'#person','name':name,'identifier':ident}}])
    def directory_pages(base, title, entries, description):
        chunks=[entries[i:i+100] for i in range(0,len(entries),100)] or [[]]
        routes=[base if i==0 else f'{base}/page/{i+1}' for i in range(len(chunks))]
        for i,chunk in enumerate(chunks):
            # Adjacent pagination makes every entry reachable without giant nav blocks.
            nav=' · '.join(anchor(routes[j],label) for j,label in [(0,'首页'),(i-1,'上一页'),(i+1,'下一页'),(len(chunks)-1,'末页')] if 0<=j<len(routes) and j!=i)
            body=f'<p>第 {i+1} / {len(chunks)} 页，共 {len(entries)} 项。</p><ul>'+''.join('<li>'+item+'</li>' for item in chunk)+'</ul><nav aria-label="目录分页">'+nav+'</nav>'
            add(routes[i],title+(f' · 第 {i+1} 页' if i else ''),description,body)
    directory_pages('/players','FIDE 棋手目录',[player_link(p)+' · FIDE ID '+esc(p['fideID']) for p in sorted(selected,key=lambda p:int(p['fideID']))],'本站注册表内的全部 FIDE 棋手；有无棋谱均提供身份与官方榜单资料。')

    # A name directory is a collection of observations, never an inferred Person.
    domestic_manifest=docs/'data/registry/domestic/manifest.json'
    name_rows={}
    if domestic_manifest.is_file():
        if read(domestic_manifest).get('snapshotId')!=sid: raise ValueError('SEO_DOMESTIC_SNAPSHOT_MISMATCH')
        public_events={event_key(e):e for e in catalog['events'] if e.get('detailStatus')=='published'}
        for shard in sorted((docs/'data/registry/domestic/shards').glob('*.json')):
            for person in read(shard):
                name=person.get('chineseName') or person.get('displayName','')
                if not re.fullmatch(r'[\u4e00-\u9fff]{2,6}',name): continue
                for sighting in person.get('sightings',[]):
                    key=event_key(sighting);event=public_events.get(key)
                    number=str(sighting.get('playerNo') or '')
                    if not event or not number.isdigit():continue
                    # A public event roster row is a fact, not cross-event identity proof.
                    name_rows.setdefault(name,{})[(key,number)]={'event':event,'playerNo':number,'rank':sighting.get('rank'),'score':sighting.get('score')}
    name_limit=min(NAME_PAGE_LIMIT,max(0,MAX_PAGES-len(players)-len(selected_events)-(len(players)+99)//100-200))
    eligible_names=sorted(((name,list(rows.values())) for name,rows in name_rows.items() if len(rows)>=2),key=lambda item:(-len(item[1]),item[0]))[:name_limit]
    name_routes={name:'/names/name-'+digest(name.encode())[:20] for name,_ in eligible_names}
    for name,rows in eligible_names:
        rows.sort(key=lambda r:(str(r['event'].get('date') or ''),event_key(r['event']),r['playerNo']),reverse=True)
        body=f'<p>已收录 {len(rows)} 条同名参赛记录。姓名相同不代表同一人；以下记录不合并为个人履历，也不推断 FIDE 身份。</p>'
        # Exact registry names only, no presentation suggestions promoted to authority.
        matches=[p for p in selected if name in (p.get('chineseName'),p.get('displayName'))]
        if matches:body+='<h2>注册表中的同名棋手</h2><p>'+ ' · '.join(player_link(p)+'（'+esc(p['fideID'])+'）' for p in matches)+'</p><p>同名参赛记录不自动归属于以上棋手。</p>'
        body+='<h2>同名参赛记录</h2>'+table(['日期','赛事 / 组别','名单编号','名次','积分'],[[esc(r['event'].get('date') or '日期未记录'),event_link(r['event']),esc(r['playerNo']),esc(r['rank'] or '—'),esc(r['score'] if r['score'] is not None else '—')] for r in rows])
        body+='<p>'+anchor('/?q='+quote(name),'在站内继续检索此姓名')+' · '+anchor('/names','中文姓名目录')+'</p>'
        add(name_routes[name],name+' · 国际象棋同名参赛记录','按公开赛事与名单编号区分记录；此页为姓名目录，不是已确认的个人档案。',body,[{'@type':'CollectionPage','name':name+'同名参赛记录'}])
    directory_pages('/names','中文姓名目录',[anchor(name_routes[name],name)+f' · {len(rows)} 条参赛记录' for name,rows in sorted(eligible_names)],'有公开赛事记录的中文姓名索引；同名不代表同一棋手。')
    for e in selected_events:
        key=event_key(e); title=e.get('displayName') or e.get('name') or key
        desc=f'{title}的已收录赛事信息与棋谱。{coverage(e)}。'
        body=table(['日期','组别','参赛人数','轮次','可复盘棋谱'],[[esc(e.get('date') or '未记录'),esc(e.get('groupLabel') or '未标注'),esc(e.get('participants') if e.get('participants') is not None else '未记录'),esc(e.get('rounds') or '未记录'),esc(e.get('gameCount',0))]])
        body+='<p>'+anchor('/?event='+quote(key)+'&view=interactive','查看成绩、逐轮对阵与交互式棋谱')+'</p>'
        if e.get('pgnPath'):
            url='/'+str(e['pgnPath']).lstrip('/')
            if url.startswith('/api/event-pgn?'):body+='<p>'+anchor(url,'下载本赛事 PGN')+'</p>'
        if e.get('attribution'):body+='<p>'+esc(e['attribution'])+'</p>'
        if e.get('license'):body+='<p>许可：'+esc(e['license'])+'</p>'
        # Copy only explicitly public structured rows, never raw capture evidence.
        detail_path=e.get('detailPath') or ''
        if re.fullmatch(r'data/index/event-details/[A-Za-z0-9_-]+\.json',detail_path) and (docs/detail_path).is_file():
            detail=read(docs/detail_path)
            if detail.get('snapshotId')!=sid: raise ValueError('SEO_DETAIL_SNAPSHOT_MISMATCH')
            standings=detail.get('standings',[])[:15]
            roster={str(p.get('playerNo')):p for p in detail.get('players',[])}
            rows=[]
            for r in standings:
                rp=roster.get(str(r.get('playerNo')),{})
                ident=str(rp.get('fideID') or '');name=players.get(ident,{}).get('displayName') or rp.get('name') or r.get('name') or '姓名未记录'
                rows.append([esc(r.get('rank') or r.get('place') or '—'),player_link(players[ident]) if ident in players else esc(name),esc(r.get('points',r.get('score')) if r.get('points',r.get('score')) is not None else '—')])
            if rows:body+='<h2>成绩摘要</h2>'+table(['名次','棋手','积分'],rows)
        body+='<p>'+anchor('/events','查找其他赛事')+' · '+anchor('/methodology','棋谱覆盖如何计量')+'</p>'
        graph=[{'@type':'SportsEvent','name':title,'url':ORIGIN+event_routes[key],**({'startDate':e['date']} if e.get('date') else {})}]
        add(event_routes[key],title,desc,body,graph)
    rank_links=[]
    for g in ranks['groups']:
        for control,label in CONTROLS.items():
            rows=g.get('rankings',{}).get(control,{}).get('all',{}).get('players',[])[:30]
            if not rows:continue
            route=f'/leaderboards/{control}/{str(g["id"]).lower()}'
            title=f'中国棋手 {g["label"]} {label}等级分榜'
            desc=f'榜单生效月 {month[:7]}。按 {ranks["basisYear"]} 年自然年龄分组（{g["minAge"]} 岁'+(f'至 {g["maxAge"]} 岁' if g.get('maxAge') is not None else '及以上')+'），排除标记为不活跃的棋手；包含注册表覆盖的转出棋手。'
            body=table(['排序','棋手','等级分'],[[str(i),player_link(p),esc(players[str(p['fideID'])].get(control) if players[str(p['fideID'])].get(control) is not None else '未提供')] for i,p in enumerate(rows,1)])
            body+='<p>'+anchor('/leaderboards?cohort='+quote(str(g['id']))+'&control='+control,'切换年龄、性别和出生年份')+'</p>'
            add(route,title,desc,body);rank_links.append((route,g['label']+' · '+label))
    year_links=[];station_links={}
    for year in master['years']:
        yr=str(year['year']); route='/master-series/'+yr; year_links.append((route,yr+' 年'))
        station_rows=[]
        for station in year['stations']:
            label=station['station']
            # Unknown stations stay in the existing diagnostic area, not landing pages.
            if '待核' in label or '未知' in label: continue
            slug='station-'+digest((yr+'|'+label).encode())[:12]; sr=route+'/'+slug
            station_links[(yr,label)]=sr
            groups=[{**g,'displayName':g.get('groupLabel') or '组别待核','gameCount':g.get('playableGames',0)} for g in station['groups']]
            body=event_table(groups)+'<p>'+anchor(route,'查看本年度其他赛站')+'</p>'
            add(sr,yr+' 棋协大师赛'+label,'已收录组别的成绩与棋谱覆盖；本站记录不代表全部赛站。',body)
            station_rows.append([anchor(sr,label),esc(station['groupCount']),esc(station.get('latestDate') or '日期未记录')])
        body=table(['赛站','已收录组别','最近比赛日期'],station_rows)+'<p>'+anchor('/master-series','全部年份与交互筛选')+'</p>'
        add(route,yr+' 棋协大师赛','按赛站查看已收录组别、赛事成绩与棋谱覆盖。',body)
    catalog_rows=selected_events;chunks=[catalog_rows[i:i+25] for i in range(0,len(catalog_rows),25)] or [[]]
    def event_paging(index):
        links=[]
        for i,label in [(0,'首页'),(index-1,'上一页'),(index+1,'下一页'),(len(chunks)-1,'末页')]:
            if 0<=i<len(chunks) and i!=index:links.append(anchor('/events' if i==0 else f'/events/page/{i+1}',label))
        return '<nav class="seo-links" aria-label="精选赛事分页">'+f'第 {index+1} / {len(chunks)} 页 · '+' · '.join(links)+'</nav>'
    paging=event_paging(0)
    for i,chunk in enumerate(chunks[1:],2):add(f'/events/page/{i}',f'精选赛事目录 · 第 {i} 页','按赛事查找已收录成绩与可用棋谱；完整目录可在查棋谱页面筛选。',event_table(chunk)+event_paging(i-1))
    totals=metrics['totals']
    blocks={
      'index.html':('<h2>查找棋手与比赛</h2><p>已收录 '+esc(totals['playersWithGames'])+' 名有棋谱棋手，公共目录提供 '+esc(totals['playableUniqueGames'])+' 局独立可复盘棋谱。</p><div class="seo-links">'+''.join(player_link(p) for p in selected[:8])+'</div><p>'+anchor('/players','全部 FIDE 棋手目录')+' · '+anchor('/names','中文姓名目录')+'</p>'),
      'events.html':'<h2>精选赛事目录</h2>'+event_table(chunks[0])+paging,
      'leaderboards.html':'<h2>标准棋 · 成年组</h2>'+table(['排序','棋手','等级分'],[[str(i),player_link(p),esc(players[str(p['fideID'])].get('standard'))] for i,p in enumerate(next(g for g in ranks['groups'] if g['id']=='OPEN')['rankings']['standard']['all']['players'][:20],1)])+f'<p>官方榜单生效月：{month[:7]}。自然年龄、非赛事名次；含注册表覆盖的转出棋手。</p>',
      'master-series.html':'<h2>按年份查看赛站</h2><div class="seo-links">'+''.join(anchor(p,l) for p,l in year_links)+'</div>'+''.join('<h3>'+esc(y['year'])+' 年</h3><ul>'+''.join('<li>'+anchor(station_links[(str(y['year']),s['station'])],s['station'])+' · '+esc(s['groupCount'])+' 个已收录组别</li>' for s in y['stations'] if (str(y['year']),s['station']) in station_links)+'</ul>' for y in master['years'])}
    for template in TEMPLATES:
        route='/' if template=='index.html' else '/'+template.removesuffix('.html')
        raw=(docs/template).read_text(); title=re.search(r'<title>(.*?)</title>',raw,re.S)[1].split(' · ')[0]
        description=re.search(r'<meta name="description" content="([^"]*)"',raw)[1]
        raw=re.sub(r'<title>.*?</title>','',raw,flags=re.S);raw=re.sub(r'\s*<meta name="(?:description|robots)"[^>]*>','',raw)
        raw=raw.replace('</head>',metadata(route,title,description,[{'@type':'WebSite' if route=='/' else 'CollectionPage','@id':ORIGIN+route,'url':ORIGIN+route,'name':title}])+f'<meta name="chessdb-snapshot" content="{esc(sid)}"><link rel="stylesheet" href="/seo.css?v=20260918-1"><script src="/seo.js?v=20260918-1" defer></script></head>')
        extra=('<details class="seo-directory"><summary>按年龄与棋种查看榜单</summary><div class="seo-links">'+''.join(anchor(p,l) for p,l in rank_links)+'</div></details>') if template=='leaderboards.html' else ''
        fragment=f'<noscript><style>#eventsStatus,#reportLoading,.events-toolbar,#timeTabs,.leaderboard-filters,#refreshButton{{display:none!important}}</style></noscript><section id="seo-content" class="seo-content">{blocks[template]}</section>{extra}{footer()}'
        if template=='master-series.html':
            fragment+='<nav class="seo-links" aria-label="年度赛事页面">'+''.join(anchor(p,l) for p,l in year_links)+'</nav>'
        if template=='index.html':
            raw=raw.replace('</body>','<div class="seo-home-directory">'+fragment+'</div></body>',1)
        else:
            raw=raw.replace('</main>',fragment+'</main>',1)
        outputs[template]=raw;page_specs[route]={'title':title}
    if len(page_specs)>MAX_PAGES:raise ValueError('SEO_PAGE_BUDGET_EXCEEDED')
    # Previous metadata preserves published URL allocation and semantic dates, never facts.
    old={p['route']:p for p in previous.get('pages',[])};pages=[]
    for route, spec in page_specs.items():
        path=route_file(route);raw=outputs[path].encode();semantic=re.sub(r'<meta name="chessdb-snapshot"[^>]*>','',outputs[path])
        # Snapshot cache-busting values are excluded only from the metadata comparison.
        sh=digest(semantic.encode());prev=old.get(route,{})
        lastmod=prev.get('lastmod',now) if prev.get('contentSha256')==sh else now
        pages.append({'route':route,'file':path,'sha256':digest(raw),'bytes':len(raw),'contentSha256':sh,'lastmod':lastmod,**spec})
    urlset=Element('urlset',xmlns='http://www.sitemaps.org/schemas/sitemap/0.9')
    for p in sorted(pages,key=lambda p:p['route']):
        u=SubElement(urlset,'url');SubElement(u,'loc').text=ORIGIN+p['route'];SubElement(u,'lastmod').text=p['lastmod']
    outputs['sitemap.xml']=tostring(urlset,encoding='unicode',xml_declaration=True)
    outputs['llms.txt']='# ChessDB 中国国际象棋棋手数据库\n\n公开棋手、FIDE 等级分、赛事成绩与棋谱；完整性以各页标注为准。\n\n'+''.join(f'- [{label}]({ORIGIN}{route})\n' for route,label in [('/players','全部 FIDE 棋手'),('/names','中文姓名目录'),('/events','查棋谱'),('/leaderboards','棋手排行榜'),('/master-series','棋协大师赛'),('/methodology','口径与许可'),('/developers','API 与 PGN')])
    templates={name:digest((docs/name).read_bytes()) for name in TEMPLATES}
    assets={name:digest((docs/name).read_bytes()) for name in ('seo.css','seo.js')}
    manifest={'schemaVersion':1,'snapshotId':sid,'origin':ORIGIN,'generatedAt':now,'registryListDate':month,'templates':templates,'assets':assets,'builderSha256':digest(Path(__file__).read_bytes()),'pages':sorted(pages,key=lambda p:p['route']),'routes':{'players':player_routes,'events':event_routes,'names':name_routes},'coverage':{'registryPlayers':len(players),'playerPages':len(player_routes),'eventPages':len(event_routes),'namePages':len(name_routes),'maxPages':MAX_PAGES},'files':[{'file':name,'sha256':digest(body.encode()),'bytes':len(body.encode())} for name,body in sorted(outputs.items())]}
    target.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.seo-',dir=target.parent))
    try:
        for name,body in outputs.items():
            p=staging/'output'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
        (staging/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        # Validate before touching the previous generated tree.
        from validate_seo_pages import validate
        validate(root,seo_root=staging,expected_snapshot=sid,check_snapshot=False)
        if target.exists():shutil.rmtree(target)
        staging.rename(target)
    finally:
        if staging.exists():shutil.rmtree(staging)
    print(json.dumps({'snapshotId':sid,'seoPages':len(pages)},ensure_ascii=False))
    return manifest

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT);args=parser.parse_args()
    from snapshot_context import snapshot_id
    build(args.root,snapshot_id())
if __name__=='__main__':main()
