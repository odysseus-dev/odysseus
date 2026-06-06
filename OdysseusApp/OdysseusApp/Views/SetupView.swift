import SwiftUI

struct SetupView: View {
    @EnvironmentObject var appState: AppState
    @State private var username = "admin"
    @State private var password = ""
    @State private var confirm = ""
    @State private var isRunning = false
    @State private var validationError: String? = nil

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 28) {
                VStack(spacing: 8) {
                    HStack(spacing: 12) {
                        BoatIcon()
                            .frame(width: 38, height: 38)
                            .foregroundColor(.accentColor)
                        Text("Welcome to Odysseus")
                            .font(.largeTitle.bold())
                    }

                    Text("Create your admin account to get started.")
                        .foregroundColor(.secondary)
                }

                VStack(alignment: .leading, spacing: 16) {
                    LabeledField(label: "Username") {
                        TextField("admin", text: $username)
                            .textFieldStyle(.roundedBorder)
                            .autocorrectionDisabled()
                    }

                    LabeledField(label: "Password") {
                        SecureField("Required", text: $password)
                            .textFieldStyle(.roundedBorder)
                    }

                    LabeledField(label: "Confirm") {
                        SecureField("Re-enter password", text: $confirm)
                            .textFieldStyle(.roundedBorder)
                    }

                    if let error = validationError {
                        Text(error)
                            .font(.caption)
                            .foregroundColor(.red)
                    }
                }
                .frame(width: 340)

                Button(action: submit) {
                    if isRunning {
                        ProgressView()
                            .controlSize(.small)
                            .frame(width: 160)
                    } else {
                        Text("Create Account & Launch")
                            .frame(width: 160)
                    }
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)
                .disabled(isRunning)
            }

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
    }

    private func submit() {
        let trimmedUser = username.trimmingCharacters(in: .whitespaces)
        guard !trimmedUser.isEmpty else {
            validationError = "Username cannot be empty."
            return
        }
        guard password.count >= 8 else {
            validationError = "Password must be at least 8 characters."
            return
        }
        guard password == confirm else {
            validationError = "Passwords don't match."
            return
        }
        validationError = nil
        isRunning = true
        Task {
            await ServerManager.shared.runSetupAndStart(
                username: trimmedUser,
                password: password,
                appState: appState
            )
            isRunning = false
        }
    }
}

private struct LabeledField<Content: View>: View {
    let label: String
    @ViewBuilder let content: Content

    var body: some View {
        HStack(alignment: .center) {
            Text(label)
                .frame(width: 72, alignment: .trailing)
                .foregroundColor(.secondary)
            content
        }
    }
}
