--!nonstrict
-- Shared plumbing for every fossypaper entry.
--
-- One rule holds this plugin together: the shell never reaches into the
-- wallpaper manager's internals, and the wallpaper manager never opens a port
-- for the shell. Everything crosses the boundary as `fossypaper --json <cmd>`
-- on argv, run off-thread, answered on stdout. That is the whole IPC layer.

local M = {}

function M.bin(): string
  local b = noctalia.getConfig("binary")
  if type(b) ~= "string" or b == "" then
    return "fossypaper"
  end
  return b
end

-- Run a fossypaper subcommand and hand back parsed JSON.
-- cb(value, err) -- value is nil when anything went wrong.
function M.json(args: { string }, cb)
  local argv = { M.bin(), "--json" }
  for _, a in args do
    table.insert(argv, a)
  end
  local started = noctalia.runAsync(argv, function(result)
    if result.exitCode ~= 0 and result.stdout == "" then
      cb(nil, M.trim(result.stderr) ~= "" and M.trim(result.stderr) or "fossypaper failed")
      return
    end
    local value, err = noctalia.json.decode(result.stdout)
    if value == nil then
      cb(nil, "unreadable reply: " .. tostring(err))
      return
    end
    cb(value, nil)
  end)
  if not started then
    cb(nil, "could not run " .. M.bin())
  end
end

-- Fire and forget, with the plain-text reply as the result.
function M.plain(args: { string }, cb)
  local argv = { M.bin() }
  for _, a in args do
    table.insert(argv, a)
  end
  if not noctalia.runAsync(argv, function(result)
    if cb then
      cb(result.exitCode == 0, M.trim(result.stdout) .. M.trim(result.stderr))
    end
  end) then
    if cb then cb(false, "could not run " .. M.bin()) end
  end
end

function M.trim(s: string?): string
  if type(s) ~= "string" then
    return ""
  end
  return noctalia.string.trim(s)
end

function M.installed(): boolean
  return noctalia.commandExists(M.bin())
end

function M.cut(s: string, n: number): string
  if utf8.len(s) == nil or (utf8.len(s) :: number) <= n then
    return s
  end
  return string.sub(s, 1, (utf8.offset(s, n) or n) - 1) .. "..."
end

-- Applying a wallpaper is three decisions, not one: hand it to fossypaper,
-- optionally re-read the palette, and get Noctalia's own wallpaper surface out
-- of the way so the two are not stacked on the same output.
function M.apply(id: string, cb)
  M.plain({ "apply", id }, function(ok, message)
    if ok then
      M.release()
      if noctalia.getConfig("sync_theme") == true then
        M.theme(nil)
      end
    end
    if cb then cb(ok, message) end
  end)
end

function M.release()
  if noctalia.getConfig("release_noctalia_wallpaper") ~= true then
    return
  end
  for _, output in noctalia.outputs() do
    local name = type(output) == "table" and output.name or output
    if type(name) == "string" and name ~= "" then
      noctalia.setWallpaperEnabled(name, false)
    end
  end
end

local FRAME = "~/.local/state/fossypaper/current-frame.png"

-- `fossypaper theme` writes that still as the source it extracted fossypaper's
-- own palette from. `setWallpaper` after `setWallpaperEnabled(..., false)`
-- registers an image with Noctalia's own wallpaper record without Noctalia
-- painting it anywhere — the output stays externally managed, so
-- Wallpaper::syncInstances() skips it — but it does become what
-- getPaletteWallpaperPath() returns, which is what Noctalia's own "Wallpaper"
-- colour-scheme source reads from. Off by default: it only does anything once
-- that source is actually selected (Settings -> Appearance -> Colour scheme,
-- or `noctalia msg color-scheme-set wallpaper <scheme>`).
function M.pushFrameToNoctalia()
  if noctalia.getConfig("sync_noctalia_wallpaper") ~= true then
    return
  end
  local frame = noctalia.expandPath(FRAME)
  if noctalia.fileExists(frame) then
    noctalia.setWallpaper(frame)
  end
end

-- `fossypaper theme`: render a still, extract fossypaper's own palette, and
-- optionally hand that same still to Noctalia's own palette source.
function M.theme(cb)
  M.plain({ "theme" }, function(ok, message)
    if ok then
      M.pushFrameToNoctalia()
    end
    if cb then cb(ok, message) end
  end)
end

return M
