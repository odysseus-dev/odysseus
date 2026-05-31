import SwiftUI

/// Modal sheet presented when one or more required host ports are taken.
/// Each row pre-fills with a probed-free alternative; users can override
/// before hitting Continue. The chosen values are written to .env and
/// docker-compose substitutes them on the next start.
struct PortConflictView: View {
    let conflicts: [PortManager.Conflict]
    let onResolve: ([String: Int]) -> Void

    /// Keyed by envKey so SwiftUI can drive the TextField bindings without
    /// re-deriving from `conflicts` on every keystroke.
    @State private var chosen: [String: Int] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: "network.slash")
                    .font(.system(size: 28))
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Port conflict").font(.title3).bold()
                    Text("Odysseus needs a few host ports that are already in use. Pick alternatives below.")
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            ForEach(conflicts) { c in
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(c.label).font(.headline)
                        Text("Port \(c.port) is held by **\(c.occupiedBy)**")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    TextField(
                        "Port",
                        value: Binding(
                            get: { chosen[c.envKey] ?? c.suggested },
                            set: { chosen[c.envKey] = $0 }
                        ),
                        format: .number.grouping(.never)
                    )
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 90)
                    .multilineTextAlignment(.center)
                }
                .padding(12)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
            }

            HStack {
                Spacer()
                Button("Continue") {
                    var resolved: [String: Int] = [:]
                    for c in conflicts {
                        resolved[c.envKey] = chosen[c.envKey] ?? c.suggested
                    }
                    onResolve(resolved)
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 480)
    }
}
