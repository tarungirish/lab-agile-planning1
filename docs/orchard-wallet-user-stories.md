# Orchard Wallet: EV Charging + Club Access

## Product summary

Drivers add an **Orchard card** to Apple Wallet or Google Wallet. There is no mobile app. Tapping the card at an Electric Era charger starts a charging session. A **club access fee is automatically added to every charging session**, so every driver can use the club without any extra decision. The card also opens the club door and pays at the micro market. All driver communication is by **text** (and email for receipts). After every charging session Orchard creates a private web page for that session and texts the driver its link. That page is the driver's account screen, so no app and no login are needed.

The Orchard card is tied to the **default payment card in the driver's mobile wallet** (Apple Pay / Google Pay), chosen at signup and saved with Stripe. Charges are made to that card **after** the charging session completes. There is no prepaid balance.

Each account is identified by the driver's **mobile phone number** (one number, one account). Orchard staff manage accounts in an **internal dashboard**.

**Charging partner:** Electric Era designs and builds the charging infrastructure for Orchard's sites, so tap detection, session events and session control are requirements we specify to them (see "Requirements for Electric Era").

## User journey

1. **Sign up.** The driver signs up on a web page and verifies their phone number by text.
2. **Get the card.** They add the Orchard card to Apple Wallet or Google Wallet.
3. **Link a card.** They authorize their wallet's default card (Apple Pay / Google Pay) so Orchard can charge it after each session.
4. **Arrive and tap.** They tap the card on the charger's NFC reader and charging starts. The club fee is automatically part of the session.
5. **Wait in the club.** They tap the card at the club door to enter and wait while the car charges. No offer or reply needed.
6. **Stay informed.** They get a text when the car is done, or an "80% charged, would you like to unplug now?" reminder.
7. **Shop.** They can buy items at the micro market with the card.
8. **Leave.** They get a text with a link to their session page: the charging summary, the $3 access fee, their purchases, and their cards. A receipt is emailed too.

## Messaging channel

Driver messaging uses **business messaging**: Apple Messages for Business on iPhone and Google RCS Business Messaging on Android, with **SMS as a fallback** for phones or situations where they are not available. Throughout this document, "text" means a message sent over whichever of these channels the driver is on. The conversation, requests, and replies are the same on all of them, and buttons (e.g. "Update card", "View session") replace raw links where the channel supports them.

Implications to design for:
* **Identity.** Apple Messages for Business does not give Orchard the driver's phone number, only an opaque ID for the conversation. The driver must connect their conversation to their account once (for example by tapping a "Message Orchard" button at signup, which sends a one-time code that Orchard matches to the phone number verified at signup).
* **Who speaks first.** Apple Messages for Business is driver-initiated, so Orchard can send alerts only after the driver has opened the conversation. Signup therefore includes opening the conversation. RCS Business Messaging allows Orchard to start conversations once the driver has opted in.
* **Setup and approval.** Each channel needs a registered business, approval from the platform, and typically a messaging provider. SMS fallback still needs carrier registration (A2P 10DLC).
* **Fallback.** If a driver has not connected a business messaging channel, or a message cannot be delivered, Orchard falls back to SMS.

## Text message format

Each Orchard text to a driver ends with this account summary. The text after a session also includes the link to the session page.

```
Today's spend - $__
Purchased - __
Year to date kWh usage - __ kWh
In store total spend - $__
```

* **Today's spend:** everything charged to the driver's card today (charging, club fee if any, and micro market purchases), in the account's time zone.
* **Purchased:** the micro market items bought today, e.g. "Water, Protein bar". Shows "Nothing yet" if none. A long list is shortened ("Water, Protein bar +3 more") and the full list is in the emailed receipt.
* **Year to date kWh usage:** the sum of energy delivered from completed sessions since January 1. Requires kWh from Electric Era's `session_completed` event.
* **In store total spend:** total spent at the club and micro market since January 1. Requires purchases to be recorded against the account.

## Decisions

