-- Personal Hyprland input overrides. This file layers on Omarchy's defaults
-- in /usr/share/omarchy/default/hypr/input.lua. List only settings that
-- differ, each with a reason. Keyboard layout, repeat, and compose stay
-- with Omarchy.

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
