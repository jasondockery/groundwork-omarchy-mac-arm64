-- Personal Hyprland input overrides. This file layers on Omarchy's defaults
-- in /usr/share/omarchy/default/hypr/input.lua. List only settings that
-- differ, each with a reason. Keyboard layout, repeat, and compose stay
-- with Omarchy. Gesture replacements are below; other gestures stay in
-- /usr/share/omarchy/default/hypr/gestures.lua.

hl.config({
  input = {
    -- Magic Trackpad on a large external display feels slow at Omarchy's 0.
    -- 0.35 is Omarchy's own suggested bump in its stock user template.
    sensitivity = 0.35,
    touchpad = {
      -- Apple trackpads expect tap-to-click; Omarchy leaves it off.
      tap_to_click = true,
      -- Three-finger drag matches the macOS window-move habit.
      drag_3fg = 1,
      -- Page scrolling in browsers felt too fast at Omarchy's 0.4.
      scroll_factor = 0.2,
    },
  },
})

-- Three-finger left and right switch workspaces in Omarchy and fight
-- three-finger drag, including text selection. Unset them, and replace
-- four-finger move-window swipes with focus of the previous and next
-- workspace. Unset first: Hyprland rejects a second gesture on the same
-- fingers and direction. Three-finger up and down, and four-finger down,
-- stay Omarchy's.
hl.gesture({ fingers = 3, direction = "left", action = "unset" })
hl.gesture({ fingers = 3, direction = "right", action = "unset" })
hl.gesture({ fingers = 4, direction = "left", action = "unset" })
hl.gesture({ fingers = 4, direction = "right", action = "unset" })
hl.gesture({ fingers = 4, direction = "left", action = function() hl.dispatch(hl.dsp.focus({ workspace = "r-1" })) end })
hl.gesture({ fingers = 4, direction = "right", action = function() hl.dispatch(hl.dsp.focus({ workspace = "r+1" })) end })
