#pragma once

// Per-board layout and capability config.  Selected via PlatformIO's board
// macro (ARDUINO_M5STACK_CORE2 / ARDUINO_M5STICK_C_PLUS).  All hardware-
// specific constants live here so main.cpp / buddy.cpp / character.cpp
// stay board-agnostic.

#if defined(BOARD_CORE2)
  // ─── M5Stack Core2 (320×240 capacitive touch, AXP192, MPU6886, BM8563)
  #define BOARD_NAME            "M5Stack Core2"

  // Screen — we run portrait, rotation 0 → 240 wide, 320 tall sprite.
  #define SCREEN_W              240
  #define SCREEN_H              320

  // ASCII pet geometry.
  #define BUDDY_SCALE_NORMAL    3
  #define BUDDY_SCALE_PEEK      2
  #define BUDDY_BASE_Y          12
  #define BUDDY_X_CENTER_VAL    120
  #define BUDDY_CANVAS_W_VAL    240

  // HUD/transcript.
  #define HUD_SIZE              2
  #define HUD_AREA              52
  #define HUD_LINE_H            16
  #define HUD_WIDTH_COLS        19

  // Approval popup.
  #define APPROVAL_AREA         130
  #define APPROVAL_HEAD_SZ      2
  #define APPROVAL_HINT_SZ      2
  #define APPROVAL_HINT_COLS    19

  // INFO page typography. Headers and the battery % stay big for visual
  // hierarchy; body lines stay at size 1 so the dense pages (DEVICE,
  // BUTTONS) still fit on one screen with breathing room. INFO_TOP=110
  // leaves room for the scale-2 peek pet above it.
  #define INFO_HEAD_SZ          2
  #define INFO_BODY_SZ          1
  #define INFO_TOP              110
  #define INFO_LINE_H           10
  #define INFO_BAT_PCT_SZ       3

  // Clock face vertical layout.
  #define CLOCK_TOP_CLEAR       140
  #define CLOCK_HM_Y            170
  #define CLOCK_SS_Y            210
  #define CLOCK_DT_Y            240
  #define CLOCK_TEMP_Y          270

  // PET page typography.  Everything at size 2 for visual consistency:
  // labels (mood/fed/energy/MOOD/FED/...) and counters (approved/...)
  // share one font size.  PET_TOP=95 because the header is now drawn
  // beside the peek pet (PET_HEADER_INLINE=1), not above the stats.
  #define PET_HEADER_SZ         1     // small inline header beside pet
  #define PET_HEADER_INLINE     1     // 1=draw header right of peek pet
  #define PEEK_X_OFFSET         (-40) // shift peek pet left to free right side
  #define PEEK_CLEAR_W          160   // buddyTick fillRect width in peek mode
  #define PET_STAT_BODY_SZ      2
  #define PET_STAT_LINE_H       16
  #define PET_TOP               95
  #define PET_LABEL_SZ          2
  #define PET_VISUAL_TOP_OFFSET 24
  #define PET_ROW_STEP          26
  #define PET_HEART_R           3
  #define PET_DOT_R             3
  #define PET_BAR_W             10
  #define PET_BAR_H             6

  // LED — Core2 has no GPIO indicator LED; use the AXP-driven green
  // side LED via M5.Power.setLed().
  #define LED_USES_POWER_API    1

#elif defined(BOARD_STICKC_PLUS)
  // ─── M5StickC Plus (135×240 portrait, AXP192, MPU6886, BM8563)
  #define BOARD_NAME            "M5StickC Plus"

  #define SCREEN_W              135
  #define SCREEN_H              240

  #define BUDDY_SCALE_NORMAL    2
  #define BUDDY_SCALE_PEEK      1
  #define BUDDY_BASE_Y          30
  #define BUDDY_X_CENTER_VAL    67
  #define BUDDY_CANVAS_W_VAL    135

  #define HUD_SIZE              1
  #define HUD_AREA              28
  #define HUD_LINE_H            8
  #define HUD_WIDTH_COLS        21

  #define APPROVAL_AREA         78
  #define APPROVAL_HEAD_SZ      1
  #define APPROVAL_HINT_SZ      1
  #define APPROVAL_HINT_COLS    21

  #define INFO_HEAD_SZ          1
  #define INFO_BODY_SZ          1
  #define INFO_TOP              70
  #define INFO_LINE_H           8
  #define INFO_BAT_PCT_SZ       2

  #define CLOCK_TOP_CLEAR       90
  #define CLOCK_HM_Y            140
  #define CLOCK_SS_Y            175
  #define CLOCK_DT_Y            200
  #define CLOCK_TEMP_Y          220

  #define PET_HEADER_SZ         1
  #define PET_HEADER_INLINE     0     // Plus has too narrow a screen
  #define PEEK_X_OFFSET         0
  #define PEEK_CLEAR_W          0     // unused when offset==0
  #define PET_STAT_BODY_SZ      1
  #define PET_STAT_LINE_H       10
  #define PET_TOP               70
  #define PET_LABEL_SZ          1
  #define PET_VISUAL_TOP_OFFSET 16
  #define PET_ROW_STEP          20
  #define PET_HEART_R           2
  #define PET_DOT_R             2
  #define PET_BAR_W             9
  #define PET_BAR_H             6

  // StickC Plus has a red GPIO10 LED, active-low.
  #define LED_USES_POWER_API    0
  #define LED_GPIO              10

#else
  #error "Define BOARD_CORE2 or BOARD_STICKC_PLUS via platformio.ini build_flags"
#endif
