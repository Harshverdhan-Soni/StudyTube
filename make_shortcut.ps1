# Creates (or refreshes) "ekagra" shortcuts on the Desktop and in the Start Menu.
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
  # remove the old StudyTube shortcut from before the rename
  $old = Join-Path $p "StudyTube.lnk"
  if (Test-Path $old) { Remove-Item $old -Force -ErrorAction SilentlyContinue }
  $lnk = $shell.CreateShortcut((Join-Path $p "ekagra.lnk"))
  $lnk.TargetPath       = $target
  $lnk.Arguments        = "`"$script`""
  $lnk.WorkingDirectory = $dir
  $lnk.IconLocation     = "$icon,0"
  $lnk.Description      = "ekagra: Focused learning, from curiosity to mastery."
  $lnk.Save()
  Write-Host "   shortcut: $p\ekagra.lnk"
  # same taskbar identity as the running app, so a pinned icon and the open window are one button
  & (Join-Path $dir ".venv\Scripts\python.exe") (Join-Path $dir "winshell.py") stamp (Join-Path $p "ekagra.lnk")
}

# ask Explorer to refresh icons so the new artwork shows immediately
try { & ie4uinit.exe -show } catch {}