* **Payments:** Stripe. The Orchard card is tied to the driver's default wallet card (Apple Pay / Google Pay), saved at signup for later charges made without the driver present. The charge happens only after the session completes.
* **Club fee:** every charging session automatically includes a flat club access fee of **$3**, for every driver including fleet. There is no opt-in text or prompt, so using the club is never a decision for the driver. The fee is shown prominently at signup and on every receipt. The fee is the only club charge: there are no day passes, memberships, or other access products. It exists to control the moment of charging and access to the lounge, and to bring drivers out of the car.
* **No prepaid balance:** preloads, top-up links, and the balance ledger are removed from scope.
* **Session page:** the page reached from the texted link is the account screen. Pages are per session and each link is a private, unguessable URL that can expire or be revoked.
* **Channel:** business messaging (Apple Messages for Business, Google RCS Business Messaging) with SMS fallback. See "Messaging channel".
* **Door:** an NFC reader at the club entrance reads the Orchard pass. The pass carries only an ID; the reader (or its controller) asks Orchard's backend whether that ID may enter, and unlocks the door on approval.

## Requirements for Electric Era

To confirm in the build agreement and spec:

1. **Tap credential.** Chargers read an NFC wallet pass (Apple/Google Wallet) and return a stable pass/credential ID. If not feasible, agree on a supported alternative (e.g. an RFID/NFC card credential).
2. **Session events.** Real-time webhooks or an API for `session_started` and `session_completed`, each with credential ID, charger ID, site ID, timestamps, and energy delivered (kWh).
3. **Charge progress.** Real-time state of charge (percent) for the vehicle where the charger can read it, or an event when a configurable level (e.g. 80%) is reached.
4. **Session control.** An API to stop a session on request (for the "unplug now" reply) and to show the driver a status on the charger.
5. **Authorization.** Orchard authorizes taps: Electric Era asks Orchard (or accepts an allow-list) so unknown or suspended accounts do not start sessions.
6. **Billing split.** Decide who bills for electricity (Electric Era or Orchard). Orchard bills at least the club fee and micro market purchases to the driver's card.
7. **Fleet.** Fleet drivers are identified by Orchard's account type; confirm how this interacts with any fleet programs on Electric Era's side.
8. **Sites.** The list of sites with the club and micro market, so club messages are only sent for those chargers.
9. **Reliability.** Retries and idempotency for events, and a way to reconcile missed events.
10. **Test environment.** Sandbox or test charger so the tap-to-text flow can be tested before launch.

## Open questions

* Fleet: the $3 fee applies to fleet drivers too. How is fleet status determined (fleet code, fleet account), and does the fleet company or the driver pay?
* Disclosure: where is the automatic club fee shown to the driver (signup page and terms, charger screen or label, receipts)? Confirm what your state requires for disclosing mandatory fees.
* Is the fee charged if a session is very short or stopped early, or if the charger faults? Refund policy for those cases.
* Apple and Google must approve Orchard for NFC-enabled passes (Apple VAS / NFC pass entitlement, Google Smart Tap). Start the applications early; this gates both the chargers and the door reader.
* Which door reader and micro market hardware, and do they support Apple VAS and Google Smart Tap?
* Micro market: how does the driver pay (tap the card on a register or kiosk)? Which point-of-sale system records purchases against the account?
* Micro market charges are small, and card processing fees have a fixed per-charge part. Combine purchases from one visit into one charge, or charge each purchase?
* Apple Pay / Google Pay default card: confirm with Stripe how a card from the wallet is saved for later charges without the driver present. A wallet card is chosen by the driver at signup; Orchard cannot read the wallet's cards afterwards.
* Does club access last for the whole visit or end when the charging session ends?
* Unplug reminder: percent threshold(s), and whether idle fees apply after charging completes.
* Failed charges: retry schedule, and does the account get blocked from further sessions or the club until paid?
* Is "year to date" the calendar year or a rolling 12 months?
* Text opt-in/opt-out compliance (TCPA) and STOP handling, on every channel including business messaging.
* Which messaging provider will connect Orchard to Apple Messages for Business, RCS Business Messaging, and SMS? Check what each platform currently requires (business verification, driver-initiated rules, message content limits).
* How is the Apple conversation tied to the verified phone number, and what happens if a driver changes phones or numbers?
* Which alerts are important enough to fall back to SMS if the rich channel fails (e.g. charging complete, failed card)?

---

## Stories

### 1. Sign up and verify phone

**As a** driver
**I need** to sign up with my phone number
**So that** I have an Orchard account without installing an app

#### Details and Assumptions
* Web signup collects name, mobile number, email, and optional fleet code.
* The phone number is the account identifier and must be unique. It is verified with a one-time text code.
* Signup includes text-message consent.
* After verifying the phone number, signup includes opening the Orchard conversation in Apple Messages (iPhone) or Google Messages/RCS (Android) so alerts can reach the driver. If the driver skips this, they receive SMS.
* The signup page shows the $3 access fee prominently, before the driver links a card or agrees to the terms. The prototype is in `prototype/signup.html`.
* Signup requires an explicit tick to agree to: texts, being charged to the saved card after each session, and the $3 access fee added to every session.

