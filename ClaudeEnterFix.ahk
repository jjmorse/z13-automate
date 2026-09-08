#Requires AutoHotkey v2.0
#SingleInstance Force

; Auto-clear a stuck "always on top" on the Claude window. Something (most likely
; an app bug) intermittently pins the Claude window topmost with no PowerToys or
; other window tool involved. Poll once a second and un-pin it whenever it happens.
SetTimer(ClearClaudeTopmost, 1000)

; Work around the Claude Code desktop "Enter = newline" bug (triggered by Windows
; tablet/slate posture). When the Claude app is focused, remap plain Enter to
; Ctrl+Enter, which always submits. Shift+Enter (and any modified Enter) passes
; through untouched, so deliberate newlines still work.
#HotIf WinActive("ahk_exe claude.exe")
Enter::Send "^{Enter}"
#HotIf

ClearClaudeTopmost() {
    for hwnd in WinGetList("ahk_exe claude.exe") {
        try {
            if (WinGetExStyle("ahk_id " hwnd) & 0x8)   ; WS_EX_TOPMOST
                WinSetAlwaysOnTop(false, "ahk_id " hwnd)
        }
    }
}
