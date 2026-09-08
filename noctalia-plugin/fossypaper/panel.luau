--!nonstrict
-- The library panel: everything you actually do with a wallpaper.
--
--   noctalia msg panel-toggle friend/fossypaper:library
--
-- One `fossypaper --json list` on open fills the whole surface; applying,
-- stopping and theming are one more call each. Nothing polls while the panel
-- is closed.

local fossy = require("./fossy.luau")

local wallpapers = {}
local filter = ""
local status = { running = false, current = "", title = "" }
local note = ""
local busy = false
local hovered = ""

local KIND_GLYPH = {
  scene = "cube",
  video = "movie",
  image = "photo",
  web = "world-off",
  application = "app-window-filled",
}

local function matches(w)
  if filter == "" then
    return true
  end
  local needle = string.lower(filter)
  return string.find(string.lower(w.title or ""), needle, 1, true) ~= nil
      or string.find(w.id or "", needle, 1, true) ~= nil
end

local function statusLine(): string
  if note ~= "" then
    return note
  end
  if status.title ~= "" and status.title ~= nil then
    return (if status.running then "playing: " else "stopped: ") .. status.title
  end
  return "nothing applied yet"
end

local function row(w)
  local usable = w.usable == true
  local isCurrent = w.id == status.current
  local lit = hovered == w.id or isCurrent
  local children = {}

  if noctalia.getConfig("show_thumbnails") == true and w.thumb ~= nil and w.thumb ~= "" then
    table.insert(children, ui.image({
      path = w.thumb, width = 72, height = 41, radius = 3, fit = "cover",
    }))
  else
    table.insert(children, ui.glyph({
      name = KIND_GLYPH[w.type] or "photo", size = 18,
      color = if usable then "on_surface_variant" else "error",
    }))
  end

  table.insert(children, ui.column({ gap = 2, flexGrow = 1 }, {
    ui.label({
      text = w.title,
      color = if isCurrent then "primary" elseif usable then "on_surface" else "on_surface_variant",
    }),
    ui.label({
      text = (if usable then w.type else (w.reason ~= "" and w.reason or ("needs " .. w.backend)))
             .. (if w.audio == true then "  ~ audio" else ""),
      fontSize = 11,
      color = if usable then "on_surface_variant" else "error",
    }),
  }))

  if isCurrent then
    table.insert(children, ui.glyph({ name = "check", size = 16, color = "primary" }))
  end

  return ui.row({
    key = w.id,
    gap = 10,
    align = "center",
    padding = 6,
    radius = 6,
    fill = if lit then "surface_variant/0.55" else "transparent",
    onHover = "onRowHover",
    onClick = function()
      if not usable then
        note = w.reason ~= "" and w.reason or ("this one needs " .. w.backend)
        render()
        return
      end
      busy = true
      note = "applying " .. w.title
      render()
      fossy.apply(w.id, function(ok, message)
        busy = false
        note = if ok then "" else message
        refresh()
      end)
    end,
  }, children)
end

function render()
  local rows = {}
  for _, w in wallpapers do
    if matches(w) then
      table.insert(rows, row(w))
    end
  end
  if #rows == 0 then
    table.insert(rows, ui.label({
      text = if #wallpapers == 0 then "no library found - subscribe in Steam, or run `fossypaper browse wallhaven`"
             else "nothing matches " .. filter,
      color = "on_surface_variant",
    }))
  end

  panel.render(ui.column({ flexGrow = 1, gap = 12 }, {
    ui.row({ align = "center", justify = "space_between", gap = 8 }, {
      ui.label({ text = "fossypaper", fontSize = 16, fontWeight = "bold",
                 color = "on_surface", flexGrow = 1 }),
      ui.button({ glyph = "refresh", tooltip = "rescan", onClick = "onRefresh" }),
      ui.button({ glyph = "close", onClick = "onClose" }),
    }),

    ui.row({ gap = 8, align = "center" }, {
      ui.label({ text = statusLine(), fontSize = 12,
                 color = if note ~= "" then "primary" else "on_surface_variant",
                 flexGrow = 1 }),
    }),

    ui.row({ gap = 8, align = "center" }, {
      ui.input({ key = "filter", placeholder = "filter the library",
                 controlSize = "sm", flexGrow = 1, onChange = "onFilter" }),
      ui.button({ text = "Off", controlSize = "sm", tooltip = "stop the wallpaper",
                  enabled = not busy, onClick = "onOff" }),
      ui.button({ text = "Theme", controlSize = "sm",
                  tooltip = "read a palette out of the current wallpaper",
                  enabled = not busy, onClick = "onTheme" }),
    }),

    ui.scroll({ flexGrow = 1, gap = 2, align = "stretch" }, rows),

    ui.row({ align = "center", justify = "space_between", gap = 8 }, {
      ui.label({ text = #wallpapers .. " wallpapers", fontSize = 11,
                 color = "on_surface_variant", flexGrow = 1 }),
      ui.button({ text = "Open fossypaper", controlSize = "sm", variant = "ghost",
                  onClick = "onOpenApp" }),
    }),
  }))
end

function refresh()
  fossy.json({ "status" }, function(s, _err)
    if s ~= nil then
      status = { running = s.running == true, current = s.current or "", title = s.title or "" }
    end
    render()
  end)
end

local function reload()
  if not fossy.installed() then
    note = noctalia.tr("missing") .. ": " .. fossy.bin()
    render()
    return
  end
  local args = { "list" }
  if noctalia.getConfig("show_thumbnails") == true then
    table.insert(args, "--thumbs")
  end
  fossy.json(args, function(list, err)
    if list == nil then
      note = err or "could not read the library"
    else
      wallpapers = list
      note = ""
    end
    refresh()
  end)
end

function onOpen(_context)
  render()
  reload()
end

function onRowHover(state, key)
  hovered = if state == "true" then key else ""
  render()
end

function onFilter(value)
  filter = fossy.trim(value)
  render()
end

function onRefresh()
  note = "rescanning"
  render()
  reload()
end

function onOff()
  busy = true
  render()
  fossy.plain({ "off" }, function(_ok, _msg)
    busy = false
    note = ""
    refresh()
  end)
end

function onTheme()
  if status.current == "" then
    note = "apply a wallpaper first"
    render()
    return
  end
  busy = true
  note = "reading the palette"
  render()
  fossy.plain({ "theme" }, function(ok, message)
    busy = false
    note = if ok then "palette written to ~/.local/state/fossypaper" else message
    render()
  end)
end

function onOpenApp()
  noctalia.runAsync({ fossy.bin(), "gui" })
  panel.close()
end

function onClose()
  panel.close()
end
