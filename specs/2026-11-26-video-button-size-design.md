# The video download button: its size, and hiding it

Asked 2026-11-26. Extension 1.9.8 (`chrome_ext/content.js`, `background.js`).

## Why

- On a thumbnail the button ("⬇ Download", about 118 x 26 px) covers most of
  the picture.
- Thumbnails that play a preview on hover stop it when the pointer reaches the
  button. The button is a layer of its own, so the site sees the pointer leave.
- Dropping the top-right "Download Media" button on the hide target hid only
  that button. The buttons on the videos stayed.

## What it does

- **Size by the box.** A video box at least 360 x 200 px gets the full button,
  the same as today, which is the largest it gets. A smaller box gets a round
  ⬇ with no label and no quality arrow (a click sends the best quality, as the
  main button does). It is 24 to 30 px, 15% of the box's shorter side, and sits
  6 px from the corner instead of 10 px.
- **Previews.** The user's call: a small button is enough. It covers a
  corner, not the picture, so moving across a thumbnail no longer crosses it.
  Pointing at the button itself still ends a site's preview. That is the
  moment of the click, so it is accepted.
- **Hide all.** The drop hides every video button in the tab until the page is
  reloaded, as the panel already was. That includes buttons in embedded
  player frames.
  - The page tells the worker (`HYPERFETCH_HIDE_BADGES`), and the worker passes
    it on to every frame.
  - A frame that loads later (a "Server 2" player) asks the page before it
    shows its first button (`HYPERFETCH_BADGES_HIDDEN`, sent through the worker
    to frame 0).
  - Nothing is saved, so a reload brings them all back.

## Approaches

- A: let the pointer pass through the button, so previews never stop. This
  needs hit-testing by position and a hover effect drawn by hand. Not taken:
  the user preferred the smaller button.
- B: put the button inside the thumbnail's own element. Not taken: the site's
  layout can clip it or remove it.
- C (taken): a smaller button on small boxes.

## Not done

- A big player whose controls fade when the pointer is over the button: the
  full button is the same as before.
