' Starts Scar with no console window. Used by the Windows Startup shortcut.
' Waits 20 s after login so Windows, Wi-Fi and audio are ready before the models load.
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
WScript.Sleep 20000
shell.CurrentDirectory = here
shell.Run """" & here & "\Start_Scar.bat""", 0, False
