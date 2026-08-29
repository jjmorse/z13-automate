#Requires AutoHotkey v2.0
#SingleInstance Force
; Work around the Claude Code desktop "Enter = newline" bug (triggered by Windows
; tablet/slate posture). When the Claude app is focused, remap plain Enter to
; Ctrl+Enter, which always submits. Shift+Enter (and any modified Enter) passes
; through untouched, so deliberate newlines still work.
#HotIf WinActive("ahk_exe claude.exe")
Enter::Send "^{Enter}"
#HotIf
