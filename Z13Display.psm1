# Z13Display.psm1 — shared display/power control for the Z13.
# Used by BOTH the scheduled task (Apply-DisplayPower.ps1) and the tray app
# (Z13Tray.ps1) so there is exactly one implementation of each operation.

$script:Root    = Split-Path -Parent $PSCommandPath
$script:CfgPath = Join-Path $script:Root 'config.json'
$script:LogPath = Join-Path $script:Root 'z13-automate.log'

# ---------------------------------------------------------------- native
# IMPORTANT: PowerShell marshals $null to an EMPTY STRING for P/Invoke string
# parameters. EnumDisplaySettings/ChangeDisplaySettingsEx reject "" as an
# invalid device name and fail. ALWAYS pass an explicit device name.
if (-not ('Z13Native.Disp' -as [type])) {
Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace Z13Native {
  public class Disp {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
    public struct DEVMODE {
      [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
      public ushort dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
      public uint dmFields; public int dmPositionX, dmPositionY;
      public uint dmDisplayOrientation, dmDisplayFixedOutput;
      public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
      [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
      public ushort dmLogPixels;
      public uint dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
      public uint dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
    }
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern int EnumDisplaySettings(string dev, int mode, ref DEVMODE dm);
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern int ChangeDisplaySettingsEx(string dev, ref DEVMODE dm, IntPtr hwnd, uint flags, IntPtr lparam);
  }
}
'@
}
Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- logging
function Write-Z13Log {
  param([string]$Message)
  $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
  Add-Content -Path $script:LogPath -Value $line -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- config
function Get-Z13Config {
  if (Test-Path $script:CfgPath) {
    try { return (Get-Content $script:CfgPath -Raw | ConvertFrom-Json) } catch { Write-Z13Log "config parse failed: $($_.Exception.Message)" }
  }
  # defaults
  return [pscustomobject]@{
    AutomationEnabled = $true
    AC = [pscustomobject]@{ Hz = 180; Brightness = 90 }
    DC = [pscustomobject]@{ Hz = 60;  Brightness = 40 }
  }
}

function Set-Z13Config {
  param([Parameter(Mandatory)]$Config)
  $Config | ConvertTo-Json -Depth 6 | Set-Content -Path $script:CfgPath -Encoding UTF8
}

function Get-Z13ConfigPath { $script:CfgPath }
function Get-Z13LogPath    { $script:LogPath }

# ---------------------------------------------------------------- state
function Get-PowerSource {
  # returns 'AC' or 'DC'
  $b = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue
  if ($null -eq $b) { return 'AC' }          # desktop / no battery
  if ($b.PowerOnline) { 'AC' } else { 'DC' }
}

function Get-BatteryPercent {
  $b = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue
  $f = Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity -ErrorAction SilentlyContinue
  if ($b -and $f -and $f.FullChargedCapacity) { [int](100 * $b.RemainingCapacity / $f.FullChargedCapacity) } else { $null }
}

function Get-DisplayCount { [System.Windows.Forms.Screen]::AllScreens.Count }

function Get-PrimaryDeviceName { [System.Windows.Forms.Screen]::PrimaryScreen.DeviceName }

function Get-InternalDeviceName {
  # The Z13 internal panel is the active display with the highest refresh CAPABILITY
  # (180 Hz); external monitors top out at 60. This is robust regardless of which
  # display is currently "primary" when docked. Falls back to primary if enumeration
  # somehow finds nothing. Used for REPORTING the internal panel's refresh when docked.
  $best = $null; $bestMax = -1
  foreach ($s in [System.Windows.Forms.Screen]::AllScreens) {
    $maxHz = 0; $mode = 0
    while ($true) {
      $dm = New-Object Z13Native.Disp+DEVMODE
      $dm.dmSize = [uint16][System.Runtime.InteropServices.Marshal]::SizeOf([type]'Z13Native.Disp+DEVMODE')
      if ([Z13Native.Disp]::EnumDisplaySettings($s.DeviceName, $mode, [ref]$dm) -eq 0) { break }
      if ($dm.dmDisplayFrequency -gt $maxHz) { $maxHz = $dm.dmDisplayFrequency }
      $mode++
    }
    if ($maxHz -gt $bestMax) { $bestMax = $maxHz; $best = $s.DeviceName }
  }
  if ($best) { $best } else { Get-PrimaryDeviceName }
}

function Get-CurrentRefresh {
  $dev = Get-InternalDeviceName
  $dm = New-Object Z13Native.Disp+DEVMODE
  $dm.dmSize = [uint16][System.Runtime.InteropServices.Marshal]::SizeOf([type]'Z13Native.Disp+DEVMODE')
  if ([Z13Native.Disp]::EnumDisplaySettings($dev, -1, [ref]$dm) -eq 0) { return $null }
  [int]$dm.dmDisplayFrequency
}

function Get-CurrentBrightness {
  try { [int](Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness -ErrorAction Stop).CurrentBrightness }
  catch { $null }
}

# ---------------------------------------------------------------- actions
function Set-DisplayRefresh {
  param([Parameter(Mandatory)][int]$Hz)
  $dev = Get-PrimaryDeviceName
  $DM_PELSWIDTH = 0x80000; $DM_PELSHEIGHT = 0x100000; $DM_DISPLAYFREQUENCY = 0x400000
  $CDS_UPDATEREGISTRY = 0x01

  $dm = New-Object Z13Native.Disp+DEVMODE
  $dm.dmSize = [uint16][System.Runtime.InteropServices.Marshal]::SizeOf([type]'Z13Native.Disp+DEVMODE')
  if ([Z13Native.Disp]::EnumDisplaySettings($dev, -1, [ref]$dm) -eq 0) {
    Write-Z13Log "EnumDisplaySettings failed on $dev"; return $false
  }
  if ($dm.dmDisplayFrequency -eq $Hz) { Write-Z13Log "refresh already $Hz Hz"; return $true }

  $dm.dmDisplayFrequency = $Hz
  $dm.dmFields = $DM_PELSWIDTH -bor $DM_PELSHEIGHT -bor $DM_DISPLAYFREQUENCY
  $r = [Z13Native.Disp]::ChangeDisplaySettingsEx($dev, [ref]$dm, [IntPtr]::Zero, $CDS_UPDATEREGISTRY, [IntPtr]::Zero)
  if ($r -eq 0) { Write-Z13Log "refresh -> $Hz Hz on $dev"; return $true }
  Write-Z13Log "ChangeDisplaySettingsEx returned $r for $Hz Hz"; return $false
}

function Set-DisplayBrightness {
  param([Parameter(Mandatory)][ValidateRange(0,100)][int]$Percent)
  try {
    $m = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop
    Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{ Timeout = 0; Brightness = $Percent } -ErrorAction Stop | Out-Null
    Write-Z13Log "brightness -> $Percent%"; return $true
  } catch { Write-Z13Log "brightness failed: $($_.Exception.Message)"; return $false }
}

function Invoke-Z13Profile {
  <#  Applies Hz (and optionally brightness). Refresh is skipped when more than
      one display is attached so external monitors are never touched.

      BRIGHTNESS IS DISABLED BY DEFAULT - see below.  #>
  param(
    [Parameter(Mandatory)][int]$Hz,
    [Parameter(Mandatory)][int]$Brightness,
    [string]$Reason = ''
  )
  $count = Get-DisplayCount
  $cfg = Get-Z13Config
  # Default to NOT managing brightness unless explicitly enabled in config.
  $manage = $false
  if ($null -ne $cfg.ManageBrightness) { $manage = [bool]$cfg.ManageBrightness }

  Write-Z13Log ("--- apply {0}Hz{1} ({2}) displays={3}" -f $Hz, $(if($manage){"/$Brightness%"}else{" (brightness: Windows)"}), $Reason, $count)
  if ($count -eq 1) { Set-DisplayRefresh -Hz $Hz | Out-Null }
  else { Write-Z13Log "skipping refresh ($count displays / docked)" }

  # WHY BRIGHTNESS IS OFF BY DEFAULT (found 2026-07-31):
  # Setting brightness via WMI makes Windows WRITE that value into the active
  # power plan's brightness setting FOR THE CURRENT POWER SOURCE. This task
  # fires on Kernel-Power EventID 105, which lands BEFORE Windows has finished
  # switching power source. So on unplug we'd set 40% while Windows still
  # thought it was on AC -> it recorded AC brightness = 40% -> plugging back in
  # then restored 40% instead of 90%. Our own writes were poisoning the AC value.
  # Windows already does per-source brightness natively and applies it at the
  # correct moment. Let it. Configure with:
  #   powercfg /setacvalueindex SCHEME_CURRENT 7516b95f-f776-4464-8c53-06167f40cc99 aded5e82-b909-4619-9949-f5d71dac0bcb 90
  #   powercfg /setdcvalueindex SCHEME_CURRENT 7516b95f-f776-4464-8c53-06167f40cc99 aded5e82-b909-4619-9949-f5d71dac0bcb 40
  #   powercfg /setactive SCHEME_CURRENT
  if ($manage) { Set-DisplayBrightness -Percent $Brightness | Out-Null }
}

function Invoke-Z13AutoProfile {
  <#  Applies the profile matching the current power source.

      SETTLE DELAY: this runs from a Kernel-Power EventID 105 trigger, which
      fires BEFORE Windows has finished switching power source. Writing
      brightness that early makes Windows attribute the value to the OLD source
      (setting 40% on unplug got recorded as the AC brightness, so plugging back
      in restored 40%). Wait for the reported source to stop changing first.  #>
  $cfg = Get-Z13Config

  $src = Get-PowerSource
  for ($i = 0; $i -lt 8; $i++) {
    Start-Sleep -Milliseconds 700
    $now = Get-PowerSource
    if ($now -eq $src) { break }   # stable
    $src = $now                    # changed - keep waiting
  }
  Start-Sleep -Milliseconds 800    # extra margin after it settles

  $src = Get-PowerSource           # authoritative read
  $p   = if ($src -eq 'DC') { $cfg.DC } else { $cfg.AC }
  Invoke-Z13Profile -Hz $p.Hz -Brightness $p.Brightness -Reason "auto/$src"
  return $src
}

Export-ModuleMember -Function Write-Z13Log, Get-Z13Config, Set-Z13Config, Get-Z13ConfigPath, Get-Z13LogPath,
  Get-PowerSource, Get-BatteryPercent, Get-DisplayCount, Get-PrimaryDeviceName, Get-InternalDeviceName,
  Get-CurrentRefresh, Get-CurrentBrightness,
  Set-DisplayRefresh, Set-DisplayBrightness, Invoke-Z13Profile, Invoke-Z13AutoProfile
