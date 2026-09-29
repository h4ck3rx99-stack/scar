@echo off
rem Stops Scar: its Electron window, the Python brain and its llama-server.
rem Only processes started from this folder are touched (other Electron apps like VS Code are safe).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = '%~dp0'.TrimEnd('\');" ^
  "$procs = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -like ('*' + $root + '*') -or $_.CommandLine -like '*-m jarvis*') -and $_.Name -in @('electron.exe','python.exe','pythonw.exe','llama-server.exe') };" ^
  "foreach ($p in $procs) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {} }"
if /i not "%~1"=="quiet" echo Scar stopped.
