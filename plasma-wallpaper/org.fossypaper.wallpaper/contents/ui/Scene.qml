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
    property bool mouse: true
    // off unless fossypaper says this scene has no effect chain: the
    // renderer's static-pass cache feeds effects their own previous frame
    property alias cachePasses: player.cachePasses

    onMouseChanged: { player.setAcceptMouse(mouse); player.setAcceptHover(mouse); }

    onPausedChanged: paused ? player.pause() : player.play()
    onFillModeChanged: player.fillMode = fillMode === "fit" ? SceneViewer.ASPECTFIT
                                       : fillMode === "stretch" ? SceneViewer.STRETCH
                                       : SceneViewer.ASPECTCROP

    SceneViewer {
        id: player
        anchors.fill: parent
        speed: 1.0
        cachePasses: false
        Component.onCompleted: {
            setAcceptMouse(parent.mouse);
            setAcceptHover(parent.mouse);
            parent.fillModeChanged();
            if (!parent.paused) play();
        }
    }
}
