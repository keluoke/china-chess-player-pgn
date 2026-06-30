import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        NavigationSplitView {
            SearchSidebar()
                .navigationSplitViewColumnWidth(min: 320, ideal: 350, max: 420)
        } detail: {
            HSplitView {
                EventListView()
                    .frame(minWidth: 620, idealWidth: 760, maxWidth: .infinity, maxHeight: .infinity)
                DownloadPanel()
                    .frame(minWidth: 300, idealWidth: 360, maxWidth: 430, maxHeight: .infinity)
            }
            .background(Color.appCanvas)
        }
        .tint(.appAccent)
    }
}

private struct SearchSidebar: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("中国棋手 PGN")
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Text(store.statusText)
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(2)
                    .frame(minHeight: 34, alignment: .topLeading)
            }
            .padding(.top, 8)

            HomeSidebarButton()

            VStack(alignment: .leading, spacing: 10) {
                Text("棋手搜索")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)

                HStack(spacing: 8) {
                    SearchInputField(
                        text: $store.query,
                        placeholder: "王皓 / wang hao / 8602883"
                    ) {
                        store.search()
                    }

                    Button {
                        store.search()
                    } label: {
                        if store.isSearching {
                            ProgressView()
                                .controlSize(.small)
                                .frame(width: 22, height: 22)
                        } else {
                            Image(systemName: "magnifyingglass")
                                .frame(width: 22, height: 22)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(store.isSearching)
                    .help("搜索")
                }

                Toggle("联网补齐本地结果", isOn: $store.autoRefreshOnline)
                    .font(.callout)
                    .foregroundStyle(Color.appText)

                Toggle("包含测试赛事", isOn: $store.includeLikelyTestEvents)
                    .font(.callout)
                    .foregroundStyle(Color.appText)
            }

            Divider()

            HStack {
                Label("候选棋手", systemImage: "person.2")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
                Spacer()
                Text("\(store.candidates.count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appTextSecondary)
            }

            if store.candidates.isEmpty {
                EmptySidebarState()
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(store.candidates) { candidate in
                            CandidateRow(
                                candidate: candidate,
                                isSelected: candidate.id == store.selectedCandidateID
                            ) {
                                store.selectCandidate(candidate)
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }
            }

            Spacer(minLength: 8)

            VStack(alignment: .leading, spacing: 6) {
                Text("数据源")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
                Label("Chess-Results 球员/对局数据库", systemImage: "globe.asia.australia")
                    .font(.callout)
                    .foregroundStyle(Color.appText)
                    .lineLimit(1)
                Label("FIDE 资料链接用于区分同名", systemImage: "link")
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(1)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color.panelBorder)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .padding(18)
        .background(Color.sidebarBackground)
    }
}

private struct SearchInputField: View {
    @Binding var text: String
    let placeholder: String
    let onSubmit: () -> Void

    @FocusState private var isFocused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.callout.weight(.semibold))
                .foregroundStyle(isFocused ? Color.appAccent : Color.appTextSecondary)
                .frame(width: 18)

            TextField(
                "",
                text: $text,
                prompt: Text(placeholder).foregroundStyle(Color.appPlaceholder)
            )
            .textFieldStyle(.plain)
            .font(.callout)
            .foregroundStyle(Color.appText)
            .focused($isFocused)
            .onSubmit(onSubmit)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(minHeight: 38)
        .background(Color.inputBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(isFocused ? Color.appAccentStrong : Color.panelBorder, lineWidth: isFocused ? 1.6 : 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .frame(maxWidth: .infinity)
    }
}

private struct HomeSidebarButton: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        Button {
            store.showHome()
        } label: {
            HStack(spacing: 8) {
                Image(systemName: "house")
                    .frame(width: 20)
                Text("首页")
                    .font(.callout.weight(.medium))
                Spacer()
                Text("\(store.youthLeaderboardEntryCount)")
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
            }
            .foregroundStyle(Color.appText)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(store.selectedCandidateID == nil ? Color.selectionBackground : Color.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(store.selectedCandidateID == nil ? Color.appAccent : Color.panelBorder)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
        .help("首页")
    }
}

private struct CandidateRow: View {
    let candidate: PlayerCandidate
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text(candidate.displayName)
                        .font(.headline)
                        .foregroundStyle(Color.appText)
                        .lineLimit(1)
                    Spacer()
                    Text("\(candidate.eventCount)")
                        .font(.caption.monospacedDigit().weight(.semibold))
                        .foregroundStyle(isSelected ? Color.appAccent : .secondary)
                }

                Text(candidate.detailLine)
                    .font(.caption)
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(2)

                if !candidate.clubs.isEmpty {
                    Text(candidate.clubs.joined(separator: " / "))
                        .font(.caption2)
                        .foregroundStyle(Color.appTextSecondary)
                        .lineLimit(1)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(isSelected ? Color.selectionBackground : Color.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(isSelected ? Color.appAccent : Color.panelBorder)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

private struct EmptySidebarState: View {
    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: "person.crop.circle.badge.questionmark")
                .font(.system(size: 34))
                .foregroundStyle(Color.appTextSecondary)
            Text("暂无候选棋手")
                .font(.headline)
                .foregroundStyle(Color.appText)
            Text("搜索后会按 FIDE ID 聚合重名棋手")
                .font(.callout)
                .foregroundStyle(Color.appTextSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 180)
    }
}

private struct EventListView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        Group {
            if store.selectedCandidate == nil {
                HomeView()
            } else {
                PlayerDetailView()
            }
        }
        .background(Color.appCanvas)
    }
}

