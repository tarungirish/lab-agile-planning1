# App Store submission checklist

## Done in this repo
- Capacitor iOS project in `ios/` wrapping `www/index.html`
- Camera / photo permission text and export-compliance flag in `Info.plist`
- iPhone portrait only, 1024x1024 app icon (placeholder design, replace if you like)
- Haptics and a local "time's up" notification (native extras that help with review)
- Privacy policy text in `docs/privacy-policy.md`

## You must do (needs your Apple account and a Mac)
1. Enrol in the Apple Developer Program ($99/yr).
2. Pick a unique bundle ID and set it in `capacitor.config.json` (`appId`, currently `com.example.cleansweep`) and in Xcode, then run `npx cap sync ios`.
3. `npm install && npx cap sync ios && npx cap open ios`.
4. In Xcode: Signing & Capabilities -> choose your Team. Set Version 1.0 and Build 1.
5. Test on a real iPhone: ratings, camera, timer, notification, haptics, voice coach.
6. Product -> Archive -> Distribute App -> App Store Connect.
7. Host the privacy policy at a public URL (GitHub Pages works) and paste it into App Store Connect.
8. Fill in App Store Connect: name, subtitle, description, keywords, category (Lifestyle), age rating, screenshots (6.9" and 6.5" iPhone), and the privacy answers ("Data Not Collected").
9. Submit for review (typically 1-2 days).

## Suggested listing text
- **Name:** Clean Sweep
- **Subtitle:** Photo-proof cleaning timer
- **Description:** Race the clock on any cleaning job: home, car, garage, desk or your own list. Rate how bad each task is, take a before photo, clean, take an after photo and lock it in. A voice coach tells you where to start and keeps you moving. Everything stays on your phone.
- **Keywords:** cleaning,timer,chores,tidy,declutter,housework,productivity
- **Review notes:** No login needed. Tap Start Sweep, rate a room, then add photos with the camera.

## Known risks
- Reviewers may reject thin web wrappers; the native haptics and notification help, but test them.
- Voice input (Talk) does not work in the iOS web view, so the button stays hidden. The spoken coach does work.
- Photos live in the web view's local storage; iOS can clear it under storage pressure. Moving them to native file storage is a good v1.1.
- This was built on Linux: I could not compile or run the iOS project, so the first Xcode build is untested.