#### Acceptance Criteria

```gherkin
Given I enter a phone number not used by another account
When I enter the correct verification code from the text
Then my account is created

Given I am on the signup page
When I view it before linking a card
Then the $3 access fee added to every charging session is clearly shown, with a statement that it applies whether or not I visit the club

Given I have not ticked the agreement
When I try to continue
Then I cannot link a card

Given a phone number already has an account
When someone signs up with that number
Then no second account is created
```

### 2. Add the Orchard card to my wallet

**As a** driver
**I need** to add an Orchard card to my Apple or Google Wallet
**So that** I can tap to charge, enter the club and pay

#### Details and Assumptions
* The pass contains an identifier only, no payment card data.
* Available right after signup; a link is also texted so the driver can add it later.

#### Acceptance Criteria

```gherkin
Given I have a verified account
When I choose "Add to Apple Wallet" or "Add to Google Wallet"
Then the Orchard card appears in my wallet
```

### 3. Link my wallet card

**As a** driver
**I need** to link my wallet's default card to my Orchard card
**So that** charges go to a card I already use

#### Details and Assumptions
* At signup the driver authorizes Apple Pay or Google Pay on the web page and agrees to be charged after sessions and purchases.
* Stripe saves the card for later charges made without the driver present.
* The driver can replace the card by text link; the dashboard can also send that link.
* A linked card is required before a tap can start a session (story 6). The link can be completed before or after the Orchard card is added to the wallet.

#### Acceptance Criteria

```gherkin
Given I am signing up
When I authorize Apple Pay or Google Pay and agree to the terms
Then my card is saved and my account is ready to use

Given I have added the Orchard card but have not linked a card
When I tap my card at a charger
Then the session does not start and I receive a text with a link to link a card
```

### 4. Open my session page from a texted link

**As a** driver
**I need** a page created after every charging session, with a link texted to me
**So that** I can see what I used, what I was charged, and manage my cards without an app or a password

#### Details and Assumptions
* After each session completes, Orchard creates a page for that session and texts the driver its URL. This page is the account screen: there is no app.
* The page shows: this session (charger, time, duration, kWh delivered, charging amount, club access fee, total, and the card charged by brand and last four digits), micro market purchases today, Today's spend, In store total spend, Year to date kWh usage, whether the Orchard card is in the wallet, the saved payment card, and earlier sessions.
* The link is the credential. It is a long random token, tied to one account, with no guessable or sequential parts.
* The page shows only what a receipt needs: first name, last four digits of the card, and the phone number masked. No full card numbers or email address.
* Actions that change something (add the Orchard card again, update the payment card) send a new text with a secure link. Card details are entered on a Stripe page, never on the Orchard page.
* Links can be short-lived (to decide, e.g. 30 days) because history is always available by text (story 5). Links can be revoked in the dashboard. Pages must not be indexed by search engines.
* If a link has expired, the driver texts Orchard (story 5) or uses the login page (code by text) to get a fresh one.
* Unknown phone numbers on the login page are pointed to signup without revealing whether an account exists. Codes expire quickly, are single-use, and are rate-limited per number.
* Prototypes: `prototype/session.html` (the session page) and `prototype/login.html` (get-a-link fallback).

#### Acceptance Criteria

```gherkin
Given my charging session has completed and my card has been charged
When the system finalizes the session
Then a page is created for the session and I receive a text with its link

Given I open the link on my phone
When the page loads
Then I see the session itemized with the $3 club access fee, my totals, and my cards, without logging in

Given the link has expired or has been revoked
When I open it
Then I see a message that it is no longer valid and how to get a new one by texting Orchard

Given I choose "Text me a link to update my card"
When the text arrives
Then it contains a secure Stripe link and no card details are entered on the Orchard page
```

### 5. Ask Orchard by text

**As a** driver
**I need** to text Orchard's number and ask for my history and totals
**So that** I can get my information any time, without an app, a login, or a link that has not expired

#### Details and Assumptions
* The driver texts the same Orchard number that sends their notifications. The sender's phone number identifies the account.
* Supported requests (read-only), in the driver's own words, e.g.:
  * "charging history" / "history": the last 3 sessions (date, kWh, total) plus a link to the full history page
  * "total spend" / "how much have I spent": spend this month and year to date, and in store total spend
  * "kWh" / "usage": year to date kWh usage
  * "last receipt": the last session's total with its itemized lines and a link to its page
  * "link": a fresh link to the latest session page
  * "update card": a secure Stripe link to change the payment card
  * "help": the list of things they can ask
