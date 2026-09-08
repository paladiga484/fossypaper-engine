--!nonstrict
-- Bar widget: what is on screen right now, and a way to change it.
--
--   left click   open the library panel
--   right click  wallpaper off / back on
--
-- It polls `fossypaper --json status` rather than watching anything, because a
-- wallpaper changes about as often as you decide to change it. Ten seconds is
-- already generous.

local fossy = require("./fossy.luau")

local showTitle = noctalia.getConfig("show_title")
local maxLength = noctalia.getConfig("max_length") or 18

local title = ""
local running = false
local problem = ""

local function render()
  if problem ~= "" then
    barWidget.setGlyph("photo-off")
    barWidget.setText("")
    barWidget.setTooltip(problem)
    return
  end
  barWidget.setGlyph(if running then "photo" else "photo-off")
  if showTitle == true and title ~= "" then
    barWidget.setText(fossy.cut(title, maxLength))
  else
    barWidget.setText("")
  end
  barWidget.setTooltip(
    if running and title ~= "" then title .. "\nleft: library   right: turn off"
    elseif title ~= "" then title .. " (stopped)\nleft: library   right: turn on"
    else noctalia.tr("stopped") .. "\nleft: library"
  )
end

local function refresh()
  if not fossy.installed() then
    problem = noctalia.tr("missing") .. ": " .. fossy.bin()
    render()
    return
  end
  fossy.json({ "status" }, function(status, err)
    if status == nil then
      problem = err or "no reply"
    else
      problem = ""
      running = status.running == true
      title = status.title or status.current or ""
    end
    render()
  end)
end

function update()
  noctalia.setUpdateInterval(10000)
  refresh()
end

function onClick()
  noctalia.togglePanel("friend/fossypaper:library")
end

function onRightClick()
  fossy.plain({ "toggle" }, function(_ok, _msg)
    refresh()
  end)
end

function onIpc(event, _payload)
  if event == "refresh" then
    refresh()
  end
end