private struct HomeView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("首页")
                            .font(.largeTitle.weight(.semibold))
                            .foregroundStyle(Color.appText)
                        Text("U8-U18 FIDE 排行榜")
                            .font(.title3)
                            .foregroundStyle(Color.appTextSecondary)
                    }

                    Spacer()

                    Button {
                        store.refreshDatabaseStats()
                        store.loadHomepage()
                    } label: {
                        Image(systemName: "arrow.clockwise")
                            .frame(width: 22, height: 22)
                    }
                    .buttonStyle(.bordered)
                    .help("刷新")
                }

                HomeCacheSummaryView()

                YouthLeaderboardsSection()
            }
            .padding(24)
        }
    }
}

private struct HomeCacheSummaryView: View {
    @EnvironmentObject private var store: AppStore

    private let columns = [
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("本地缓存")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                Button {
                    store.revealDatabaseFolder()
                } label: {
                    Label("目录", systemImage: "folder")
                }
                .buttonStyle(.bordered)
            }

            LazyVGrid(columns: columns, spacing: 10) {
                DashboardMetricCard(title: "棋手", value: "\(store.databaseStats.players)", subtitle: "唯一 ID", symbol: "person.crop.circle")
                DashboardMetricCard(title: "赛事", value: "\(store.databaseStats.events)", subtitle: "索引", symbol: "calendar")
                DashboardMetricCard(title: "PGN", value: "\(store.databaseStats.pgnArchives)", subtitle: store.databaseStats.pgnSizeText, symbol: "archivebox")
                DashboardMetricCard(title: "棋局", value: "\(store.databaseStats.games)", subtitle: "已解析", symbol: "checkerboard.rectangle")
            }
        }
        .padding(16)
        .background(Color.panelBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct YouthLeaderboardsSection: View {
    @EnvironmentObject private var store: AppStore

    private let columns = [
        GridItem(.adaptive(minimum: 330), spacing: 12)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("青少年 FIDE 排行榜")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                Text("\(store.youthLeaderboardEntryCount) 名")
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
            }

            LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
                ForEach(store.youthLeaderboards) { leaderboard in
                    YouthLeaderboardCard(leaderboard: leaderboard) { candidate in
                        store.selectCandidate(candidate)
                    }
                }
            }
        }
    }
}

private struct YouthLeaderboardCard: View {
    let leaderboard: YouthLeaderboard
    let action: (PlayerCandidate) -> Void

    @State private var animateBars = false

