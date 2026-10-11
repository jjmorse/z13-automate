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
if (-not ('Z13Native.CoreAudio' -as [type])) {
Add-Type @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
namespace Z13Native {

  // Present-device lookup via CfgMgr32. Reads the device list directly, because
  // Get-PnpDevice and Win32_PnPEntity cost 200-650 ms per call on this machine.
  public static class DevTree {
    [DllImport("cfgmgr32.dll", CharSet=CharSet.Unicode)]
    static extern uint CM_Get_Device_ID_List_SizeW(out uint len, IntPtr filter, uint flags);
    [DllImport("cfgmgr32.dll", CharSet=CharSet.Unicode)]
    static extern uint CM_Get_Device_ID_ListW(IntPtr filter, byte[] buffer, uint len, uint flags);
    [DllImport("cfgmgr32.dll", CharSet=CharSet.Unicode)]
    static extern uint CM_Locate_DevNodeW(out uint devInst, string deviceId, uint flags);
    [DllImport("cfgmgr32.dll", CharSet=CharSet.Unicode)]
    static extern uint CM_Get_DevNode_Registry_PropertyW(uint devInst, uint property, IntPtr regType, byte[] buffer, ref uint length, uint flags);

    const uint CM_GETIDLIST_FILTER_PRESENT = 0x100;
    const uint CM_DRP_DEVICEDESC = 0x1;
    const uint CM_DRP_FRIENDLYNAME = 0xD;

    static string Prop(uint devInst, uint property, byte[] buffer) {
      uint length = (uint)buffer.Length;
      if (CM_Get_DevNode_Registry_PropertyW(devInst, property, IntPtr.Zero, buffer, ref length, 0) != 0) return null;
      return Encoding.Unicode.GetString(buffer, 0, (int)length).TrimEnd('\0');
    }

    // True if a PRESENT device's FriendlyName or DeviceDesc contains needle (case-insensitive).
    // Throws if the device list cannot be read, so a failed check is never taken as "undocked".
    public static bool IsPresentNameContaining(string needle) {
      if (string.IsNullOrEmpty(needle)) throw new ArgumentException("needle is empty");
      uint size;
      if (CM_Get_Device_ID_List_SizeW(out size, IntPtr.Zero, CM_GETIDLIST_FILTER_PRESENT) != 0)
        throw new InvalidOperationException("CM_Get_Device_ID_List_Size failed");
      byte[] idBuffer = new byte[(int)size * 2 + 2];
      if (CM_Get_Device_ID_ListW(IntPtr.Zero, idBuffer, size, CM_GETIDLIST_FILTER_PRESENT) != 0)
        throw new InvalidOperationException("CM_Get_Device_ID_List failed");
      string[] ids = Encoding.Unicode.GetString(idBuffer).Split(new char[] { '\0' }, StringSplitOptions.RemoveEmptyEntries);
      if (ids.Length == 0) throw new InvalidOperationException("no present devices enumerated");
      byte[] buffer = new byte[4096];
      foreach (string id in ids) {
        uint devInst;
        if (CM_Locate_DevNodeW(out devInst, id, 0) != 0) continue;
        string friendly = Prop(devInst, CM_DRP_FRIENDLYNAME, buffer);
        if (friendly != null && friendly.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0) return true;
        string desc = Prop(devInst, CM_DRP_DEVICEDESC, buffer);
        if (desc != null && desc.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0) return true;
      }
      return false;
    }
  }

  // Core Audio endpoint mute (MMDevice API). Vtable slots were checked against mmdeviceapi.h and endpointvolume.h.
  public class AudioEndpoint {
    public string Id;
    public string Name;
  }

  [StructLayout(LayoutKind.Sequential)]
  public struct PROPERTYKEY {
    public Guid fmtid;
    public uint pid;
  }

  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
  public class MMDeviceEnumeratorComObject { }

  [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IMMDeviceEnumerator {
    void EnumAudioEndpoints(int dataFlow, int stateMask, out IMMDeviceCollection devices);
    void GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
    void GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
    void RegisterEndpointNotificationCallback(IntPtr client);
    void UnregisterEndpointNotificationCallback(IntPtr client);
  }

  [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IMMDeviceCollection {
    void GetCount(out uint count);
    void Item(uint index, out IMMDevice device);
  }

  [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IMMDevice {
    void Activate(ref Guid iid, int clsCtx, IntPtr activationParams, out IAudioEndpointVolume endpointVolume);
    void OpenPropertyStore(int access, out IPropertyStore properties);
    void GetId(out IntPtr id);
    void GetState(out int state);
  }

  [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IPropertyStore {
    void GetCount(out uint count);
    void GetAt(uint index, out PROPERTYKEY key);
    void GetValue(ref PROPERTYKEY key, IntPtr value);
    void SetValue(ref PROPERTYKEY key, IntPtr value);
    void Commit();
  }

  [ComImport, Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  public interface IAudioEndpointVolume {
    // Slots 3-13 are the level, channel, step and range methods. They are not used; they only
    // keep SetMute and GetMute at their real vtable offsets (14 and 15).
    void Slot3(); void Slot4(); void Slot5(); void Slot6(); void Slot7(); void Slot8(); void Slot9();
    void Slot10(); void Slot11(); void Slot12(); void Slot13();
    void SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, IntPtr eventContext);
    void GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
  }

  public static class CoreAudio {
    const int CLSCTX_ALL = 23;          // INPROC_SERVER | INPROC_HANDLER | LOCAL_SERVER | REMOTE_SERVER
    const int eRender = 0;
    const int DEVICE_STATE_ACTIVE = 1;
    const int VT_LPWSTR = 31;

    [DllImport("ole32.dll")]
    static extern int PropVariantClear(IntPtr pvar);

    static void Release(object com) {
      if (com != null && Marshal.IsComObject(com)) Marshal.ReleaseComObject(com);
    }

    // PKEY_Device_FriendlyName (pid 14) gives "Speakers (Realtek(R) Audio)". Pid 2 is just "Speakers" on this machine.
    static string FriendlyName(IPropertyStore props) {
      PROPERTYKEY key = new PROPERTYKEY();
      key.fmtid = new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0");
      key.pid = 14;
      IntPtr pv = Marshal.AllocHGlobal(32);
      try {
        for (int i = 0; i < 32; i += 8) Marshal.WriteInt64(pv, i, 0);
        props.GetValue(ref key, pv);
        if (Marshal.ReadInt16(pv, 0) != VT_LPWSTR) return null;
        return Marshal.PtrToStringUni(Marshal.ReadIntPtr(pv, 8));
      } finally {
        PropVariantClear(pv);
        Marshal.FreeHGlobal(pv);
      }
    }

    public static AudioEndpoint[] GetActiveRenderEndpoints() {
      List<AudioEndpoint> list = new List<AudioEndpoint>();
      IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
      IMMDeviceCollection devices = null;
      try {
        enumerator.EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, out devices);
        uint count;
        devices.GetCount(out count);
        for (uint i = 0; i < count; i++) {
          IMMDevice device = null;
          IPropertyStore props = null;
          try {
            devices.Item(i, out device);
            IntPtr idPtr;
            device.GetId(out idPtr);
            string id;
            try { id = Marshal.PtrToStringUni(idPtr); }
            finally { Marshal.FreeCoTaskMem(idPtr); }
            device.OpenPropertyStore(0, out props);   // STGM_READ
            string name = FriendlyName(props);
            if (id != null) list.Add(new AudioEndpoint { Id = id, Name = name ?? "" });
          } finally {
            Release(props);
            Release(device);
          }
        }
      } finally {
        Release(devices);
        Release(enumerator);
      }
      return list.ToArray();
    }

    public static bool GetEndpointMute(string endpointId) {
      IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
      IMMDevice device = null;
      IAudioEndpointVolume volume = null;
      try {
        enumerator.GetDevice(endpointId, out device);
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        device.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out volume);
        bool muted;
        volume.GetMute(out muted);
        return muted;
      } finally {
        Release(volume);
        Release(device);
        Release(enumerator);
      }
    }

    public static void SetEndpointMute(string endpointId, bool mute) {
      IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
      IMMDevice device = null;
      IAudioEndpointVolume volume = null;
      try {
        enumerator.GetDevice(endpointId, out device);
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        device.Activate(ref iid, CLSCTX_ALL, IntPtr.Zero, out volume);
        volume.SetMute(mute, IntPtr.Zero);
      } finally {
        Release(volume);
        Release(device);
        Release(enumerator);
      }
    }
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

# ---------------------------------------------------------------- dock + speakers
# Mutes the built-in Realtek speakers while the Z13 is docked (CalDigit TS3 Plus) and unmutes them on
# undock. Edge-triggered, and it only undoes a mute that it made (MutedByUs in the state file).
# Design and rationale: plans/dock-mute-speakers.md.
$script:lastDockFailure = $null

function Write-Z13DockFailure {
  # Logs a dock or speaker problem once. The same message on later ticks stays silent until it changes.
  param([string]$Message)
  if ($Message -ne $script:lastDockFailure) { Write-Z13Log $Message; $script:lastDockFailure = $Message }
}

function Get-Z13DockMatch {
  $cfg = Get-Z13Config
  $v = [string]$cfg.DockDeviceMatch
  if ([string]::IsNullOrWhiteSpace($v)) { 'TS3 Plus' } else { $v.Trim() }
}

function Get-Z13SpeakerMatch {
  $cfg = Get-Z13Config
  $v = [string]$cfg.SpeakerEndpointMatch
  if ([string]::IsNullOrWhiteSpace($v)) { 'Realtek' } else { $v.Trim() }
}

function Test-Z13Docked {
  <#  $true when a PRESENT PnP device whose name contains DockDeviceMatch exists, $false when none does.
      $null when the check itself failed: callers must treat that as unknown, never as undocked.  #>
  try { return [Z13Native.DevTree]::IsPresentNameContaining((Get-Z13DockMatch)) }
  catch { Write-Z13DockFailure "dock check failed: $($_.Exception.Message)"; return $null }
}

function Find-Z13SpeakerEndpoint {
  <#  The single active playback endpoint whose name contains SpeakerEndpointMatch, or $null.
      Zero or several matches return $null and are logged. Never mute a guess.  #>
  $match = Get-Z13SpeakerMatch
  try { $all = @([Z13Native.CoreAudio]::GetActiveRenderEndpoints()) }
  catch { Write-Z13DockFailure "endpoint list failed: $($_.Exception.Message)"; return $null }
  $hits = @($all | Where-Object { ([string]$_.Name).IndexOf($match, [StringComparison]::OrdinalIgnoreCase) -ge 0 })
  if ($hits.Count -eq 1) { return $hits[0] }
  if ($hits.Count -eq 0) {
    Write-Z13DockFailure ("no active playback endpoint matches '{0}' (active: {1})" -f $match, (@($all | ForEach-Object { $_.Name }) -join '; '))
  } else {
    Write-Z13DockFailure ("{0} active playback endpoints match '{1}'; not guessing" -f $hits.Count, $match)
  }
  return $null
}

function Get-Z13SpeakerMute {
  <#  $true or $false: mute state of the matching speakers endpoint.  $null: endpoint not found or ambiguous, or the read failed.  #>
  $ep = Find-Z13SpeakerEndpoint
  if ($null -eq $ep) { return $null }
  try { return [Z13Native.CoreAudio]::GetEndpointMute($ep.Id) }
  catch { Write-Z13DockFailure "speaker mute read failed: $($_.Exception.Message)"; return $null }
}

function Set-Z13SpeakerMute {
  <#  Sets the mute of the matching speakers endpoint. The volume level is not changed. Returns $true on success.  #>
  param([Parameter(Mandatory)][bool]$Muted)
  $ep = Find-Z13SpeakerEndpoint
  if ($null -eq $ep) { return $false }
  try {
    [Z13Native.CoreAudio]::SetEndpointMute($ep.Id, $Muted)
    Write-Z13Log ("speakers {0}: {1}" -f $(if ($Muted) { 'muted' } else { 'unmuted' }), $ep.Name)
    return $true
  }
  catch { Write-Z13DockFailure "speaker mute set failed: $($_.Exception.Message)"; return $false }
}

function Get-Z13DockStatePath { Join-Path $env:LOCALAPPDATA 'Z13Automate\dock-audio-state.json' }

function Read-Z13DockState {
  <#  { Docked; MutedByUs }. A missing or unreadable file gives Docked = $null (unknown) and MutedByUs = $false.  #>
  param([Parameter(Mandatory)][string]$Path)
  $state = [pscustomobject]@{ Docked = $null; MutedByUs = $false }
  if (-not (Test-Path -LiteralPath $Path)) { return $state }
  try {
    $j = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($j.Docked -is [bool]) { $state.Docked = $j.Docked }
    if ($j.MutedByUs -is [bool]) { $state.MutedByUs = $j.MutedByUs }
  }
  catch { Write-Z13DockFailure "dock state file unreadable, treating as unknown: $($_.Exception.Message)" }
  return $state
}

function Save-Z13DockState {
  <#  Writes the state file as UTF-8 without a BOM. Set-Content -Encoding UTF8 writes one on Windows PowerShell 5.1.  #>
  param([Parameter(Mandatory)][string]$Path, $Docked, [bool]$MutedByUs)
  try {
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $text = [ordered]@{ Docked = $Docked; MutedByUs = $MutedByUs } | ConvertTo-Json
    [System.IO.File]::WriteAllText($Path, $text, (New-Object System.Text.UTF8Encoding($false)))
    return $true
  }
  catch { Write-Z13DockFailure "dock state write failed: $($_.Exception.Message)"; return $false }
}

function New-Z13DockResult {
  param([string]$Action, $Docked)
  [pscustomobject]@{ Action = $Action; Docked = $Docked }
}

function Invoke-Z13DockAudio {
  <#  Edge-triggered speaker mute for dock and undock. Acts only when the dock state differs from the state file
      (or the file is new). On dock it mutes only if the speakers are not already muted. On undock it unmutes only
      if we muted them (MutedByUs). So a manual unmute while docked sticks.
      Returns [pscustomobject]@{ Action = 'none' | 'muted' | 'unmuted' | 'skipped:<reason>'; Docked = $true | $false | $null }.
      -StatePath and -DockedOverride are for tests only.  #>
  param([string]$StatePath, [object]$DockedOverride)
  if (-not $StatePath) { $StatePath = Get-Z13DockStatePath }
  $prev = Read-Z13DockState -Path $StatePath
  $cfg = Get-Z13Config
  $enabled = $true
  if ($null -ne $cfg.DockMuteSpeakers) { $enabled = [bool]$cfg.DockMuteSpeakers }

  if (-not $enabled) {
    if ($prev.MutedByUs) {
      if (-not (Set-Z13SpeakerMute -Muted $false)) { return (New-Z13DockResult 'skipped:unmute-failed' $null) }
      Save-Z13DockState -Path $StatePath -Docked $null -MutedByUs $false | Out-Null
      Write-Z13Log 'dock audio: feature off, unmuted the speakers we had muted'
      return (New-Z13DockResult 'unmuted' $null)
    }
    # Forget the last dock state, so turning the feature back on while docked counts as a new dock.
    if ($null -ne $prev.Docked) { Save-Z13DockState -Path $StatePath -Docked $null -MutedByUs $false | Out-Null }
    return (New-Z13DockResult 'skipped:disabled' $null)
  }

  if ($PSBoundParameters.ContainsKey('DockedOverride')) { $docked = [bool]$DockedOverride }
  else { $docked = Test-Z13Docked }
  if ($null -eq $docked) { return (New-Z13DockResult 'skipped:dock-unknown' $null) }

  # No edge: do nothing at all.
  if (($null -ne $prev.Docked) -and ($docked -eq $prev.Docked)) {
    $script:lastDockFailure = $null
    return (New-Z13DockResult 'none' $docked)
  }

  if ($docked) {
    $muted = Get-Z13SpeakerMute
    if ($null -eq $muted) { return (New-Z13DockResult 'skipped:endpoint' $true) }
    if ($muted) {
      Save-Z13DockState -Path $StatePath -Docked $true -MutedByUs $false | Out-Null
      Write-Z13Log 'dock audio: docked, speakers already muted (left as they are)'
      $script:lastDockFailure = $null
      return (New-Z13DockResult 'none' $true)
    }
    if (-not (Set-Z13SpeakerMute -Muted $true)) { return (New-Z13DockResult 'skipped:mute-failed' $true) }
    Save-Z13DockState -Path $StatePath -Docked $true -MutedByUs $true | Out-Null
    Write-Z13Log 'dock audio: docked, muted the speakers'
    $script:lastDockFailure = $null
    return (New-Z13DockResult 'muted' $true)
  }

  # Undocked edge.
  if ($prev.MutedByUs) {
    if (-not (Set-Z13SpeakerMute -Muted $false)) { return (New-Z13DockResult 'skipped:unmute-failed' $false) }
    Save-Z13DockState -Path $StatePath -Docked $false -MutedByUs $false | Out-Null
    Write-Z13Log 'dock audio: undocked, unmuted the speakers we had muted'
    $script:lastDockFailure = $null
    return (New-Z13DockResult 'unmuted' $false)
  }
  Save-Z13DockState -Path $StatePath -Docked $false -MutedByUs $false | Out-Null
  $script:lastDockFailure = $null
  return (New-Z13DockResult 'none' $false)
}

Export-ModuleMember -Function Write-Z13Log, Get-Z13Config, Set-Z13Config, Get-Z13ConfigPath, Get-Z13LogPath,
  Get-PowerSource, Get-BatteryPercent, Get-DisplayCount, Get-PrimaryDeviceName, Get-InternalDeviceName,
  Get-CurrentRefresh, Get-CurrentBrightness,
  Set-DisplayRefresh, Set-DisplayBrightness, Invoke-Z13Profile, Invoke-Z13AutoProfile,
  Test-Z13Docked, Get-Z13SpeakerMute, Set-Z13SpeakerMute, Invoke-Z13DockAudio
