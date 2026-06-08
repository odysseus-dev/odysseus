//
//  Generated file. Do not edit.
//

// clang-format off

#include "generated_plugin_registrant.h"

#include <fullscreen_window/fullscreen_window_plugin.h>
#include <screen_retriever_linux/screen_retriever_linux_plugin.h>
#include <webview_win_floating/webview_win_floating_plugin.h>
#include <window_manager/window_manager_plugin.h>

void fl_register_plugins(FlPluginRegistry* registry) {
  g_autoptr(FlPluginRegistrar) fullscreen_window_registrar =
      fl_plugin_registry_get_registrar_for_plugin(registry, "FullscreenWindowPlugin");
  fullscreen_window_plugin_register_with_registrar(fullscreen_window_registrar);
  g_autoptr(FlPluginRegistrar) screen_retriever_linux_registrar =
      fl_plugin_registry_get_registrar_for_plugin(registry, "ScreenRetrieverLinuxPlugin");
  screen_retriever_linux_plugin_register_with_registrar(screen_retriever_linux_registrar);
  g_autoptr(FlPluginRegistrar) webview_win_floating_registrar =
      fl_plugin_registry_get_registrar_for_plugin(registry, "WebviewWinFloatingPlugin");
  webview_win_floating_plugin_register_with_registrar(webview_win_floating_registrar);
  g_autoptr(FlPluginRegistrar) window_manager_registrar =
      fl_plugin_registry_get_registrar_for_plugin(registry, "WindowManagerPlugin");
  window_manager_plugin_register_with_registrar(window_manager_registrar);
}