    private var maxRating: Int {
        max(leaderboard.entries.map(\.rating).max() ?? 1, 1)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(leaderboard.stage.rawValue)
                    .font(.title.weight(.bold))
                    .foregroundStyle(Color.appAccentStrong)
                VStack(alignment: .leading, spacing: 2) {
                    Text("FIDE ELO")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(Color.appText)
                    Text("本地缓存排名")
                        .font(.caption2)
                        .foregroundStyle(Color.appTextSecondary)
                }
                Spacer()
                Image(systemName: "chart.line.uptrend.xyaxis")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appAccent)
            }

            if leaderboard.entries.isEmpty {
                Text("待补缓存")
                    .font(.callout.weight(.medium))
                    .foregroundStyle(Color.appTextSecondary)
                    .frame(maxWidth: .infinity, minHeight: 236, alignment: .center)
            } else {
                VStack(spacing: 8) {
                    ForEach(leaderboard.entries) { entry in
                        YouthLeaderboardRow(
                            entry: entry,
                            maxRating: maxRating,
                            animateBar: animateBars
                        ) {
                            action(entry.candidate)
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, minHeight: 332, alignment: .topLeading)
        .background(
            LinearGradient(
                colors: [Color.panelBackground, Color.selectionBackground.opacity(0.55)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .shadow(color: Color.appAccentStrong.opacity(0.08), radius: 14, y: 6)
        .onAppear {
            withAnimation(.spring(response: 0.65, dampingFraction: 0.82).delay(0.08)) {
                animateBars = true
            }
        }
    }
}

private struct YouthLeaderboardRow: View {
    let entry: YouthLeaderboardEntry
    let maxRating: Int
    let animateBar: Bool
    let action: () -> Void

    private var barRatio: CGFloat {
        guard maxRating > 0 else { return 0 }
        return animateBar ? CGFloat(entry.rating) / CGFloat(maxRating) : 0
    }

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 7) {
                HStack(alignment: .center, spacing: 10) {
                    Text("\(entry.rank)")
                        .font(.caption.monospacedDigit().weight(.bold))
                        .foregroundStyle(Color.appTextOnAccent)
                        .frame(width: 24, height: 24)
                        .background(rankColor)
                        .clipShape(Circle())

                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.candidate.displayName)
                            .font(.callout.weight(.semibold))
                            .foregroundStyle(Color.appText)
                            .lineLimit(1)
                            .minimumScaleFactor(0.82)
                        Text(entry.playerMetaText)
                            .font(.caption2)
                            .foregroundStyle(Color.appTextSecondary)
                            .lineLimit(1)
                    }

                    Spacer(minLength: 8)

                    VStack(alignment: .trailing, spacing: 2) {
                        Text("\(entry.rating)")
                            .font(.callout.monospacedDigit().weight(.bold))
                            .foregroundStyle(Color.appText)
                        Text(entry.ratingKindText)
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(Color.appAccent)
                    }
                    .frame(width: 54, alignment: .trailing)
                }

                GeometryReader { proxy in
                    ZStack(alignment: .leading) {
                        Capsule()
                            .fill(Color.panelBorder.opacity(0.45))
                        Capsule()
                            .fill(Color.appAccent)
                            .frame(width: proxy.size.width * barRatio)
                    }
                }
                .frame(height: 5)

                if let note = entry.note {
                    Text(note)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(Color.stageFutureAccent)
                        .lineLimit(1)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(Color.stageFutureBackground)
                        .clipShape(Capsule())
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .background(Color.panelBackground.opacity(0.78))
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private var rankColor: Color {
        switch entry.rank {
        case 1: Color.appAccentStrong
        case 2: Color.appAccent
        case 3: Color.stageFutureAccent
        default: Color.appTextSecondary
        }
    }
}

private struct RecommendedYouthCard: View {
    let item: RecommendedYouthPlayer
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .firstTextBaseline) {
                    Text(item.displayName)
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Color.appText)
                        .lineLimit(1)
                    Spacer()
                    Text(item.seed.birthYear)
                        .font(.caption.monospacedDigit().weight(.semibold))
                        .foregroundStyle(Color.appTextSecondary)
                }

                Text(item.subtitle)
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(1)

                Text(item.seed.focus)
                    .font(.callout)
                    .foregroundStyle(Color.appText)
                    .lineLimit(2)
                    .frame(minHeight: 38, alignment: .topLeading)

                HStack(spacing: 6) {
                    ForEach(Array(item.seed.tags.prefix(3)), id: \.self) { tag in
                        TagPill(text: tag)
                    }
                }

                Divider()

                HStack(spacing: 8) {
                    MiniMetric(title: "赛事", value: "\(item.dashboard.eventCount)")
                    MiniMetric(title: "前三", value: "\(item.dashboard.topThreeCount)")
                    MiniMetric(title: "缓存", value: "\(item.dashboard.cachedGames)")
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, minHeight: 210, alignment: .topLeading)
            .background(Color.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color.panelBorder)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

private struct PlayerDetailView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(spacing: 0) {
            EventToolbar()
            Divider()

            if let candidate = store.selectedCandidate {
                ScrollView {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        PlayerDashboardView(candidate: candidate, stats: store.selectedDashboardStats)
                            .padding(18)

                        if candidate.events.isEmpty {
                            ContentUnavailableView("没有近十年赛事", systemImage: "calendar.badge.exclamationmark")
                                .frame(minHeight: 280)
                        } else {
                            Section {
                                ForEach(candidate.events) { event in
                                    EventRow(
                                        event: event,
                                        isSelected: store.selectedEventIDs.contains(event.id)
                                    ) {
                                        store.toggleEvent(event)
                                    }
                                }
                            } header: {
                                EventHeader()
                            }
                        }
                    }
                }
            }
        }
    }
}

