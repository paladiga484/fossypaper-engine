--!nonstrict
-- Control-center tile: wallpaper on or off, and nothing else.

local fossy = require("./fossy.luau")

local running = false

local function render()
  shortcut.setLabel(if running then "Wallpaper on" else "Wallpaper off")
  shortcut.setIcon("photo", "photo-off")
  shortcut.setActive(running)
  shortcut.setEnabled(fossy.installed())
end

local function refresh()
  fossy.json({ "status" }, function(status, _err)
    running = status ~= nil and status.running == true
    render()
  end)
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
