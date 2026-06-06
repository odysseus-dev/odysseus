//
//  OdysseusAppApp.swift
//  OdysseusApp
//
//  Created by Brandon Gray on 6/5/26.
//

import SwiftUI

@main
struct OdysseusAppApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appState)
        }

        Settings {
            SettingsView()
                .environmentObject(appState)
        }
    }
}