private struct PlayerDashboardView: View {
    @State private var chartKind: DashboardChartKind = .elo

    let candidate: PlayerCandidate
    let stats: PlayerDashboardStats

    private let columns = [
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10),
        GridItem(.flexible(), spacing: 10)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("重点数据")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Color.appText)
                    Text(candidate.detailLine)
                        .font(.callout)
                        .foregroundStyle(Color.appTextSecondary)
                        .lineLimit(2)
                }

                Spacer()

                if let url = candidate.profileURL {
                    Link(destination: url) {
                        Label("FIDE", systemImage: "arrow.up.right.square")
                    }
                    .buttonStyle(.bordered)
                }
            }

            YouthProgressRail(stages: stats.youthStages)

            LazyVGrid(columns: columns, spacing: 10) {
                DashboardMetricCard(title: "赛事索引", value: "\(stats.eventCount)", subtitle: "近十年", symbol: "calendar")
                DashboardMetricCard(title: "前三名", value: "\(stats.topThreeCount)", subtitle: "冠军 \(stats.firstPlaceCount)", symbol: "trophy")
                DashboardMetricCard(title: "PGN 缓存", value: "\(stats.cachedPGNArchives)", subtitle: "\(stats.cachedGames) 盘", symbol: "archivebox")
                DashboardMetricCard(title: "青少年阶段", value: stats.currentStage?.rawValue ?? "-", subtitle: stats.birthYear.map { "\($0) 出生" } ?? "待补出生年", symbol: "flag.checkered")
            }

            YouthStageTimelineView(stages: stats.youthStages)
            YouthChartSwitcher(kind: $chartKind, stats: stats)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.panelBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private enum DashboardChartKind: String, CaseIterable, Identifiable {
    case elo = "FIDE ELO"
    case rank = "大赛名次"

    var id: String { rawValue }
}

private struct YouthStageTimelineView: View {
    let stages: [YouthStageSummary]

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 6)

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("青少年阶段")
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.appTextSecondary)

            LazyVGrid(columns: columns, spacing: 8) {
                ForEach(stages) { stage in
                    YouthStageCard(summary: stage)
                }
            }
        }
    }
}

private struct YouthProgressRail: View {
    let stages: [YouthStageSummary]

    @State private var reveal = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("阶段进度")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
                Spacer()
                Text(stageCaption)
                    .font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Color.appAccentStrong)
            }

            GeometryReader { proxy in
                let inset: CGFloat = 14
                let width = max(proxy.size.width - inset * 2, 1)
                let step = width / CGFloat(max(stages.count - 1, 1))
                let activeIndex = activeStageIndex

                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(Color.panelBorder.opacity(0.7))
                        .frame(height: 4)
                        .position(x: inset + width / 2, y: 16)

                    Capsule()
                        .fill(Color.appAccentStrong)
                        .frame(width: reveal ? step * CGFloat(activeIndex) : 0, height: 4)
                        .position(x: inset + (reveal ? step * CGFloat(activeIndex) : 0) / 2, y: 16)

                    ForEach(Array(stages.enumerated()), id: \.element.id) { index, stage in
                        VStack(spacing: 6) {
                            ZStack {
                                Circle()
                                    .fill(circleFill(for: stage))
                                    .frame(width: stage.status == .current ? 20 : 16, height: stage.status == .current ? 20 : 16)
                                    .shadow(
                                        color: stage.status == .current ? Color.appAccentStrong.opacity(0.25) : .clear,
                                        radius: 7,
                                        y: 2
                                    )
                                Circle()
                                    .stroke(circleStroke(for: stage), lineWidth: 2)
                                    .frame(width: 22, height: 22)
                            }

                            Text(stage.stage.rawValue)
                                .font(.caption2.monospacedDigit().weight(stage.status == .current ? .semibold : .regular))
                                .foregroundStyle(stage.status == .current ? Color.appText : Color.appTextSecondary)
                        }
                        .position(x: inset + step * CGFloat(index), y: 25)
                    }
                }
            }
            .frame(height: 58)
        }
        .padding(14)
        .background(Color.chartSurface)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .onAppear {
            withAnimation(.easeOut(duration: 0.65)) {
                reveal = true
            }
        }
        .onChange(of: stages) { _, _ in
            reveal = false
            withAnimation(.easeOut(duration: 0.65)) {
                reveal = true
            }
        }
    }

    private var activeStageIndex: Int {
        stages.lastIndex { $0.status == .completed || $0.status == .current } ?? 0
    }

    private var stageCaption: String {
        stages.first { $0.status == .current }?.stage.rawValue ?? "待补"
    }

    private func circleFill(for stage: YouthStageSummary) -> Color {
        switch stage.status {
        case .completed, .current:
            Color.appAccentStrong
        case .upcoming:
            Color.panelBackground
        case .unknown:
            Color.statBackground
        }
    }

    private func circleStroke(for stage: YouthStageSummary) -> Color {
        switch stage.status {
        case .completed, .current:
            Color.appAccentStrong
        case .upcoming:
            Color.stageFutureBorder
        case .unknown:
            Color.panelBorder
        }
    }
}

