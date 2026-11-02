// Swipe up from the bottom screen edge to show KWin's virtual keyboard (KWin.ElectricBottom is the bottom edge).
if (typeof registerTouchScreenEdge !== "function") {
    print("z13swipekeyboard: registerTouchScreenEdge is not available to KWin scripts");
} else {
    registerTouchScreenEdge(KWin.ElectricBottom, function () {
        print("z13swipekeyboard: bottom edge swipe, showing the keyboard");
        callDBus("org.kde.KWin", "/VirtualKeyboard", "org.kde.kwin.VirtualKeyboard", "forceActivate");
    });
    print("z13swipekeyboard: bottom edge touch swipe registered");
}
