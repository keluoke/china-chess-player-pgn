import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        NavigationSplitView {
            SearchSidebar()
                .navigationSplitViewColumnWidth(min: 360, ideal: 380, max: 440)
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

            VStack(alignment: .leading, spacing: 10) {
                Text("棋手搜索")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)

                HStack(spacing: 8) {
                    TextField("王皓 / wang hao / 8602883", text: $store.query)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { store.search() }

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
        VStack(spacing: 0) {
            EventToolbar()
            Divider()

            if let candidate = store.selectedCandidate {
                if candidate.events.isEmpty {
                    ContentUnavailableView("没有近十年赛事", systemImage: "calendar.badge.exclamationmark")
                } else {
                    ScrollView {
                        LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
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
            } else {
                ContentUnavailableView("选择棋手", systemImage: "person.text.rectangle")
            }
        }
        .background(Color.appCanvas)
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
    static let appText = Color(red: 0.08, green: 0.09, blue: 0.10)
    static let appTextSecondary = Color(red: 0.34, green: 0.38, blue: 0.42)
    static let appCanvas = Color(red: 0.96, green: 0.96, blue: 0.95)
    static let sidebarBackground = Color(red: 0.985, green: 0.985, blue: 0.975)
    static let panelBackground = Color.white
    static let panelBorder = Color(red: 0.82, green: 0.83, blue: 0.82)
    static let selectionBackground = Color(red: 0.88, green: 0.95, blue: 0.94)
    static let headerBackground = Color(red: 0.91, green: 0.92, blue: 0.91)
    static let statBackground = Color(red: 0.95, green: 0.96, blue: 0.95)
    static let sidebarInset = Color(red: 0.95, green: 0.96, blue: 0.95)
    static let downloadPanelBackground = Color(red: 0.975, green: 0.975, blue: 0.965)
}
