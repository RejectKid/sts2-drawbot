# STS2 Drawbot

STS2 Drawbot turns a text prompt or image into right-click drawing strokes for the Slay the Spire 2 map drawing surface.

It is built for quick in-game doodles: preview first, then draw only when you add `--draw`.

## Features

- Simple prompt interface: `--prompt "simple star" --draw`
- Built-in vector doodles for common shapes: star, heart, circle, triangle, square, arrow, smiley, poop
- Image search through Openverse, DuckDuckGo Images, optional Google Custom Search, and Wikimedia Commons
- Sketch-style extraction for shaded drawings and grayscale illustrations
- Longer intentional strokes for cleaner in-game output
- Mouse-anchor placement: the drawing starts where your cursor is after the countdown
- Safe scaling/clamping to keep drawings inside the Slay the Spire 2 map-paper area
- Emergency stop with `Esc` or the PyAutoGUI top-left mouse failsafe

## Requirements

- Windows
- Python 3.11 or newer
- Slay the Spire 2 running in a normal window

## Install

```powershell
cd E:\projects\sts2-drawbot
.\install.ps1
```

## Preview

```powershell
.\run.ps1 --prompt "simple poop emoji"
```

This writes a preview image to `previews\` and does not move the mouse.

## GUI

For candidate browsing and easier previewing, launch the desktop UI:

```powershell
.\run-gui.ps1
```

The GUI lets you:

- search from a prompt
- inspect downloaded candidates
- compare the source image and sketch preview side by side
- open a local image
- choose trace mode
- draw the selected preview into Slay the Spire 2

## Draw

Open the Slay the Spire 2 map drawing UI first, then run:

```powershell
.\run.ps1 --prompt "simple poop emoji" --draw
```

When the countdown starts, move your mouse to where the first line should begin. The tool scales the full drawing into the safe map-paper area; if the mouse anchor would push part of the drawing off the paper, it clamps the drawing back inside the safe area.

Press `Esc` to stop while drawing. Moving the mouse to the top-left corner of the screen also aborts.

## Examples

```powershell
.\run.ps1 --prompt "simple star" --draw
.\run.ps1 --prompt "anime sailor moon line art" --draw
.\run.ps1 --prompt "sketch portrait of a wizard" --draw
```

Force web image search instead of built-in prompt handling:

```powershell
.\run.ps1 --prompt "anime sailor moon line art" --draw --no-builtins
```

Use a local image and force the sketch extractor:

```powershell
.\run.ps1 --image "C:\path\to\drawing.png" --mode sketch --draw
```

## Advanced Options

Most tuning is hidden from normal help because the defaults are tuned for Slay the Spire 2:

```powershell
.\run.ps1 --advanced-help
```

Useful advanced options:

- `--area X,Y,W,H`: manually set the safe drawing rectangle
- `--fit-padding N`: keep more or less space from the safe-area edges
- `--abort-key f9`: change the stop hotkey
- `--center-in-window`: center the drawing instead of starting at the mouse
- `--mode sketch`: force sketch extraction for shaded drawings
- `--scale N` and `--max-strokes N`: tune detail and runtime

## Search Providers

The default prompt flow uses public image sources that can be queried without setup. Google Images is supported through the official Custom Search API if these environment variables are set:

```powershell
$env:GOOGLE_API_KEY = "..."
$env:GOOGLE_CSE_ID = "..."
```

Without those keys, Openverse, DuckDuckGo Images, and Wikimedia Commons are still used.

## Notes

This is mouse automation for a game UI. Keep the game focused while drawing, and keep your hand near `Esc` until you trust the selected preview. Very dense source images may still need a simpler prompt, a cleaner line-art source, or lower `--max-strokes`.
