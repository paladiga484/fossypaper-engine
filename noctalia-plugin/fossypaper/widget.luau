--!nonstrict
-- Bar widget: what is on screen right now, and a way to change it.
--
--   left click   open the library panel
--   right click  wallpaper off / back on
--
-- A bar widget lives forever, so its idle cost is the only cost that matters.
-- Two things are shown, and they are found two different ways:
--
--   * whether a wallpaper is playing -- `noctalia.processMatches`, which the
--     host answers natively;
--   * which one -- that only changes when fossypaper writes its config, so the
--     mtime of that file gates the one call that spawns a process.
--
-- The result is a tick that usually spawns nothing at all.

local fossy = require("./fossy.luau")

local showTitle = noctalia.getConfig("show_title")
local maxLength = noctalia.getConfig("max_length") or 18

local CONFIG = noctalia.expandPath("~/.config/fossypaper/config.json")

local title = ""
local running = false
local problem = ""
local seenMtime = -1

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

-- The cheap half: no subprocess, the host already tracks this.
local function pollRunning()
  noctalia.processMatches(function(matched)
    running = matched
    render()
  end, "linux-wallpaper", "mpvpaper")
end

-- The expensive half: one `fossypaper --json status`, only when the config
-- fossypaper writes on every apply has actually moved.
local function pollTitle(force: boolean?)
  local info = noctalia.fileInfo(CONFIG)
  local mtime = if info ~= nil then info.mtime else 0
  if force ~= true and mtime == seenMtime then
    return
  end
  seenMtime = mtime
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
      title = status.title or status.current or ""
    end
    render()
  end)
end

function update()
  noctalia.setUpdateInterval(15000)
  pollRunning()
  pollTitle()
end

function onClick()
  noctalia.togglePanel("friend/fossypaper:library")
end

function onRightClick()
  fossy.plain({ "toggle" }, function(_ok, _msg)
    pollRunning()
    pollTitle(true)
  end)
end

function onIpc(event, _payload)
  if event == "refresh" then
    pollRunning()
    pollTitle(true)
  end
end
