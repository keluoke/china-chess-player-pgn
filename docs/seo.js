// Keep the readable server projection until the interactive view is ready.
const fallback = document.getElementById('seo-content');
if (fallback) {
  const ready = () => {
    const events = document.getElementById('eventsTable');
    const ranking = document.querySelector('#leaderboardPage .leaderboard-card');
    const master = document.getElementById('reportContent');
    if ((events && !events.hidden) || ranking || (master && !master.hidden)) {
      fallback.hidden = true;
      observer.disconnect();
    }
  };
  const observer = new MutationObserver(ready);
  observer.observe(document.body, {childList:true,subtree:true,attributes:true,attributeFilter:['hidden']});
  ready();
}
