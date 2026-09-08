--!nonstrict
-- Control-center tile: wallpaper on or off, and nothing else.
--
-- The running state comes from the host's own process check rather than a
-- subprocess, so the tile costs nothing to display.

local fossy = require("./fossy.luau")

local running = false

local function render()
  shortcut.setLabel(if running then "Wallpaper on" else "Wallpaper off")
  shortcut.setIcon("photo", "photo-off")
  shortcut.setActive(running)
  shortcut.setEnabled(fossy.installed())
end

local function refresh()
  noctalia.processMatches(function(matched)
    running = matched
    render()
  end, "linux-wallpaper", "mpvpaper")
end

render()
refresh()

function onClick()
  fossy.plain({ "toggle" }, function(_ok, _msg)
    refresh()
  end)
end

function onRightClick()
  noctalia.togglePanel("friend/fossypaper:library")
end
