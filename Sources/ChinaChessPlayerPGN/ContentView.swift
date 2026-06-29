import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        NavigationSplitView {
            SearchSidebar()
                .navigationSplitViewColumnWidth(min: 280, ideal: 320, max: 380)
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
            VStack(alignment: .leading, spacing: 4) {
                Text("中国棋手 PGN")
                    .font(.title2.weight(.semibold))
                Text(store.statusText)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .frame(minHeight: 34, alignment: .topLeading)
            }
            .padding(.top, 8)

            VStack(alignment: .leading, spacing: 10) {
                Text("棋手拼音")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)

                HStack(spacing: 8) {
                    TextField("wang hao", text: $store.query)
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

                Toggle("包含测试赛事", isOn: $store.includeLikelyTestEvents)
                    .font(.callout)
            }

            Divider()

            HStack {
                Label("候选棋手", systemImage: "person.2")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Text("\(store.candidates.count)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
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
                    .foregroundStyle(.secondary)
                Label("Chess-Results 球员/对局数据库", systemImage: "globe.asia.australia")
                    .font(.callout)
                Label("FIDE 资料链接用于区分同名", systemImage: "link")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.sidebarInset)
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
                        .lineLimit(1)
                    Spacer()
                    Text("\(candidate.eventCount)")
                        .font(.caption.monospacedDigit().weight(.semibold))
                        .foregroundStyle(isSelected ? Color.appAccent : .secondary)
                }

                Text(candidate.detailLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)

                if !candidate.clubs.isEmpty {
                    Text(candidate.clubs.joined(separator: " / "))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(isSelected ? Color.appAccent.opacity(0.14) : Color.clear)
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
                .foregroundStyle(.secondary)
            Text("暂无候选棋手")
                .font(.headline)
            Text("搜索后会按 FIDE ID 聚合重名棋手")
                .font(.callout)
                .foregroundStyle(.secondary)
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
                Text(toolbarDetail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
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
        .foregroundStyle(.secondary)
        .padding(.horizontal, 18)
        .padding(.vertical, 8)
        .background(.bar)
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
                    .foregroundStyle(isSelected ? Color.appAccent : .secondary)
                    .frame(width: 28)

                Text(event.dateText)
                    .font(.callout.monospacedDigit())
                    .frame(width: 92, alignment: .leading)

                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(event.name)
                            .font(.callout.weight(.medium))
                            .lineLimit(1)
                        if event.isLikelyTestData {
                            Image(systemName: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                                .help("疑似测试赛事")
                        }
                    }

                    Text("\(event.playerName) · \(event.federation.isEmpty ? "未知协会" : event.federation)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)

                Text(event.rank.isEmpty ? "-" : event.rank)
                    .font(.callout.monospacedDigit())
                    .frame(width: 52, alignment: .trailing)

                Text(event.compactMeta)
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(width: 92, alignment: .trailing)

                Link(destination: event.eventURL) {
                    Label(event.source, systemImage: "arrow.up.right.square")
                        .labelStyle(.iconOnly)
                        .frame(width: 104, alignment: .leading)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
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
                Text(summaryText)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }

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
                        .foregroundStyle(.secondary)
                    Text("尚未下载")
                        .font(.headline)
                    Text("选中赛事后可合并保存")
                        .font(.callout)
                        .foregroundStyle(.secondary)
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
                Spacer()
                if result.gameCount > 0 {
                    Text("\(result.gameCount) 盘")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }

            Text(result.event.name)
                .font(.callout.weight(.medium))
                .lineLimit(2)

            if case let .failed(message) = result.status {
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            } else if result.byteCount > 0 {
                Text("\(result.byteCount) bytes")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sidebarInset)
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

private struct StatPill: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(value)
                .font(.title3.monospacedDigit().weight(.semibold))
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.sidebarInset)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

extension Color {
    static let appAccent = Color(red: 0.10, green: 0.35, blue: 0.45)
    static let appCanvas = Color(red: 0.97, green: 0.98, blue: 0.98)
    static let sidebarBackground = Color(red: 0.93, green: 0.95, blue: 0.95)
    static let sidebarInset = Color.black.opacity(0.045)
    static let downloadPanelBackground = Color(red: 0.95, green: 0.96, blue: 0.96)
}