* Matching: simple keywords handle the common requests. An AI model may be used to interpret free-form wording, but only to choose one of the fixed requests above. It never reads or writes payments or accounts directly, never invents numbers, and every number in a reply comes from Orchard's records.
* Replies are short, end with the account summary lines, and give a link for anything longer.
* Anything that changes money or account details (update card, refunds, changing the number) is never done by text alone. It sends a secure link, or is handled by staff.
* Unrecognized messages get a short help message. A request that needs a human ("dispute a charge") is passed to staff in the dashboard, and the driver is told someone will reply.
* STOP, HELP, and START keep working as carrier rules require.
* Prototype: `prototype/texts.html` shows the conversation, including the notifications sent for a session and the replies to each request.
* The sender number is the only proof of identity. Because phone numbers can be spoofed or taken over (SIM swap), replies contain no full card numbers or other sensitive details, and rate limits apply per number.

#### Acceptance Criteria

```gherkin
Given I am a driver with at least one completed session
When I text "charging history" to Orchard's number
Then I receive my last 3 sessions with date, kWh, and total, and a link to my full history

Given I text "how much have I spent"
When the system receives it
Then I receive my spend this month and year to date, and my in store total spend

Given I text something Orchard does not understand
When the system replies
Then I receive a short list of what I can ask for

Given I text "update card"
When the system replies
Then I receive a secure Stripe link and no card details are exchanged by text

Given a text comes from a number that has no Orchard account
When the system replies
Then it points them to signup and shares no account information
```

### 6. Tap to start charging

**As a** driver
**I need** to tap my Orchard card on the charger
**So that** my charging session starts and is linked to my account

#### Details and Assumptions
* Backend receives a "pass tapped at charger X" event (via Electric Era) and looks up the account by pass ID.
* Unknown or suspended accounts do not start a session.
* The session starts if the account is active with a valid saved card.
* Otherwise the tap is declined, the reason is returned to Electric Era's system and shown to staff in the dashboard, and a text with a link to update the card is sent.

#### Acceptance Criteria

```gherkin
Given I have an active account with a saved card
When I tap my card at an available charger
Then a charging session starts and is associated with my account

Given my saved card has been declined or removed
When I tap my card at a charger
Then the session does not start and I receive a text with a link to update my card
```

### 7. Enter the club with the Orchard card

**As a** driver
**I need** to tap my Orchard card at the club door
**So that** I can wait in the club while my car charges

#### Details and Assumptions
* The door has an NFC reader. A tap sends the pass ID to Orchard's backend, which answers allow or deny (target under 1 second).
* Access rule: the account has an active charging session. The club fee is already part of the session, so no further step is needed.
* Every tap, allowed or denied, is logged and visible in the dashboard.
* Open: whether access continues for the rest of the visit after the session ends.

#### Acceptance Criteria

```gherkin
Given I have an active charging session
When I tap my Orchard card at the club door
Then the door unlocks

Given I have no active charging session
When I tap my Orchard card at the club door
Then entry is denied and the tap is logged
```

### 8. Unplug reminder at 80%

**As a** driver
**I need** a text when my car reaches 80%
**So that** I can choose to finish and unplug

#### Details and Assumptions
* Requires state of charge from Electric Era (requirement 3) and the ability to stop a session (requirement 4).
* Text example: "Your car is at 80%. Reply STOP CHARGING to finish now." The word is chosen so it is not confused with the texting STOP opt-out.
* Threshold is configurable; default 80%.
* If the driver does not reply, charging continues until the vehicle or charger stops it.

#### Acceptance Criteria

```gherkin
Given my car reaches the configured percent while charging
When the system receives the progress event
Then I receive a text asking whether to finish and unplug, with the account summary

Given I reply "STOP CHARGING"
When the system receives my reply
Then the charging session is stopped and I receive a confirmation text

Given I do not reply
When charging continues
Then the session is unchanged
```

### 9. Charging complete notification

**As a** driver
**I need** a text when my car has finished charging
**So that** I know when to come and unplug

#### Acceptance Criteria

```gherkin
Given my charging session has ended
When the system receives session_completed
Then I receive a text telling me charging is complete, with the account summary
```

### 10. Charge my card after the session

