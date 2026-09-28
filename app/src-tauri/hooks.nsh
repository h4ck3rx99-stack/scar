; SCAR installer hooks (Tauri NSIS).
; Uninstall stops SCAR's own runtime (only processes running from SCAR's runtime folder) and asks whether to keep the
; user's SCAR data. Default: keep. Silent uninstalls always keep data.

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'powershell -NoProfile -NonInteractive -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like \"$LOCALAPPDATA\SCAR\runtime\*\" } | Stop-Process -Force"'
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  IfSilent scar_keep_data
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also remove your SCAR data from this computer?$\r$\n$\r$\nThis deletes SCAR's settings, memory, task history and its private Python environment. API keys stored in Windows Credential Manager are not affected.$\r$\n$\r$\nChoose No to keep them (for example, to reinstall later)." IDYES scar_remove_data IDNO scar_keep_data
  scar_remove_data:
    RMDir /r "$LOCALAPPDATA\SCAR"
    RMDir /r "$APPDATA\SCAR"
  scar_keep_data:
!macroend
