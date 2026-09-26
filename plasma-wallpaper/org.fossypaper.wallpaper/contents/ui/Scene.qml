// The one file that touches catsout's native scene renderer. It imports the
// compiled SceneViewer type only — not the rest of that project's wallpaper
// plugin, whose Pyext helper runs an unauthenticated WebSocket server.
import QtQuick
import com.github.catsout.wallpaperEngineKde 1.2

Item {
    property alias source: player.source
    property alias assets: player.assets
    property alias fps: player.fps
    property alias muted: player.muted
    property alias volume: player.volume
    property string fillMode: "fill"
    property alias props: player.userProperties
    property bool paused: false

    onPausedChanged: paused ? player.pause() : player.play()
    onFillModeChanged: player.fillMode = fillMode === "fit" ? SceneViewer.ASPECTFIT
                                       : fillMode === "stretch" ? SceneViewer.STRETCH
                                       : SceneViewer.ASPECTCROP

    SceneViewer {
        id: player
        anchors.fill: parent
        speed: 1.0
        Component.onCompleted: {
            setAcceptMouse(true);
            setAcceptHover(true);
            parent.fillModeChanged();
            if (!parent.paused) play();
        }
    }
}
