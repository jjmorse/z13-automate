# Reset-Bluetooth.ps1
#
# Cycles the Bluetooth adapter (disable -> re-enable) to recover a dropped BLE
# device - notably the Surface Slim Pen, whose BLE link goes idle after charging
# and won't reconnect on its own on this MediaTek BT stack. Restarting the stack
# is the thing that reliably brings it back.
#
# Double-click the desktop shortcut; this self-elevates (one UAC prompt).

# --- self-elevate ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$ErrorActionPreference = 'Stop'

$bt = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue |
      Where-Object { $_.FriendlyName -match 'MediaTek Bluetooth Adapter' } |
      Select-Object -First 1

if (-not $bt) {
    Write-Host "Bluetooth adapter not found." -ForegroundColor Red
    Start-Sleep -Seconds 4
    exit 1
}

Write-Host "Cycling $($bt.FriendlyName) ..." -ForegroundColor Cyan
Disable-PnpDevice -InstanceId $bt.InstanceId -Confirm:$false
Start-Sleep -Seconds 3
Enable-PnpDevice  -InstanceId $bt.InstanceId -Confirm:$false
Start-Sleep -Seconds 3

Write-Host "Bluetooth reset complete." -ForegroundColor Green
Write-Host "If the pen doesn't reconnect in a few seconds, click its top button once." -ForegroundColor Green
Start-Sleep -Seconds 2
