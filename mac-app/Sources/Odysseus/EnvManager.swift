import Foundation

/// Tiny KEY=value reader/writer for the repo's .env. Preserves comments
/// and ordering on write, replaces existing keys in place (or appends if
/// absent).
///
/// Deliberately not a full dotenv parser — we only handle simple
/// `KEY=value` lines (no quoting, no `${VAR}` interpolation). That's
/// enough for the keys this app touches (ports, PUID/PGID).
enum EnvManager {
    static func read(_ key: String) -> String? {
        guard let contents = try? String(contentsOf: Config.envFile, encoding: .utf8) else { return nil }
        for line in contents.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.hasPrefix("#"),
                  let eq = trimmed.firstIndex(of: "="),
                  trimmed[..<eq] == Substring(key) else { continue }
            let raw = trimmed[trimmed.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            return raw.trimmingCharacters(in: CharacterSet(charactersIn: "\"'"))
        }
        return nil
    }

    static func upsert(_ key: String, _ value: String) {
        let url = Config.envFile
        let existing = (try? String(contentsOf: url, encoding: .utf8)) ?? ""
        var lines = existing.split(omittingEmptySubsequences: false, whereSeparator: \.isNewline).map(String.init)

        let prefix = "\(key)="
        let commentedPrefix = "#\(key)="
        var replaced = false
        for i in lines.indices {
            let trimmed = lines[i].trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix(prefix) || trimmed.hasPrefix(commentedPrefix) {
                lines[i] = "\(key)=\(value)"
                replaced = true
                break
            }
        }
        if !replaced { lines.append("\(key)=\(value)") }

        try? lines.joined(separator: "\n").write(to: url, atomically: true, encoding: .utf8)
    }
}
