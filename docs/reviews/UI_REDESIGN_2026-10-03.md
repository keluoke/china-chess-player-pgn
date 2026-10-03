# 公开网站与本机工作台 UI 改造

公开网站采用暖灰背景、白色内容面板、深蓝操作色和统一排版。首页以搜索与三个探索入口为主；赛事目录、排行榜、大师赛、妙手、数据覆盖、贡献表单及 404 页面共享 `docs/ui.css`。棋手和赛事详情继承同一视觉层，保留已有数据与交互逻辑。独立生成的 SEO 页面也引用该样式；SEO manifest 将新样式纳入资产哈希校验。

本机面板采用侧栏与六个独立工作分区：赛事采集、目标队列、已抓赛事、发布中心、维护与诊断、运行日志。当前任务状态与自动生产发布开关跨分区保留，任务运行中仍能切换分区。发现、采集、恢复、投递及回执仍使用原命令和服务端门禁。本地预览从队列或历史分区切回采集分区，表格在窄窗口内独立滚动。

## 验证

- 在临时校验区以代码工作区提交 `2915170f6f` 的完整 SEO 产物和当前修改复跑 235 项测试：local pipeline、Chess-Results parser、docs consistency、SEO pages、frontend initialization，全部通过。临时区不承载采集或投递。
- Search core、game quality、presentation names 的 Node 测试通过；Python compileall、嵌入面板 JavaScript 语法、refresh.sh shell 语法和 diff whitespace 检查通过。
- 浏览器验证棋手搜索到侯逸凡 FIDE 8602980 档案、赛事年份/关键词筛选、排行榜数据、大师赛统计、妙手棋盘与列表、面板导航和本地赛事预览。
- 在 390px 视口验证首页、赛事目录、排行榜、大师赛、妙手、数据覆盖、贡献表单及面板赛事预览的页面宽度，均无整页横向溢出；核对浅色与深色视觉。
- 公开预览使用代码工作区同一提交的派生快照。采集面板只读预览读取本机原有记录，不执行抓取或投递。

## 双工作区交付边界

代码只在 `kimi-code/main` 修改。面板部署到采集工作区须使用 `collector-runtime-plan` 与 `collector-runtime-sync`；不得复制单文件或绕过清单校验。`ui.css` 已加入 core profile 的 control-input 白名单。

本次不修改人工数据、机器数据或 outbox。生产网站仍需后续通过既有 CI → 统一 rebuild → R2 回执认证 → deploy 流程发布；本地浏览器验证不等于线上验证。移动端检查是浏览器视口验证，不代表 iOS/Android 真机验收。