private struct YouthStageCard: View {
    let summary: YouthStageSummary

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text(summary.stage.rawValue)
                    .font(.headline.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
            }

            Text(summary.status.label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(statusColor)
                .lineLimit(1)

            Text(summary.status == .upcoming && summary.eventCount == 0 ? "未完待续" : "\(summary.eventCount) 场")
                .font(.caption)
                .foregroundStyle(Color.appTextSecondary)
                .lineLimit(1)

            HStack(spacing: 4) {
                Text(summary.rankText)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appText)
                Spacer(minLength: 2)
                Text(summary.ratingText)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, minHeight: 112, alignment: .leading)
        .background(cardBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(summary.status == .current ? Color.appAccentStrong : Color.panelBorder, lineWidth: summary.status == .current ? 1.5 : 1)
        )
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 3, style: .continuous)
                .fill(statusColor)
                .frame(width: 3)
                .padding(.vertical, 10)
                .opacity(summary.status == .unknown ? 0.25 : 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .shadow(color: summary.status == .current ? Color.appAccentStrong.opacity(0.12) : .clear, radius: 9, y: 3)
    }

    private var cardBackground: Color {
        switch summary.status {
        case .current:
            Color.stageCurrentBackground
        case .upcoming:
            Color.stageFutureBackground
        case .completed, .unknown:
            Color.statBackground
        }
    }

    private var statusColor: Color {
        switch summary.status {
        case .completed:
            Color.appTextSecondary
        case .current:
            Color.appAccentStrong
        case .upcoming:
            Color.stageFutureAccent
        case .unknown:
            Color.appTextSecondary.opacity(0.7)
        }
    }
}

private struct YouthChartSwitcher: View {
    @Binding var kind: DashboardChartKind
    let stats: PlayerDashboardStats

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("成长曲线")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)

                Spacer()

                ChartModeControl(selection: $kind)
            }

            StageLineChart(
                points: points,
                invertY: kind == .rank,
                emptyText: emptyText
            )
            .frame(height: 230)
        }
        .padding(14)
        .background(Color.statBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var points: [YouthChartPoint] {
        switch kind {
        case .elo:
            stats.eloChartPoints
        case .rank:
            stats.rankChartPoints
        }
    }

    private var emptyText: String {
        switch kind {
        case .elo:
            "暂无 FIDE 分阶段数据"
        case .rank:
            "暂无大赛名次数据"
        }
    }
}

private struct ChartModeControl: View {
    @Binding var selection: DashboardChartKind

