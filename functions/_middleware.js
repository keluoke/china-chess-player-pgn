const ORIGIN = 'https://chessdb.aigclabs.cc';
const ALIASES = new Set(['4chess.cc','www.4chess.cc','china-chess-player-pgn.pages.dev']);
const htmlPath = path => path === '/' || /^\/(?:index|events|leaderboards|master-series|players|names|about|methodology|developers|coverage|contribute)(?:\.html|\/.*)?$/.test(path);
export async function onRequest(context) {
  const url = new URL(context.request.url);
  if (!['GET','HEAD'].includes(context.request.method) || !htmlPath(url.pathname)) return context.next();
  const preview = url.hostname.endsWith('.pages.dev') && !ALIASES.has(url.hostname);
  const params = url.searchParams;
  if (!preview && ['/', '/index.html'].includes(url.pathname) && params.get('view') !== 'interactive' && !params.has('q') && !params.has('eventFocus') && !params.has('round') && !params.has('player')) {
    const player = params.get('fideID'); const event = params.get('event');
    if (player || event) {
      // Only certified generated entities may replace old links.
      try {
        const response = await context.env.ASSETS.fetch(new URL('/data/seo-routes.json', url));
        if (response.ok && response.headers.get('content-type')?.includes('json')) {
          const data = await response.json();
          const target = player ? data.players?.[player] : data.events?.[event];
          if (typeof target === 'string' && /^\/(players|events)\/[a-zA-Z0-9_-]+$/.test(target)) {
            const next = new URL(target,ORIGIN);
            for (const key of ['eventFocus','round']) if(params.has(key)) next.searchParams.set(key,params.get(key));
            return Response.redirect(next.href,308);
          }
        }
      } catch { /* Retain the existing interactive route on a transient asset failure. */ }
    }
  }
  if (ALIASES.has(url.hostname)) {
    const path = url.pathname === '/index.html' ? '/' : url.pathname.replace(/\.html$/, '');
    return Response.redirect(ORIGIN + path + url.search, 308);
  }
  const response = await context.next();
  const headers = new Headers(response.headers);
  const meaningfulParams = [...params.keys()].filter(k=>!['utm_source','utm_medium','utm_campaign','v'].includes(k));
  if (preview || meaningfulParams.length) headers.set('X-Robots-Tag','noindex, follow');
  return new Response(response.body,{status:response.status,statusText:response.statusText,headers});
}
