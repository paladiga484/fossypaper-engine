/*
    fossypaper's Plasma wallpaper. Plasma owns the desktop background, so a
    layer-shell renderer can only ever sit on top of it (covering icons and
    widgets) or be hidden under it. Registering as a real wallpaper type is the
    only way to be *the* wallpaper here.

    Everything it shows comes from its own config group, which `fossypaper`
    writes over plasmashell's scripting interface. No sockets, no servers, no
    helper processes.

      image  -> Image
      video  -> QtMultimedia
      scene  -> Scene.qml, when catsout's native scene renderer is installed;
                otherwise the still frame fossypaper rendered for it
*/
import QtQuick
import QtMultimedia
import org.kde.plasma.plasmoid
import org.kde.taskmanager as TaskManager

WallpaperItem {
    id: root

    readonly property var cfg: root.configuration
    readonly property string kind: cfg.Kind
    // fossypaper writes these as ready-made, percent-encoded file:// URLs
    readonly property string sourceUrl: cfg.Source
    readonly property string stillUrl: cfg.Still
    readonly property int fill: cfg.Fit === "fit" ? Image.PreserveAspectFit
                              : cfg.Fit === "stretch" ? Image.Stretch
                              : Image.PreserveAspectCrop
    // a game (or anything) fullscreen on this screen stops the animation
    readonly property bool paused: cfg.FullscreenPause && blocking > 0
    property int blocking: 0
    function recount() {
        let n = 0;
        for (let i = 0; i < watchers.count; ++i) {
            const w = watchers.objectAt(i);
            if (w && w.blocks) ++n;
        }
        blocking = n;
    }

    onPausedChanged: console.log("fossypaper:", paused ? "paused" : "playing")

    Rectangle { anchors.fill: parent; color: "black" }

    // -- still: images, and scenes/videos that can't animate here ----------- //
    Image {
        id: still
        anchors.fill: parent
        visible: root.kind === "image" || (root.kind === "scene" && scene.status !== Loader.Ready)
        source: root.kind === "image" ? root.sourceUrl : root.stillUrl
        fillMode: root.fill
        asynchronous: true
        cache: false
        smooth: true
    }

    // -- video --------------------------------------------------------------- //
    Loader {
        anchors.fill: parent
        active: root.kind === "video" && root.sourceUrl !== ""
        sourceComponent: Item {
            VideoOutput {
                id: out
                anchors.fill: parent
                fillMode: root.cfg.Fit === "fit" ? VideoOutput.PreserveAspectFit
                        : root.cfg.Fit === "stretch" ? VideoOutput.Stretch
                        : VideoOutput.PreserveAspectCrop
            }
            AudioOutput {
                id: audio
                muted: root.cfg.Muted
                volume: root.cfg.Volume / 100.0
            }
            MediaPlayer {
                id: player
                source: root.sourceUrl
                loops: MediaPlayer.Infinite
                videoOutput: out
                audioOutput: audio
                Component.onCompleted: if (!root.paused) play()
            }
            Connections {
                target: root
                function onPausedChanged() { root.paused ? player.pause() : player.play() }
            }
        }
    }

    // -- scene --------------------------------------------------------------- //
    // Loaded through a Loader on purpose: if the native module isn't
    // installed the import fails, the Loader lands in Error, and the still
    // above stays up instead of the whole wallpaper failing to load.
    Loader {
        id: scene
        anchors.fill: parent
        active: root.kind === "scene" && root.sourceUrl !== "" && root.cfg.Assets !== ""
        source: active ? "Scene.qml" : ""
        onStatusChanged: if (status === Loader.Error)
            console.warn("fossypaper: no native scene renderer; showing the still frame")
        onLoaded: {
            item.source = Qt.binding(() => root.sourceUrl);
            item.assets = Qt.binding(() => root.cfg.Assets);
            item.fps = Qt.binding(() => root.cfg.Fps);
            item.muted = Qt.binding(() => root.cfg.Muted);
            item.volume = Qt.binding(() => root.cfg.Volume / 100.0);
            item.fillMode = Qt.binding(() => root.cfg.Fit);
            item.paused = Qt.binding(() => root.paused);
            item.props = Qt.binding(() => root.cfg.Props || "{}");
        }
    }

    // -- what counts as "something is covering me" --------------------------- //
    TaskManager.VirtualDesktopInfo { id: desktops }
    TaskManager.ActivityInfo { id: activities }
    TaskManager.TasksModel {
        id: tasks
        groupMode: TaskManager.TasksModel.GroupDisabled
        sortMode: TaskManager.TasksModel.SortDisabled
        screenGeometry: root.parent ? root.parent.screenGeometry : Qt.rect(0, 0, 0, 0)
        filterByScreen: true
        virtualDesktop: desktops.currentDesktop
        filterByVirtualDesktop: true
        activity: activities.currentActivity
        filterByActivity: true
    }
    Instantiator {
        id: watchers
        model: tasks
        // task-manager roles are capitalised, so they can't be required
        // properties; read them off `model` instead
        delegate: QtObject {
            readonly property bool blocks: model.IsWindow === true && model.IsFullScreen === true
                                           && model.IsMinimized !== true
                                           && (!root.cfg.PauseOnlyActive || model.IsActive === true)
            onBlocksChanged: Qt.callLater(root.recount)
        }
        onObjectAdded: Qt.callLater(root.recount)
        onObjectRemoved: Qt.callLater(root.recount)
    }
}