    var body: some View {
        HStack(spacing: 4) {
            ForEach(DashboardChartKind.allCases) { chartKind in
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        selection = chartKind
                    }
                } label: {
                    Text(chartKind.rawValue)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(selection == chartKind ? Color.appTextOnAccent : Color.appText)
                        .lineLimit(1)
                        .minimumScaleFactor(0.82)
                        .padding(.horizontal, 10)
                        .padding(.vertical, 7)
                        .frame(minWidth: 92)
                        .background(selection == chartKind ? Color.appAccentStrong : Color.clear)
                        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(3)
        .background(Color.inputBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct StageLineChart: View {
    @State private var reveal = false

    let points: [YouthChartPoint]
    let invertY: Bool
    let emptyText: String

    private let stages = YouthStage.allCases

    var body: some View {
        GeometryReader { proxy in
            let chartRect = CGRect(
                x: 42,
                y: 18,
                width: max(proxy.size.width - 64, 1),
                height: max(proxy.size.height - 58, 1)
            )

            ZStack(alignment: .topLeading) {
                chartGrid(in: chartRect)

                if points.isEmpty {
                    Text(emptyText)
                        .font(.callout)
                        .foregroundStyle(Color.appTextSecondary)
                        .frame(width: proxy.size.width, height: proxy.size.height, alignment: .center)
                } else {
                    Path { path in
                        for (index, point) in points.enumerated() {
                            let position = position(for: point, in: chartRect)
                            if index == 0 {
                                path.move(to: position)
                            } else {
                                path.addLine(to: position)
                            }
                        }
                    }
                    .trim(from: 0, to: reveal ? 1 : 0)
                    .stroke(Color.appAccentStrong, style: StrokeStyle(lineWidth: 2.8, lineCap: .round, lineJoin: .round))

                    ForEach(points) { point in
                        let position = position(for: point, in: chartRect)
                        VStack(spacing: 4) {
                            Text(point.label)
                                .font(.caption2.monospacedDigit().weight(.semibold))
                                .foregroundStyle(Color.appText)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 2)
                                .background(Color.panelBackground)
                                .clipShape(RoundedRectangle(cornerRadius: 5, style: .continuous))
                            Circle()
                                .fill(Color.appAccentStrong)
                                .frame(width: 9, height: 9)
                        }
                        .position(x: position.x, y: position.y - 13)
                        .opacity(reveal ? 1 : 0)
                        .scaleEffect(reveal ? 1 : 0.82)
                        .help(point.subtitle)
                    }
                }

                ForEach(Array(stages.enumerated()), id: \.element) { index, stage in
                    Text(stage.rawValue)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(Color.appTextSecondary)
                        .frame(width: 36)
                        .position(
                            x: xPosition(forStageIndex: index, in: chartRect),
                            y: chartRect.maxY + 18
                        )
                }
            }
        }
        .onAppear {
            animateReveal()
        }
        .onChange(of: points) { _, _ in
            animateReveal()
        }
    }

    private func animateReveal() {
        reveal = false
        withAnimation(.easeOut(duration: 0.55)) {
            reveal = true
        }
    }

    private func chartGrid(in rect: CGRect) -> some View {
        ZStack(alignment: .topLeading) {
            Path { path in
                for step in 0...3 {
                    let y = rect.minY + rect.height * CGFloat(step) / 3
                    path.move(to: CGPoint(x: rect.minX, y: y))
                    path.addLine(to: CGPoint(x: rect.maxX, y: y))
                }
            }
            .stroke(Color.panelBorder.opacity(0.7), lineWidth: 1)

            Path { path in
                path.move(to: CGPoint(x: rect.minX, y: rect.minY))
                path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
                path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
            }
            .stroke(Color.panelBorder, lineWidth: 1)
        }
    }

    private func position(for point: YouthChartPoint, in rect: CGRect) -> CGPoint {
        let stageIndex = stages.firstIndex(of: point.stage) ?? 0
        let values = points.map(\.value)
        let minValue = values.min() ?? 0
        let maxValue = values.max() ?? 1
        let range = max(maxValue - minValue, 1)
        let normalized = (point.value - minValue) / range
        let yFactor = maxValue == minValue ? 0.5 : (invertY ? normalized : 1 - normalized)
        return CGPoint(
            x: xPosition(forStageIndex: stageIndex, in: rect),
            y: rect.minY + rect.height * CGFloat(yFactor)
        )
    }

    private func xPosition(forStageIndex index: Int, in rect: CGRect) -> CGFloat {
        guard stages.count > 1 else { return rect.midX }
        return rect.minX + rect.width * CGFloat(index) / CGFloat(stages.count - 1)
    }
}

private struct DashboardMetricCard: View {
    let title: String
    let value: String
    let subtitle: String
    let symbol: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .foregroundStyle(Color.appAccent)
                    .frame(width: 18)
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(1)
            }

            Text(value)
                .font(.title2.monospacedDigit().weight(.semibold))
                .foregroundStyle(Color.appText)
                .lineLimit(1)
                .minimumScaleFactor(0.76)

            Text(subtitle)
                .font(.caption)
                .foregroundStyle(Color.appTextSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.82)
        }
        .padding(12)
        .frame(maxWidth: .infinity, minHeight: 112, alignment: .leading)
        .background(Color.statBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct MiniMetric: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(Color.appTextSecondary)
            Text(value)
                .font(.callout.monospacedDigit().weight(.semibold))
                .foregroundStyle(Color.appText)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct TagPill: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .foregroundStyle(Color.appAccent)
            .lineLimit(1)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(Color.selectionBackground)
            .clipShape(Capsule())
    }
}

private struct EventToolbar: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(store.selectedCandidate?.displayName ?? "赛事")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Text(toolbarDetail)
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
            }

            Spacer()

            Button {
                store.selectAllEvents()
            } label: {
                Label("全选", systemImage: "checkmark.square")
            }
            .disabled(store.selectedCandidate == nil)

            Button {
                store.clearEventSelection()
            } label: {
                Image(systemName: "square")
            }
            .help("清空选择")
            .disabled(store.selectedCandidate == nil)

            Button {
                store.downloadAndSavePGN()
            } label: {
                Label("合并并保存 PGN", systemImage: "square.and.arrow.down")
            }
            .buttonStyle(.borderedProminent)
            .disabled(store.selectedEventIDs.isEmpty || store.isDownloading)
        }
        .padding(.horizontal, 18)
        .padding(.vertical, 14)
    }

    private var toolbarDetail: String {
        guard let candidate = store.selectedCandidate else { return "近十年赛事将显示在这里" }
        let fide = candidate.fideID.map { "FIDE \($0)" } ?? "无 FIDE ID"
        return "\(fide) · 已选 \(store.selectedCountText)"
    }
}

