# Creates (or refreshes) "StudyTube" shortcuts on the Desktop and in the Start Menu.
$dir    = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $dir ".venv\Scripts\pythonw.exe"   # pythonw = no console window
$script = Join-Path $dir "launcher.py"
$icon   = Join-Path $dir "icon.ico"
$shell  = New-Object -ComObject WScript.Shell

$places = @(
  [Environment]::GetFolderPath("Desktop"),
  (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)
foreach ($p in $places) {
  $lnk = $shell.CreateShortcut((Join-Path $p "StudyTube.lnk"))
  $lnk.TargetPath       = $target
  $lnk.Arguments        = "`"$script`""
  $lnk.WorkingDirectory = $dir
  $lnk.IconLocation     = "$icon,0"
  $lnk.Description      = "Find the best video for a topic and read its summary"
  $lnk.Save()
  Write-Host "   shortcut: $p\StudyTube.lnk"
  # same taskbar identity as the running app, so a pinned icon and the open window are one button
  & (Join-Path $dir ".venv\Scripts\python.exe") (Join-Path $dir "winshell.py") stamp (Join-Path $p "StudyTube.lnk")
}

# ask Explorer to refresh icons so the new artwork shows immediately
try { & ie4uinit.exe -show } catch {}
