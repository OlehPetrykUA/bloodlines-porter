import Foundation

public enum Names {
    public static let gameTitle = "Vampire Bloodlines"
    public static let appName = gameTitle + ".app"
    public static let bundleID = "local.vampire-bloodlines"
    public static let kitTitle = "Bloodlines Porter"
    public static let kitAppName = kitTitle + ".app"
    public static let supportName = "Vampire Bloodlines"
    public static let cacheName = "Vampire Bloodlines"
    public static let installDir = "Games"
    public static let installerPattern = "^vtmbup.*\\.exe$"

    public static func app(home: String) -> String { home + "/" + installDir + "/" + appName }
    public static func support(home: String) -> String { home + "/Library/Application Support/" + supportName }
    public static func work(home: String) -> String { home + "/Library/Caches/" + cacheName }
    public static func tilde(_ path: String, home: String) -> String { path.hasPrefix(home + "/") ? "~" + path.dropFirst(home.count) : path }

    public static func installers(in folder: String) -> [String] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder)) ?? []
        return names.filter { $0.range(of: installerPattern, options: [.regularExpression, .caseInsensitive]) != nil }
            .sorted()
            .map { URL(fileURLWithPath: folder).appendingPathComponent($0).path }
    }
}