private struct EventHeader: View {
    var body: some View {
        HStack(spacing: 12) {
            Text("")
                .frame(width: 28)
            Text("日期")
                .frame(width: 92, alignment: .leading)
            Text("赛事")
                .frame(maxWidth: .infinity, alignment: .leading)
            Text("名次")
                .frame(width: 52, alignment: .trailing)
            Text("规模")
                .frame(width: 92, alignment: .trailing)
            Text("来源")
                .frame(width: 104, alignment: .leading)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(Color.appTextSecondary)
        .padding(.horizontal, 18)
        .padding(.vertical, 8)
        .background(Color.headerBackground)
    }
}

private struct EventRow: View {
    let event: TournamentEvent
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: isSelected ? "checkmark.square.fill" : "square")
                    .font(.title3)
                    .foregroundStyle(isSelected ? Color.appAccent : Color.appTextSecondary)
                    .frame(width: 28)

                Text(event.dateText)
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(Color.appText)
                    .frame(width: 92, alignment: .leading)

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(event.name)
                            .font(.callout.weight(.medium))
                            .foregroundStyle(Color.appText)
                            .lineLimit(1)
                        if event.isLikelyTestData {
                            Image(systemName: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                                .help("疑似测试赛事")
                        }
                    }

                    Text("\(event.playerName) · \(event.federation.isEmpty ? "未知协会" : event.federation)")
                        .font(.caption)
                        .foregroundStyle(Color.appTextSecondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Text(event.rank.isEmpty ? "-" : event.rank)
                    .font(.callout.monospacedDigit())
                    .foregroundStyle(Color.appText)
                    .frame(width: 52, alignment: .trailing)

                Text(event.compactMeta)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appTextSecondary)
                    .frame(width: 92, alignment: .trailing)

                Link(destination: event.eventURL) {
                    Label(event.source, systemImage: "arrow.up.right.square")
                        .labelStyle(.iconOnly)
                        .frame(width: 104, alignment: .leading)
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.appTextSecondary)
            }
            .padding(.horizontal, 18)
            .padding(.vertical, 10)
            .background(isSelected ? Color.appAccent.opacity(0.08) : Color.clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)

        Divider()
            .padding(.leading, 58)
    }
}

private struct DownloadPanel: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("PGN")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Text(summaryText)
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
            }

            DatabaseStatsView()

            if store.isDownloading {
                ProgressView(value: store.downloadProgress)
                    .frame(maxWidth: .infinity)
            }

            HStack(spacing: 12) {
                StatPill(title: "成功", value: "\(store.successfulDownloadCount)")
                StatPill(title: "棋局", value: "\(store.totalGameCount)")
            }

            Divider()

            if store.downloadResults.isEmpty {
                Spacer()
                VStack(spacing: 10) {
                    Image(systemName: "doc.text.magnifyingglass")
                        .font(.system(size: 34))
                        .foregroundStyle(Color.appTextSecondary)
                    Text("尚未下载")
                        .font(.headline)
                        .foregroundStyle(Color.appText)
                    Text("选中赛事后可合并保存")
                        .font(.callout)
                        .foregroundStyle(Color.appTextSecondary)
                }
                .frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(store.downloadResults) { result in
                            DownloadResultRow(result: result)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }

            if let url = store.savedFileURL {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                } label: {
                    Label("显示文件", systemImage: "folder")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(18)
        .background(Color.downloadPanelBackground)
    }

    private var summaryText: String {
        if store.isDownloading {
            return "下载 \(Int(store.downloadProgress * 100))%"
        }
        if store.downloadResults.isEmpty {
            return "等待选择赛事"
        }
        return "\(store.downloadResults.count) 个赛事结果"
    }
}

