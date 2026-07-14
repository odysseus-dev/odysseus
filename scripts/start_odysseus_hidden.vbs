Option Explicit

Dim shell, fso, scriptDir, batchFile
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batchFile = fso.BuildPath(scriptDir, "start_odysseus.bat")

If Not fso.FileExists(batchFile) Then
    MsgBox "Could not find: " & batchFile, vbCritical, "Odysseus startup"
    WScript.Quit 1
End If

' 0 = hidden window, False = do not wait for Uvicorn to exit.
shell.Run """" & batchFile & """", 0, False
