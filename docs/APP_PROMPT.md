# Prompt to build Clean Sweep

Build a mobile-first single-page web app called **Clean Sweep**: a 15-minute, room-by-room cleaning challenge where you prove each room is clean with before and after photos. Vanilla HTML, CSS and JS in one file, no libraries, dark theme (slate background, sky-blue accent, red for dirty, green for clean).

## Rooms
Kitchen, Bathroom, Bedroom, Living Room, Dining, Entryway, Laundry. Each is a card with a status badge (Not started / Dirty • n/5 / Clean), two photo boxes (BEFORE, AFTER) and a rating row.

## Flow
1. Rate how dirty the room is, 1 (light) to 5 (disaster). The before photo is locked until a rating exists.
2. Add the before photo using a file input (`accept="image/*" capture="environment"`). Do not use a live camera stream. Resize to 800px and save as JPEG at 0.7 quality.
3. Clean the room, then add the after photo. The after photo is locked until a before photo exists.
4. When both photos exist, show **Confirm Clean**. Confirming marks the room done (green, dimmed) with a **Reset Zone** button.
5. When all rooms are done, stop the timer and celebrate.

## Timer
- 15:00 countdown, shown as a pill at the top right and a progress bar across the top.
- It does NOT start automatically. A **Start Sweep / Pause / Resume** button under the title and tapping the pill both toggle it.
- Compute remaining time from an end timestamp so it stays correct when the screen sleeps.
- Last 60 seconds: red and pulsing. At zero: vibrate and show the result.

## Spite Agent
A coach box with a sharp, mocking-but-motivating tone (never abusive about the person, only about the mess). It:
- ranks rooms by dirtiness and shows an "Attack Order (filthiest first)" list;
- has 2 lines per rating level (1-5) that match the severity;
- reacts to each before photo, each confirmed room, the 5-minute and 2-minute marks, time up, and everything clean.

## Voice Coach
A toggle button. When on, the agent speaks every message using speech synthesis, and each message ends with the single next step ("Rate Kitchen from one to five", "Take the before photo of your worst room", "Clean Bedroom, then take the after photo", "Confirm Bedroom is clean"). Optional **Talk** button using speech recognition (hide it if unsupported or the mic is blocked) for commands: "rate kitchen four", "start", "pause", "what's next", "kitchen done".

## Other
- Save everything in localStorage inside try/catch; the app must work if storage fails.
- No alert/confirm dialogs. Reset Everything needs a second tap within 4 seconds.
- Respect safe-area insets, 16px side gutters, works at 400px wide.
- Keep it fast and usable one-handed.
