import Foundation

struct GitHubDataPublisher {
    func publishManualImport() async throws -> GitHubPublishResult {
        try await publishLocalPGNCache()
    }

    func publishLocalPGNCache() async throws -> GitHubPublishResult {
        try await Task.detached(priority: .utility) {
            let repoURL = try findRepositoryRoot()
            let sync = try run(["python3", "Scripts/sync_static_pgn.py", "--from-local-cache"], in: repoURL)
            let stats = parseSyncStats(sync.stdout)
            let status = try run(["git", "status", "--porcelain", "--", "docs/data"], in: repoURL)
            let changedPaths = status.stdout
                .split(separator: "\n")
                .map(String.init)
                .filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

            guard !changedPaths.isEmpty else {
                return GitHubPublishResult(
                    repoPath: repoURL.path,
                    copied: stats.copied,
                    downloaded: stats.downloaded,
                    skipped: stats.skipped,
                    pgnFiles: stats.pgnFiles,
                    games: stats.games,
                    committed: false,
                    pushed: false,
                    commitHash: nil,
                    message: "静态数据已检查，docs/data 没有新增变更。",
                    warnings: stats.warnings
                )
            }

            try run(["git", "add", "docs/data"], in: repoURL)
            let staged = try run(["git", "diff", "--cached", "--name-only", "--", "docs/data"], in: repoURL)
            guard !staged.stdout.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                return GitHubPublishResult(
                    repoPath: repoURL.path,
                    copied: stats.copied,
                    downloaded: stats.downloaded,
                    skipped: stats.skipped,
                    pgnFiles: stats.pgnFiles,
                    games: stats.games,
                    committed: false,
                    pushed: false,
                    commitHash: nil,
                    message: "docs/data 没有可提交的暂存变更。",
                    warnings: stats.warnings
                )
            }

            let stamp = ISO8601DateFormatter().string(from: Date())
            try run([
                "git",
                "commit",
                "--only",
                "-m",
                "Update PGN data from manual import",
                "-m",
                "Generated at \(stamp)",
                "--",
                "docs/data"
            ], in: repoURL)
            let commitHash = try run(["git", "rev-parse", "--short", "HEAD"], in: repoURL)
                .stdout
                .trimmingCharacters(in: .whitespacesAndNewlines)
            try run(["git", "push", "origin", "HEAD"], in: repoURL)

            return GitHubPublishResult(
                repoPath: repoURL.path,
                copied: stats.copied,
                downloaded: stats.downloaded,
                skipped: stats.skipped,
                pgnFiles: stats.pgnFiles,
                games: stats.games,
                committed: true,
                pushed: true,
                commitHash: commitHash.isEmpty ? nil : commitHash,
                message: "docs/data 已提交并推送到 GitHub。",
                warnings: stats.warnings
            )
        }.value
    }
}

private struct CommandResult {
    let stdout: String
    let stderr: String
    let status: Int32
}

private struct SyncStatsSnapshot {
    var copied = 0
    var downloaded = 0
    var skipped = 0
    var pgnFiles = 0
    var games = 0
    var warnings: [String] = []
}

private enum GitHubPublisherError: LocalizedError {
    case repositoryNotFound
    case commandFailed(String)

    var errorDescription: String? {
        switch self {
        case .repositoryNotFound:
            "未找到 GitHub 数据仓库。请从仓库内的 dist/中国棋手 PGN.app 启动，或设置 CHINA_CHESS_PGN_REPO。"
        case .commandFailed(let message):
            message
        }
    }
}

private func findRepositoryRoot() throws -> URL {
    var candidates: [URL] = []
    let environment = ProcessInfo.processInfo.environment
    if let explicit = environment["CHINA_CHESS_PGN_REPO"], !explicit.isEmpty {
        candidates.append(URL(fileURLWithPath: explicit, isDirectory: true))
    }
    candidates.append(URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true))
    candidates.append(Bundle.main.bundleURL)
    candidates.append(URL(fileURLWithPath: "/Volumes/AI/code/Playground/china-chess-player-pgn", isDirectory: true))
    candidates.append(URL(fileURLWithPath: "/Users/yan/Documents/Playground/china-chess-player-pgn", isDirectory: true))

    var checked: Set<String> = []
    for candidate in candidates {
        for ancestor in ancestors(of: candidate) {
            let standardized = ancestor.standardizedFileURL.path
            guard !checked.contains(standardized) else { continue }
            checked.insert(standardized)
            if isRepositoryRoot(ancestor) {
                return ancestor
            }
        }
    }
    throw GitHubPublisherError.repositoryNotFound
}

private func ancestors(of url: URL) -> [URL] {
    var result: [URL] = []
    var current = url.hasDirectoryPath ? url : url.deletingLastPathComponent()
    while true {
        result.append(current)
        let parent = current.deletingLastPathComponent()
        if parent.path == current.path {
            break
        }
        current = parent
    }
    return result
}

private func isRepositoryRoot(_ url: URL) -> Bool {
    let manager = FileManager.default
    return manager.fileExists(atPath: url.appendingPathComponent(".git").path)
        && manager.fileExists(atPath: url.appendingPathComponent("Scripts/sync_static_pgn.py").path)
        && manager.fileExists(atPath: url.appendingPathComponent("docs/data").path)
}

@discardableResult
private func run(_ arguments: [String], in workingDirectory: URL) throws -> CommandResult {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
    process.arguments = arguments
    process.currentDirectoryURL = workingDirectory

    let stdoutPipe = Pipe()
    let stderrPipe = Pipe()
    process.standardOutput = stdoutPipe
    process.standardError = stderrPipe

    try process.run()
    process.waitUntilExit()

    let stdout = String(data: stdoutPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    let stderr = String(data: stderrPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    let result = CommandResult(stdout: stdout, stderr: stderr, status: process.terminationStatus)
    guard result.status == 0 else {
        let command = arguments.joined(separator: " ")
        let detail = [stderr, stdout]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: "\n")
        throw GitHubPublisherError.commandFailed("命令失败：\(command)\n\(detail)")
    }
    return result
}

private func parseSyncStats(_ text: String) -> SyncStatsSnapshot {
    guard
        let data = text.data(using: .utf8),
        let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    else {
        return SyncStatsSnapshot(warnings: ["静态同步脚本未返回可解析 JSON。"])
    }

    let errors = payload["errors"] as? [String] ?? []
    let manifest = payload["manifest_pgn"] as? Int ?? 0
    return SyncStatsSnapshot(
        copied: payload["copied"] as? Int ?? 0,
        downloaded: payload["downloaded"] as? Int ?? 0,
        skipped: payload["skipped"] as? Int ?? 0,
        pgnFiles: manifest,
        games: payload["manifest_games"] as? Int ?? 0,
        warnings: errors
    )
}
