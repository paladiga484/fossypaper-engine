--!nonstrict
-- Launcher provider: type `/wp` and start typing a wallpaper's name.
--
-- The list is fetched once per query burst and cached for a few seconds, so
-- holding down a key does not spawn a process per keystroke.

local fossy = require("./fossy.luau")

local cache = {}
local cachedAt = 0
local pending = nil
local CACHE_MS = 5000

local function rows(query: string)
  local out = {}
  local needle = string.lower(fossy.trim(query))
  for _, w in cache do
    local title = w.title or w.id
    -- Noctalia's own fuzzy matcher, so ranking here feels like ranking anywhere
    -- else in the launcher. nil means "no match at all".
    local score = if needle == "" then 0 else noctalia.fuzzyScore(needle, title)
    if score ~= nil then
      table.insert(out, {
        id = w.id,
        title = title,
        subtitle = (if w.usable == true then w.type else "cannot render here - " ..
                    (w.reason ~= "" and w.reason or ("needs " .. w.backend))),
        glyph = if w.type == "video" then "movie" else "photo",
        score = score,
      })
    end
  end
  if #out == 0 then
    out[1] = { id = "", title = "no wallpaper matches", subtitle = "fossypaper", glyph = "mood-empty" }
  end
  return out
end

function onQuery(query)
  if #cache > 0 and (noctalia.nowMs() - cachedAt) < CACHE_MS then
    launcher.setResults(query, rows(query))
    return
  end
  pending = query
  launcher.setResults(query, {
    { id = "", title = "reading your library...", subtitle = "fossypaper", glyph = "loader" },
  })
  fossy.json({ "list" }, function(list, err)
    if list == nil then
      launcher.setResults(pending or query, {
        { id = "", title = err or "fossypaper is not answering", subtitle = "fossypaper",
          glyph = "alert-triangle" },
      })
      return
    end
    cache = list
    cachedAt = noctalia.nowMs()
    launcher.setResults(pending or query, rows(pending or query))
  end)
end

function onActivate(id)
  if id == "" then
    return
  end
  fossy.apply(id, function(ok, message)
    if not ok then
      noctalia.notifyError(noctalia.tr("title"), message)
    end
  end)
end
