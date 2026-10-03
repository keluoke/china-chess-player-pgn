# 公开网站与本机工作台 UI 改造

公开网站采用暖灰背景、白色内容面板、深蓝操作色和统一排版。首页以搜索与三个探索入口为主；赛事目录、排行榜、大师赛、妙手、数据覆盖、贡献表单及 404 页面共享 `docs/ui.css`。棋手和赛事详情继承同一视觉层，保留已有数据与交互逻辑。独立生成的 SEO 页面也引用该样式；SEO manifest 将新样式纳入资产哈希校验。

本机面板采用侧栏与六个独立工作分区：赛事采集、目标队列、已抓赛事、发布中心、维护与诊断、运行日志。当前任务状态与自动生产发布开关跨分区保留，任务运行中仍能切换分区。发布中心明确区分本机缓存与云端月度维护，不将本机缓存年龄描述成生产更新状态。发现、采集、恢复、投递及回执仍使用原命令和服务端门禁。本地预览从队列或历史分区切回采集分区，表格在窄窗口内独立滚动。

## 验证

- 在临时校验区以代码工作区提交 `2915170f6f` 的完整 SEO 产物和当前修改复跑 239 项测试：local pipeline、Chess-Results parser、docs consistency、SEO pages、frontend initialization、collector runtime，全部通过。临时区不承载采集或投递。
- Search core、game quality、presentation names 的 Node 测试通过；Python compileall、嵌入面板 JavaScript 语法、refresh.sh shell 语法和 diff whitespace 检查通过。
- 浏览器验证棋手搜索到侯逸凡 FIDE 8602980 档案、赛事年份/关键词筛选、排行榜数据、大师赛统计、妙手棋盘与列表、面板导航和本地赛事预览。
- 在 390px 视口验证首页、赛事目录、排行榜、大师赛、妙手、数据覆盖、贡献表单及面板赛事预览的页面宽度，均无整页横向溢出；核对浅色与深色视觉。
- 公开预览使用代码工作区同一提交的派生快照。采集面板只读预览读取本机原有记录，不执行抓取或投递。

## 双工作区交付边界

代码只在 `kimi-code/main` 修改。面板部署到采集工作区须使用 `collector-runtime-plan` 与 `collector-runtime-sync`；不得复制单文件或绕过清单校验。`ui.css` 已加入 core profile 的 control-input 白名单与安装器精确路径许可。已完成受控安装，panel profile 的 98 个文件验证通过；最终安装来源为 `ac8e77f240`；重启后实际面板在 127.0.0.1:8764 启动（入口按端口文件发现当前服务），首页和 ping/state/queue/events/outbox/automation 均返回 HTTP 200。

本次不修改人工数据、赛事采集产物或 outbox；受控安装同步运行时和白名单内控制输入（包含公共赛事目录），并未访问采集来源。生产网站仍需后续通过既有 CI → 统一 rebuild → R2 回执认证 → deploy 流程发布；本地浏览器验证不等于线上验证。移动端检查是浏览器视口验证，不代表 iOS/Android 真机验收。
