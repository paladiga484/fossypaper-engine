// fossypaper's Plasma preflight: load one scene with the same SceneViewer the
// Plasma wallpaper uses, inside a throwaway nested compositor, and quit.
// A scene that never finishes loading here would wedge plasmashell itself —
// its renderer deadlocks on teardown — so the caller watches for a hang.
// Arguments (last three): props JSON, scene.pkg URL, assets URL.
import QtQuick
import QtQuick.Window
import com.github.catsout.wallpaperEngineKde 1.2

Window {
    width: 640; height: 360; visible: true; color: "black"
    readonly property var a: Qt.application.arguments
    SceneViewer {
        anchors.fill: parent
        userProperties: a[a.length - 3]
        source: a[a.length - 2]
        assets: a[a.length - 1]
        fps: 30; muted: true
        cachePasses: false      // as the wallpaper runs any scene with effects
        Component.onCompleted: play()
    }
    Timer { interval: 6000; running: true; onTriggered: Qt.quit() }
}
