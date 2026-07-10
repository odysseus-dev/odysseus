; electron-builder preserves intentionally removed shortcuts during updates.
; Simple Signal's installer should repair both launch surfaces so upgrading
; from a build that omitted them produces a desktop icon and a searchable
; Start Menu entry without requiring an uninstall first.
!macro customInstall
  CreateShortCut "$newStartMenuLink" "$appExe" "" "$appExe" 0 "" "" "${APP_DESCRIPTION}"
  WinShell::SetLnkAUMI "$newStartMenuLink" "${APP_ID}"

  CreateShortCut "$newDesktopLink" "$appExe" "" "$appExe" 0 "" "" "${APP_DESCRIPTION}"
  WinShell::SetLnkAUMI "$newDesktopLink" "${APP_ID}"

  System::Call 'Shell32::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'
!macroend
