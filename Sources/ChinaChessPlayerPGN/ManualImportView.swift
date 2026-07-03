import SwiftUI

struct ManualImportView: View {
    @EnvironmentObject private var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                eventImportPanel
                resultPanel
                mappingPanel
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .onAppear {
            store.loadNameMappings()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("手工赛事入库")
                .font(.largeTitle.weight(.semibold))
                .foregroundStyle(Color.appText)
            Text("提交 Chess-Results 赛事链接或公开 PGN 链接，程序会下载、去重、按本地棋手/映射表识别，写入本地归档并同步到 GitHub 数据仓库。")
                .font(.callout)
                .foregroundStyle(Color.appTextSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var eventImportPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("赛事链接", systemImage: "link")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                if store.isManualImporting {
                    ProgressView()
                        .controlSize(.small)
                }
            }

            TextField(
                "https://chess-results.com/tnr935824.aspx?lan=1",
                text: $store.manualEventURL
            )
            .textFieldStyle(.roundedBorder)
            .font(.callout)

            Toggle("中国站赛事允许自动创建未映射棋手", isOn: $store.manualImportAllowsChinaEventFallback)
                .font(.callout)
                .foregroundStyle(Color.appText)

            HStack(spacing: 10) {
                Button {
                    store.importManualEventLink()
                } label: {
                    Label("分析、入库并同步 GitHub", systemImage: "tray.and.arrow.down")
                }
                .buttonStyle(.borderedProminent)
                .disabled(store.isManualImporting)

                Button {
                    store.openNameMappingFile()
                } label: {
                    Label("打开映射表", systemImage: "tablecells")
                }
                .buttonStyle(.bordered)

                Button {
                    store.importDefaultNameMappingFile()
                } label: {
                    Label("导入映射表", systemImage: "arrow.down.doc")
                }
                .buttonStyle(.bordered)
            }
        }
        .manualPanel()
    }

    @ViewBuilder
    private var resultPanel: some View {
        let report = store.manualImportResult
        if report.totalGames > 0 || !report.warnings.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                Label("入库结果", systemImage: "checklist")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(Color.appText)

                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 4), spacing: 10) {
                    ManualMetric(title: "原始棋局", value: "\(report.totalGames)")
                    ManualMetric(title: "去重后", value: "\(report.uniqueGames)")
                    ManualMetric(title: "入库棋手", value: "\(report.importedPlayers)")
                    ManualMetric(title: "入库棋局", value: "\(report.importedGames)")
                }

                if !report.eventName.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(report.eventName)
                            .font(.headline)
                            .foregroundStyle(Color.appText)
                        Text([report.sourceURL, report.finalURL].filter { !$0.isEmpty }.joined(separator: " → "))
                            .font(.caption)
                            .foregroundStyle(Color.appTextSecondary)
                            .lineLimit(2)
                    }
                }

                if !report.playerSummaries.isEmpty {
                    VStack(spacing: 8) {
                        ForEach(report.playerSummaries) { player in
                            HStack {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(player.displayName)
                                        .font(.callout.weight(.semibold))
                                        .foregroundStyle(Color.appText)
                                    Text(player.fideID.map { "FIDE \($0)" } ?? player.id)
                                        .font(.caption)
                                        .foregroundStyle(Color.appTextSecondary)
                                }
                                Spacer()
                                Text("\(player.gameCount) 盘")
                                    .font(.callout.monospacedDigit().weight(.semibold))
                                    .foregroundStyle(Color.appAccentStrong)
                            }
                            .padding(10)
                            .background(Color.statBackground)
                            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                        }
                    }
                }

                if !report.unresolvedNames.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("未识别棋手")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Color.appTextSecondary)
                        Text(report.unresolvedNames.prefix(30).joined(separator: " · "))
                            .font(.caption)
                            .foregroundStyle(Color.appTextSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(10)
                    .background(Color.stageFutureBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                }

                if store.githubPublishResult.hasStatus {
                    githubPublishPanel(store.githubPublishResult)
                }

                ForEach(report.warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(Color.stageFutureAccent)
                }
            }
            .manualPanel()
        }
    }

    private func githubPublishPanel(_ result: GitHubPublishResult) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("GitHub 数据仓库", systemImage: result.pushed ? "checkmark.icloud" : "icloud")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                Text(result.pushed ? "已推送" : (result.committed ? "已提交" : "未提交"))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(result.pushed ? Color.appAccentStrong : Color.appTextSecondary)
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 8) {
                ManualMetric(title: "复制文件", value: "\(result.copied)")
                ManualMetric(title: "静态 PGN", value: "\(result.pgnFiles)")
                ManualMetric(title: "静态棋局", value: "\(result.games)")
                ManualMetric(title: "跳过", value: "\(result.skipped)")
            }

            if !result.message.isEmpty {
                Text(result.message)
                    .font(.caption)
                    .foregroundStyle(Color.appTextSecondary)
            }

            if !result.repoPath.isEmpty {
                Text(result.repoPath)
                    .font(.caption.monospaced())
                    .foregroundStyle(Color.appTextSecondary)
                    .lineLimit(2)
            }

            if let commitHash = result.commitHash {
                Text("commit \(commitHash)")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appAccent)
            }

            ForEach(result.warnings, id: \.self) { warning in
                Label(warning, systemImage: "exclamationmark.triangle")
                    .font(.caption)
                    .foregroundStyle(Color.stageFutureAccent)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(10)
        .background(Color.statBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    private var mappingPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("用户名称映射表")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Color.appText)
                    Text("用于处理无 FIDE ID、低龄组、中文名/拼音/英文名不一致的棋手。")
                        .font(.caption)
                        .foregroundStyle(Color.appTextSecondary)
                }
                Spacer()
                Button {
                    store.exportAllNameMappings()
                } label: {
                    Label("导出全部映射 CSV", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.bordered)
                Button {
                    store.chooseAndImportNameMappingFile()
                } label: {
                    Label("选择 CSV", systemImage: "doc.badge.plus")
                }
                .buttonStyle(.bordered)
                Button {
                    store.revealNameMappingFile()
                } label: {
                    Label("定位", systemImage: "folder")
                }
                .buttonStyle(.bordered)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("字段：alias, fide_id, display_name, chinese_name, pinyin_name, english_name, federation, birth_year, standard_rating, rapid_rating, blitz_rating, note")
                    .font(.caption)
                    .foregroundStyle(Color.appTextSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("最少填写 alias；有 FIDE ID 时填写 fide_id，系统会绑定到唯一棋手。")
                    .font(.caption)
                    .foregroundStyle(Color.appTextSecondary)
            }
            .padding(10)
            .background(Color.statBackground)
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))

            mappingEditor

            HStack {
                Text("已有映射")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.appTextSecondary)
                Spacer()
                Text("\(store.nameMappingRows.count) 条")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appTextSecondary)
            }

            if store.nameMappingRows.isEmpty {
                Text("暂无用户映射。可以在上方直接填写并保存，也可以打开 CSV 批量编辑后导入。")
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
                    .frame(maxWidth: .infinity, minHeight: 80, alignment: .center)
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(store.nameMappingRows) { row in
                            mappingRow(row, showSource: false)
                        }
                    }
                }
                .frame(maxHeight: 280)
            }

            Divider()

            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text("全部来源映射")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Color.appTextSecondary)
                    Text("包含 seed / FIDE / PGN / user-mapping。点击任意行可复制到上方编辑区，保存后作为 user-mapping 补充。")
                        .font(.caption2)
                        .foregroundStyle(Color.appTextSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button {
                    store.exportAllNameMappings()
                } label: {
                    Label("导出全部 CSV", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.bordered)
            }

            if !store.aliasSourceStats.isEmpty {
                Text(store.aliasSourceStats.map { "\($0.source) \($0.count)" }.joined(separator: " · "))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(Color.appTextSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if store.allNameMappingRows.isEmpty {
                Text("暂无全部来源映射。")
                    .font(.callout)
                    .foregroundStyle(Color.appTextSecondary)
                    .frame(maxWidth: .infinity, minHeight: 64, alignment: .center)
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(store.allNameMappingRows) { row in
                            mappingRow(row, showSource: true)
                        }
                    }
                }
                .frame(maxHeight: 320)
            }
        }
        .manualPanel()
    }

    private func mappingRow(_ row: UserNameMappingRow, showSource: Bool) -> some View {
        Button {
            store.selectNameMapping(row)
        } label: {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        Text(row.alias)
                            .font(.callout.weight(.semibold))
                            .foregroundStyle(Color.appText)
                            .lineLimit(1)
                        if showSource {
                            Text(row.source)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(Color.appAccent)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.selectionBackground)
                                .clipShape(Capsule())
                        }
                    }
                    Text([row.displayName, row.chineseName, row.pinyinName, row.englishName]
                        .filter { !$0.isEmpty }
                        .joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(Color.appTextSecondary)
                        .lineLimit(1)
                }
                Spacer()
                VStack(alignment: .trailing, spacing: 3) {
                    Text(row.fideID.map { "FIDE \($0)" } ?? row.playerID)
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(Color.appAccent)
                    Text([row.federation, row.birthYear].filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.caption2)
                        .foregroundStyle(Color.appTextSecondary)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .background(row.id == store.selectedNameMappingID ? Color.selectionBackground : Color.statBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(row.id == store.selectedNameMappingID ? Color.appAccent : Color.clear)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private var mappingEditor: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("页面内编辑", systemImage: "square.and.pencil")
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(Color.appText)
                Spacer()
                Button {
                    store.clearNameMappingDraft()
                } label: {
                    Label("新建", systemImage: "plus")
                }
                .buttonStyle(.bordered)
                Button {
                    store.saveNameMappingDraft()
                } label: {
                    Label("保存映射", systemImage: "checkmark")
                }
                .buttonStyle(.borderedProminent)
            }

            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 3), spacing: 10) {
                MappingTextField(title: "alias", placeholder: "PGN 中的名字", text: $store.nameMappingDraft.alias)
                MappingTextField(title: "fide_id", placeholder: "可空", text: $store.nameMappingDraft.fideID)
                MappingTextField(title: "display_name", placeholder: "看板显示名", text: $store.nameMappingDraft.displayName)
                MappingTextField(title: "chinese_name", placeholder: "中文名", text: $store.nameMappingDraft.chineseName)
                MappingTextField(title: "pinyin_name", placeholder: "拼音", text: $store.nameMappingDraft.pinyinName)
                MappingTextField(title: "english_name", placeholder: "FIDE/英文名", text: $store.nameMappingDraft.englishName)
                MappingTextField(title: "federation", placeholder: "CHN", text: $store.nameMappingDraft.federation)
                MappingTextField(title: "birth_year", placeholder: "2016", text: $store.nameMappingDraft.birthYear)
                MappingTextField(title: "standard_rating", placeholder: "可空", text: $store.nameMappingDraft.standardRating)
                MappingTextField(title: "rapid_rating", placeholder: "可空", text: $store.nameMappingDraft.rapidRating)
                MappingTextField(title: "blitz_rating", placeholder: "可空", text: $store.nameMappingDraft.blitzRating)
                MappingTextField(title: "note", placeholder: "来源或备注", text: $store.nameMappingDraft.note)
            }
        }
        .padding(12)
        .background(Color.statBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct ManualMetric: View {
    let title: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Color.appTextSecondary)
            Text(value)
                .font(.title3.monospacedDigit().weight(.bold))
                .foregroundStyle(Color.appText)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.statBackground)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}

private struct MappingTextField: View {
    let title: String
    let placeholder: String
    @Binding var text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(Color.appTextSecondary)
            TextField(placeholder, text: $text)
                .textFieldStyle(.roundedBorder)
                .font(.caption)
        }
    }
}

private extension View {
    func manualPanel() -> some View {
        padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.panelBackground)
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(Color.panelBorder)
            )
            .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
    }
}
