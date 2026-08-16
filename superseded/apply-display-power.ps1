# Apply display settings based on power source.
# On BATTERY: drop internal panel to 60 Hz + lower brightness (save power).
# On AC:      restore 180 Hz + higher brightness.
#
#   apply-display-power.ps1            # auto-detect power source
#   apply-display-power.ps1 -Force DC  # pretend on battery (for testing)
#   apply-display-power.ps1 -Force AC  # pretend on AC (for testing)
#
# Refresh is only changed when a SINGLE display is active (undocked). When
# docked (2+ displays) refresh is left alone so external monitors aren't
# touched; brightness (internal panel only) is still managed.

param([ValidateSet('AC','DC')][string]$Force)

# ---- Config (edit these) ----
$DC_Hz = 60;  $AC_Hz = 180
$DC_Brightness = 40;  $AC_Brightness = 90
$logFile = "$env:USERPROFILE\OneDrive\Documents\Claude\power-display.log"
# -----------------------------

function Log($m) {
  $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

Add-Type @'
using System;
using System.Runtime.InteropServices;
public class DispApi {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)]
  public struct DEVMODE {
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmDeviceName;
    public ushort dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra;
    public uint dmFields;
    public int dmPositionX, dmPositionY;
    public uint dmDisplayOrientation, dmDisplayFixedOutput;
    public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=32)] public string dmFormName;
    public ushort dmLogPixels;
    public uint dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
    public uint dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2;
    public uint dmPanningWidth, dmPanningHeight;
  }
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern int EnumDisplaySettings(string dev, int mode, ref DEVMODE dm);
  [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)] public static extern int ChangeDisplaySettingsEx(string dev, ref DEVMODE dm, IntPtr hwnd, uint flags, IntPtr lparam);
}
'@ -ErrorAction SilentlyContinue

function Set-Refresh([int]$hz) {
  # NOTE: PowerShell marshals $null to an EMPTY STRING for P/Invoke string params,
  # which EnumDisplaySettings rejects as an invalid device name. Always pass an
  # explicit device name (or [NullString]::Value) -- never $null.
  Add-Type -AssemblyName System.Windows.Forms
  $dev = [System.Windows.Forms.Screen]::PrimaryScreen.DeviceName

  $ENUM_CURRENT = -1
  $DM_PELSWIDTH = 0x80000; $DM_PELSHEIGHT = 0x100000; $DM_DISPLAYFREQUENCY = 0x400000
  $CDS_UPDATEREGISTRY = 0x01
  $dm = New-Object DispApi+DEVMODE
  $dm.dmSize = [uint16][System.Runtime.InteropServices.Marshal]::SizeOf([type]'DispApi+DEVMODE')
  if ([DispApi]::EnumDisplaySettings($dev, $ENUM_CURRENT, [ref]$dm) -eq 0) { Log "EnumDisplaySettings failed on $dev"; return $false }
  if ($dm.dmDisplayFrequency -eq $hz) { Log "refresh already $hz Hz"; return $true }
  $dm.dmDisplayFrequency = $hz
  $dm.dmFields = $DM_PELSWIDTH -bor $DM_PELSHEIGHT -bor $DM_DISPLAYFREQUENCY
  $r = [DispApi]::ChangeDisplaySettingsEx($dev, [ref]$dm, [IntPtr]::Zero, $CDS_UPDATEREGISTRY, [IntPtr]::Zero)
  if ($r -eq 0) { Log "refresh set to $hz Hz OK on $dev"; return $true } else { Log "ChangeDisplaySettingsEx returned $r for $hz Hz"; return $false }
}

function Set-Brightness([int]$pct) {
  try {
    $m = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop
    Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{ Timeout = 0; Brightness = $pct } -ErrorAction Stop | Out-Null
    Log "brightness set to $pct%"; return $true
  } catch { Log "brightness failed: $($_.Exception.Message)"; return $false }
}

# ---- Determine power source ----
if ($Force) { $onBattery = ($Force -eq 'DC') }
else {
  $b = Get-CimInstance -Namespace root\wmi -ClassName BatteryStatus -ErrorAction SilentlyContinue
  $onBattery = $b -and (-not $b.PowerOnline)
}

Add-Type -AssemblyName System.Windows.Forms
$displayCount = [System.Windows.Forms.Screen]::AllScreens.Count

$hz  = if ($onBattery) { $DC_Hz } else { $AC_Hz }
$br  = if ($onBattery) { $DC_Brightness } else { $AC_Brightness }
Log ("--- source={0} displays={1} -> target {2}Hz / {3}%" -f $(if($onBattery){'BATTERY'}else{'AC'}), $displayCount, $hz, $br)

# Refresh only when undocked (single display); brightness always
if ($displayCount -eq 1) { Set-Refresh $hz | Out-Null }
else { Log "skipping refresh change ($displayCount displays / docked)" }
Set-Brightness $br | Out-Null

# Report result
$dm = New-Object DispApi+DEVMODE
$dm.dmSize = [uint16][System.Runtime.InteropServices.Marshal]::SizeOf([type]'DispApi+DEVMODE')
[DispApi]::EnumDisplaySettings([System.Windows.Forms.Screen]::PrimaryScreen.DeviceName, -1, [ref]$dm) | Out-Null
"source={0} displays={1} now={2}Hz brightness_target={3}%" -f $(if($onBattery){'BATTERY'}else{'AC'}), $displayCount, $dm.dmDisplayFrequency, $br