**As a** driver
**I need** my card charged only after my charging session ends
**So that** I pay for the session and club access only after I have used the station

#### Details and Assumptions
* Total = charging cost (if Orchard bills it) + club access fee ($3).
* The club fee applies to every session, whether or not the driver visits the club.
* The charge uses the saved card as an off-session Stripe payment.
* Use an idempotency key per session so a repeated event never charges twice.
* If the charge fails, the failure is flagged in the dashboard and the driver is texted a link to update the card. Retry policy to be decided.

#### Acceptance Criteria

```gherkin
Given my charging session has completed
When the system processes the payment
Then my card is charged once for the session total, including the $3 club access fee

Given my session is still in progress
When anything is owed
Then my card has not been charged yet

Given my card charge fails
When the system processes the result
Then the failure is flagged in the dashboard and I receive a text with a link to update my card
```

### 11. Buy at the micro market

**As a** driver
**I need** to pay at the micro market with my Orchard card
**So that** I can buy items without cash or another card

#### Details and Assumptions
* Paid with the saved card, like story 10. Whether purchases are charged one by one or combined is an open question.
* The way the driver pays at the market (tap at a register or kiosk) depends on the point-of-sale system chosen.
* Each purchase is recorded against the account and counts toward In store total spend.
* Not limited to when a charging session is active.

#### Acceptance Criteria

```gherkin
Given I have a saved card in good standing
When I tap my Orchard card to pay for a purchase
Then my card is charged and the purchase is recorded against my account

Given my saved card is declined
When I tap my Orchard card to pay
Then the purchase is declined and I receive a text with a link to update my card
```

### 12. Session text and receipts

**As a** driver
**I need** a text with my session page link and an emailed receipt
**So that** I know what I used and was charged

#### Details and Assumptions
* The session page (story 4) is the charging summary: duration, kWh delivered, charging amount, club access fee, total, and the card charged (last four digits).
* The text after the session is short: it says charging is complete, gives the link, and ends with the account summary.
* An itemized receipt is also emailed. Micro market purchases get their own receipt (email, plus the text with the total) and also appear on the session page.
* The session page and the receipt are generated after the card is charged, so the amounts are final. If the charge fails, the page says so and offers a link to update the card.

#### Acceptance Criteria

```gherkin
Given my card has been charged for my session
When the session is finalized
Then I receive a text with the total charged and a link to my session page, and an email with the itemized receipt

Given I bought items at the micro market
When the purchase completes
Then I receive an itemized receipt by email and a text with the total
```

### 13. Account summary in texts

**As a** driver
**I need** to see today's spend, what I bought, and my year-to-date totals in each text
**So that** I always know where my account stands

#### Details and Assumptions
* Format is defined in "Text message format". Values: $ with two decimals, kWh with one.
* Values reflect the state after the event that triggered the text.
* A text sent before the card is charged (e.g. the 80% reminder) shows spend so far today, not including the session in progress.
* Summary lines are appended after the main message.

#### Acceptance Criteria

```gherkin
Given I am a driver with an active account
When I receive a text from Orchard
Then it ends with Today's spend, Purchased, Year to date kWh usage, and In store total spend

Given I bought two items at the micro market today
When I receive a text
Then Purchased lists those items and Today's spend includes their cost

Given I have had no sessions or purchases
When I receive a text
Then Today's spend, kWh usage and in-store spend show zero, and Purchased shows "Nothing yet", not blank
```

### 14. Manage accounts in the internal dashboard

**As an** Orchard staff member
**I need** a dashboard to view and manage driver accounts, sessions and purchases
**So that** I can support drivers and resolve payment issues

#### Details and Assumptions
* Search by phone, email, or name.
* Set or change user type (public/fleet) and change an account's phone number. User type does not change the access fee.
* View sessions, door taps, purchases, declined taps, and failed payments.
* Refund a charge through Stripe, with a reason recorded.
* Suspend or reactivate an account.

#### Acceptance Criteria

```gherkin
Given I am a signed-in staff member
When I change an account's user type from public to fleet
Then the account is marked as fleet for reporting and billing, and the access fee is unchanged

Given a card charge has failed
When I open the dashboard
Then the account and session are flagged with the amount owed
```

### 15. Text opt-out

**As a** driver
**I need** to be able to stop text messages
**So that** I control how Orchard contacts me

#### Acceptance Criteria

```gherkin
Given I receive a text from Orchard
When I reply "STOP"
Then I receive no further texts and the dashboard shows my opt-out
```
