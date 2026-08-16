# Z13Tray.ps1 — system tray app for Z13 display/power profiles.
# Launch hidden via Start-Z13Tray.vbs (or: powershell -WindowStyle Hidden -File Z13Tray.ps1)
#
# Tray icon shows the current refresh rate. Right-click for presets, automation
# toggle, and status. Uses Z13Display.psm1 — the SAME code the scheduled task
# runs, so manual and automatic changes can never drift apart.

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# Single-instance guard - a second launch exits immediately instead of adding
# a duplicate tray icon.
# NOTE: $createdNew MUST be declared before [ref] is applied. PowerShell throws
# "[ref] cannot be applied to a variable that does not exist" otherwise.
$createdNew = $false
$script:mutex = New-Object System.Threading.Mutex($true, 'Global\Z13AutomateTray', [ref]$createdNew)
if (-not $createdNew) { exit 0 }

Import-Module (Join-Path $PSScriptRoot 'Z13Display.psm1') -Force

# Win32 DestroyIcon so dynamically-built tray icons don't leak GDI handles
if (-not ('Z13Tray.IconUtil' -as [type])) {
Add-Type @'
using System; using System.Runtime.InteropServices;
namespace Z13Tray { public class IconUtil {
  [DllImport("user32.dll", SetLastError=true)] public static extern bool DestroyIcon(IntPtr h);
} }
'@
}

$script:currentIconHandle = [IntPtr]::Zero

function New-HzIcon {
  param([string]$Text, [bool]$OnBattery)
  $bmp = New-Object System.Drawing.Bitmap 16,16
  $g   = [System.Drawing.Graphics]::FromImage($bmp)
  $g.Clear([System.Drawing.Color]::Transparent)
  $g.TextRenderingHint = 'SingleBitPerPixelGridFit'
  $col = if ($OnBattery) { [System.Drawing.Color]::FromArgb(120,220,120) } else { [System.Drawing.Color]::FromArgb(120,190,255) }
  $brush = New-Object System.Drawing.SolidBrush $col
  $size  = if ($Text.Length -ge 3) { 7 } else { 9 }
  $font  = New-Object System.Drawing.Font 'Segoe UI', $size, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
  $fmt   = New-Object System.Drawing.StringFormat
  $fmt.Alignment = 'Center'; $fmt.LineAlignment = 'Center'
  $g.DrawString($Text, $font, $brush, (New-Object System.Drawing.RectangleF 0,0,16,16), $fmt)
  $g.Dispose()
  $h = $bmp.GetHicon()
  $icon = [System.Drawing.Icon]::FromHandle($h)
  $bmp.Dispose(); $font.Dispose(); $brush.Dispose()
  return @{ Icon = $icon; Handle = $h }
}

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Visible = $true

function Update-Tray {
  $src  = Get-PowerSource
  $hz   = Get-CurrentRefresh
  $br   = Get-CurrentBrightness
  $pct  = Get-BatteryPercent
  $cfg  = Get-Z13Config
  $auto = if ($cfg.AutomationEnabled) { 'on' } else { 'OFF' }

  $hzText = if ($hz) { "$hz" } else { '?' }
  $new = New-HzIcon -Text $hzText -OnBattery ($src -eq 'DC')

  $old = $script:currentIconHandle
  $notify.Icon = $new.Icon
  $script:currentIconHandle = $new.Handle
  if ($old -ne [IntPtr]::Zero) { [void][Z13Tray.IconUtil]::DestroyIcon($old) }

  $battTxt = if ($null -ne $pct) { "$pct%" } else { 'n/a' }
  # NotifyIcon text is capped at 63 chars
  $notify.Text = "Z13 - $src $battTxt | $hzText Hz | bright $br% | auto $auto"
  $script:statusItem.Text = "$src  -  $battTxt  -  $hzText Hz  -  brightness $br%"
  $script:autoItem.Text    = "Automation: $(if($cfg.AutomationEnabled){'Enabled'}else{'Disabled'})"
  $script:autoItem.Checked = [bool]$cfg.AutomationEnabled
}

$menu = New-Object System.Windows.Forms.ContextMenuStrip

# --- status (disabled header) ---
$script:statusItem = New-Object System.Windows.Forms.ToolStripMenuItem 'status'
$script:statusItem.Enabled = $false
$menu.Items.Add($script:statusItem) | Out-Null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

# --- presets ---
foreach ($p in (Get-Z13Config).Presets) {
  $item = New-Object System.Windows.Forms.ToolStripMenuItem ("{0}  ({1} Hz, {2}%)" -f $p.Name, $p.Hz, $p.Brightness)
  $item.Tag = $p
  $item.Add_Click({
    $pp = $this.Tag
    Invoke-Z13Profile -Hz $pp.Hz -Brightness $pp.Brightness -Reason "preset/$($pp.Name)"
    Update-Tray
  }.GetNewClosure())
  $menu.Items.Add($item) | Out-Null
}
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

# --- re-apply for current power source ---
$applyItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Apply profile for current power source'
$applyItem.Add_Click({ Invoke-Z13AutoProfile | Out-Null; Update-Tray })
$menu.Items.Add($applyItem) | Out-Null

# --- automation toggle ---
$script:autoItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Automation'
$script:autoItem.CheckOnClick = $false
$script:autoItem.Add_Click({
  $c = Get-Z13Config
  $c.AutomationEnabled = -not $c.AutomationEnabled
  Set-Z13Config -Config $c
  Write-Z13Log "automation toggled -> $($c.AutomationEnabled)"
  Update-Tray
})
$menu.Items.Add($script:autoItem) | Out-Null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

# --- open folder / log ---
$folderItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Open settings folder'
$folderItem.Add_Click({ Start-Process explorer.exe $PSScriptRoot })
$menu.Items.Add($folderItem) | Out-Null

$logItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Open log'
$logItem.Add_Click({ $l = Get-Z13LogPath; if (Test-Path $l) { Start-Process notepad.exe $l } })
$menu.Items.Add($logItem) | Out-Null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$exitItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Exit'
$exitItem.Add_Click({
  $notify.Visible = $false
  if ($script:currentIconHandle -ne [IntPtr]::Zero) { [void][Z13Tray.IconUtil]::DestroyIcon($script:currentIconHandle) }
  [System.Windows.Forms.Application]::Exit()
})
$menu.Items.Add($exitItem) | Out-Null

$notify.ContextMenuStrip = $menu

# Left-click also opens the menu
$notify.Add_MouseClick({
  if ($_.Button -eq [System.Windows.Forms.MouseButtons]::Left) {
    $m = $notify.GetType().GetMethod('ShowContextMenu', [Reflection.BindingFlags]'NonPublic,Instance')
    $m.Invoke($notify, $null)
  }
})

# Refresh status periodically (also catches changes made by the scheduled task)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({ Update-Tray })
$timer.Start()

Update-Tray
Write-Z13Log 'tray app started'

[System.Windows.Forms.Application]::Run((New-Object System.Windows.Forms.ApplicationContext))
