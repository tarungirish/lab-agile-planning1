# Prompt to build Clean Sweep

Build a mobile-first single-page web app called **Clean Sweep**: a timed cleaning challenge for any kind of cleaning task (home, car, garage, desk, yard) where you prove each task is done with before and after photos. Vanilla HTML, CSS and JS in one file, no libraries, dark theme (slate background, sky-blue accent, red for dirty, green for clean).

## Tasks (any kind of cleaning)
The app works for any cleaning job, not just tasks. Each task is a card with a status badge (Not started / Dirty • n/5 / Clean), a remove button, two photo boxes (BEFORE, AFTER) and a rating row.
- Starter lists: Home, Car, Garage, Desk, Bathroom deep clean, Move-out, Yard (about 6-7 tasks each).
- A collapsible "Customize tasks and time" panel lets people load a starter list, add their own tasks (max 20, names up to 30 chars, strip quotes and HTML characters), remove tasks, and choose 10, 15, 30, 45 or 60 minutes.
- Loading a list with progress needs a second tap, and the time can only change before the clock has started.
- Save the task list and time in localStorage.

## Flow
1. Rate how dirty the room is, 1 (light) to 5 (disaster). The before photo is locked until a rating exists.
2. Add the before photo using a file input (`accept="image/*" capture="environment"`). Do not use a live camera stream. Resize to 800px and save as JPEG at 0.7 quality.
3. Clean the room, then add the after photo. The after photo is locked until a before photo exists.
4. When both photos exist, show **Confirm Clean**. Confirming marks the room done (green, dimmed) with a **Reset Zone** button.
5. When all tasks are done, stop the timer and celebrate.

## Timer
- Countdown (default 15:00), shown as a pill at the top right and a progress bar across the top.
- It does NOT start automatically. A **Start Sweep / Pause / Resume** button under the title and tapping the pill both toggle it.
- Compute remaining time from an end timestamp so it stays correct when the screen sleeps.
- Last 60 seconds: red and pulsing. At zero: vibrate and show the result.

## Spite Agent
A coach box with a sharp, mocking-but-motivating tone (never abusive about the person, only about the mess). It:
- ranks tasks by dirtiness and shows an "Attack Order (filthiest first)" list;
- has 2 lines per rating level (1-5) that match the severity;
- reacts to each before photo, each confirmed room, the 5-minute and 2-minute marks, time up, and everything clean.

## Voice Coach
A toggle button. When on, the agent speaks every message using speech synthesis, and each message ends with the single next step ("Rate Kitchen from one to five", "Take the before photo of your worst room", "Clean Bedroom, then take the after photo", "Confirm Bedroom is clean"). Optional **Talk** button using speech recognition (hide it if unsupported or the mic is blocked) for commands: "rate kitchen four", "start", "pause", "what's next", "kitchen done".

## Other
- Save everything in localStorage inside try/catch; the app must work if storage fails.
- No alert/confirm dialogs. Reset Everything needs a second tap within 4 seconds.
- Respect safe-area insets, 16px side gutters, works at 400px wide.
- Keep it fast and usable one-handed.
