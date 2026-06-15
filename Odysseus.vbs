' Odysseus desktop entry - opens the GUI launcher without a console flash.
' Contributed by Wykeve (VoidCat) - 2026.
Set sh = CreateObject("WScript.Shell")
repo = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
launcher = repo & "\launcher\Odysseus.Launcher.ps1"
cmd = "powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & launcher & """"
sh.Run cmd, 0, False