private struct DownloadResultRow: View {
    let result: PGNDownloadResult

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: result.status.symbolName)
                    .foregroundStyle(color)
                Text(result.status.label)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                if result.gameCount > 0 {
                    Text("\(result.gameCount) 盘")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Color.appTextSecondary)
                }
            }

            Text(result.event.name)
                .font(.callout.weight(.medium))
                .foregroundStyle(Color.appText)
                .lineLimit(2)

            if case let .failed(message) = result.status {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(2)
            } else if result.byteCount > 0 {
                Text("\(result.byteCount) bytes")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appTextSecondary)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.panelBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var color: Color {
        switch result.status {
        case .cached:
            .blue
        case .success:
            .green
        case .empty:
            .secondary
        case .failed:
            .red
        }
    }
}

private struct DatabaseStatsView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("本地数据库")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
                Spacer()
                Button {
                    store.refreshDatabaseStats()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.plain)
                .help("刷新统计")
            }

            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 8) {
                StatPill(title: "棋手", value: "\(store.databaseStats.players)")
                StatPill(title: "别名", value: "\(store.databaseStats.aliases)")
                StatPill(title: "赛事", value: "\(store.databaseStats.events)")
                StatPill(title: "棋局", value: "\(store.databaseStats.games)")
            }

            HStack {
                Label("\(store.databaseStats.pgnArchives) 个 PGN", systemImage: "archivebox")
                Spacer()
                Text(store.databaseStats.pgnSizeText)
                    .font(.caption.monospacedDigit())
            }
            .font(.caption)
            .foregroundStyle(Color.appTextSecondary)

            Button {
                store.revealDatabaseFolder()
            } label: {
                Label("打开数据库目录", systemImage: "folder")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
        }
        .padding(12)
        .background(Color.panelBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .stroke(Color.panelBorder)
        )
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct StatPill: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption)
                .foregroundStyle(Color.appTextSecondary)
            Text(value)
                .font(.title3.monospacedDigit().weight(.semibold))
                .foregroundStyle(Color.appText)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.statBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

extension Color {
    static let appAccent = Color(red: 0.05, green: 0.45, blue: 0.42)
    static let appAccentStrong = Color(red: 0.03, green: 0.36, blue: 0.34)
    static let appTextOnAccent = Color.white
    static let appText = Color(red: 0.08, green: 0.09, blue: 0.10)
    static let appTextSecondary = Color(red: 0.34, green: 0.38, blue: 0.42)
    static let appPlaceholder = Color(red: 0.48, green: 0.52, blue: 0.55)
    static let appCanvas = Color(red: 0.96, green: 0.96, blue: 0.95)
    static let sidebarBackground = Color(red: 0.985, green: 0.985, blue: 0.975)
    static let panelBackground = Color.white
    static let panelBorder = Color(red: 0.82, green: 0.83, blue: 0.82)
    static let selectionBackground = Color(red: 0.88, green: 0.95, blue: 0.94)
    static let headerBackground = Color(red: 0.91, green: 0.92, blue: 0.91)
    static let statBackground = Color(red: 0.95, green: 0.96, blue: 0.95)
    static let inputBackground = Color(red: 1.0, green: 1.0, blue: 0.99)
    static let chartSurface = Color(red: 0.94, green: 0.965, blue: 0.958)
    static let stageCurrentBackground = Color(red: 0.90, green: 0.965, blue: 0.955)
    static let stageFutureBackground = Color(red: 0.975, green: 0.965, blue: 0.93)
    static let stageFutureBorder = Color(red: 0.72, green: 0.63, blue: 0.38)
    static let stageFutureAccent = Color(red: 0.58, green: 0.47, blue: 0.20)
    static let sidebarInset = Color(red: 0.95, green: 0.96, blue: 0.95)
    static let downloadPanelBackground = Color(red: 0.975, green: 0.975, blue: 0.965)
}
