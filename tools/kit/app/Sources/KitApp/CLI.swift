import AppKit
import Foundation

public struct KitConfig {
    public static let python = "/usr/bin/python3"
    public var launcher: String
    public var kit: String
    public var bundle: String
    public var work: String
    public var xcodeSelect: [String]
    public var environment: [String: String]?
    public var canBuild = true

    public init(launcher: String, kit: String? = nil, bundle: String, work: String, xcodeSelect: [String] = ["/usr/bin/xcode-select"], environment: [String: String]? = nil) {
        self.launcher = launcher
        self.kit = kit ?? (URL(fileURLWithPath: launcher).deletingLastPathComponent().appendingPathComponent("kit.py").path)
        self.bundle = bundle
        self.work = work
        self.xcodeSelect = xcodeSelect
        self.environment = environment
    }

    public static let buildNeedsKitNote = "Build needs the porting kit folder"

    public static func fromArguments(_ args: [String], bundleURL: URL? = Bundle.main.bundleURL, resourceURL: URL? = Bundle.main.resourceURL) -> KitConfig {
        func value(_ name: String) -> String? {
            guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
            return args[i + 1]
        }
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        var launcher = value("--cli")
        var kit = value("--kit")
        var canBuild = true
        if launcher == nil {
            let candidates = [resourceURL?.appendingPathComponent("porting-kit/src/tools/kit"), bundleURL?.deletingLastPathComponent().appendingPathComponent("src/tools/kit")]
            let folder = candidates.compactMap { $0 }.first {
                FileManager.default.fileExists(atPath: $0.appendingPathComponent("launcher.py").path)
                    && FileManager.default.fileExists(atPath: $0.appendingPathComponent("kit.py").path)
            }
            if let folder {
                launcher = folder.appendingPathComponent("launcher.py").path
                kit = kit ?? folder.appendingPathComponent("kit.py").path
            } else {
                launcher = resourceURL?.appendingPathComponent("kit/launcher.py").path ?? "launcher.py"
                canBuild = kit != nil
            }
        }
        var config = KitConfig(
            launcher: launcher ?? "launcher.py",
            kit: kit,
            bundle: value("--bundle") ?? Names.app(home: home),
            work: value("--work") ?? Names.work(home: home)
        )
        config.canBuild = canBuild
        return config
    }

    public func launcherArgv(_ command: [String]) -> [String] {
        [KitConfig.python, "-I", "-B", launcher] + command + ["--bundle", bundle]
    }

    public func kitArgv(_ command: [String]) -> [String] {
        [KitConfig.python, "-I", "-B", kit] + command
    }
}

public struct CLIResult {
    public var exit: Int32
    public var stdout: [String]
    public var stderr: String
    public var interrupted: Bool
}

public enum ProgressLine: Equatable {
    case step(name: String, n: Int?, of: Int?, done: Int?, total: Int?, fraction: Double?, elapsed: Double?)
    case log(String)
    case result(pass: Bool, exit: Int, message: String)

    public static func parse(_ line: String) -> ProgressLine {
        guard let data = line.data(using: .utf8),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return .log(line) }
        if let text = obj["log"] as? String { return .log(text) }
        if let r = obj["result"] as? String {
            return .result(pass: r == "pass", exit: (obj["exit"] as? NSNumber)?.intValue ?? 0, message: obj["message"] as? String ?? "")
        }
        if let s = obj["step"] as? String {
            func int(_ k: String) -> Int? { (obj[k] as? NSNumber)?.intValue }
            func dbl(_ k: String) -> Double? { (obj[k] as? NSNumber)?.doubleValue }
            return .step(name: s, n: int("n"), of: int("of"), done: int("done"), total: int("total"), fraction: dbl("fraction"), elapsed: dbl("elapsed"))
        }
        return .log(line)
    }
}

public final class CLIRunner: @unchecked Sendable {
    public let environment: [String: String]?
    private let lock = NSLock()
    private var current: Process?
    private var interrupted = false

    public init(environment: [String: String]? = nil) {
        self.environment = environment
    }

    private static func chunks(_ handle: FileHandle) -> AsyncStream<Data> {
        AsyncStream { c in
            handle.readabilityHandler = { h in
                let d = h.availableData
                if d.isEmpty {
                    h.readabilityHandler = nil
                    c.finish()
                } else {
                    c.yield(d)
                }
            }
        }
    }

    public func run(_ argv: [String], cancellable: Bool = false, onLine: @escaping @MainActor (String) -> Void = { _ in }) async -> CLIResult {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: argv[0])
        p.arguments = Array(argv.dropFirst())
        if let environment {
            p.environment = ProcessInfo.processInfo.environment.merging(environment) { $1 }
        }
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        p.standardInput = FileHandle.nullDevice
        let done = AsyncStream<Void> { c in p.terminationHandler = { _ in c.yield(); c.finish() } }
        let outChunks = Self.chunks(out.fileHandleForReading)
        let errChunks = Self.chunks(err.fileHandleForReading)
        do {
            try p.run()
        } catch {
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            return CLIResult(exit: 127, stdout: [], stderr: error.localizedDescription, interrupted: false)
        }
        if cancellable { lock.withLock { current = p; interrupted = false } }
        let errTask = Task.detached { () -> String in
            var all = Data()
            for await d in errChunks { all.append(d) }
            return String(decoding: all, as: UTF8.self)
        }
        var lines: [String] = []
        var pending = Data()
        func emit(_ bytes: Data) async {
            var line = String(decoding: bytes, as: UTF8.self)
            if line.hasSuffix("\r") { line.removeLast() }
            lines.append(line)
            await onLine(line)
        }
        for await d in outChunks {
            pending.append(d)
            while let nl = pending.firstIndex(of: 0x0A) {
                let bytes = pending[pending.startIndex..<nl]
                pending = Data(pending[pending.index(after: nl)...])
                await emit(Data(bytes))
            }
        }
        if !pending.isEmpty { await emit(pending) }
        for await _ in done {}
        let stderr = await errTask.value
        let wasInterrupted = cancellable ? lock.withLock { () -> Bool in current = nil; return interrupted } : false
        return CLIResult(exit: p.terminationStatus, stdout: lines, stderr: stderr.trimmingCharacters(in: .whitespacesAndNewlines), interrupted: wasInterrupted)
    }

    public func interrupt() {
        lock.withLock {
            guard let p = current, p.isRunning else { return }
            interrupted = true
            let pid = p.processIdentifier
            if getpgid(pid) == pid {
                kill(-pid, SIGINT)
            } else {
                p.interrupt()
            }
        }
    }
}

public struct KitShell {
    public var open: (URL) -> Void
    public var reveal: (URL) -> Void
    public var showGame: () -> Void
    public var screens: () -> [String]

    public init(open: @escaping (URL) -> Void, reveal: @escaping (URL) -> Void, showGame: @escaping () -> Void, screens: @escaping () -> [String]) {
        self.open = open
        self.reveal = reveal
        self.showGame = showGame
        self.screens = screens
    }

    public static let live = KitShell(
        open: { NSWorkspace.shared.open($0) },
        reveal: { NSWorkspace.shared.activateFileViewerSelecting([$0]) },
        showGame: {
            NSRunningApplication.runningApplications(withBundleIdentifier: Names.bundleID).first?.activate(options: [])
        },
        screens: { NSScreen.screens.map(\.localizedName) }
    )
}
