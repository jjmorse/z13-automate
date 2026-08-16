' Launches Z13Tray.ps1 with no console window at all.
' (powershell -WindowStyle Hidden still flashes a console briefly; this does not.)
Set sh = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\Z13Tray.ps1""", 0, False
