# Orchard Wallet: EV Charging + Club Access

## Product summary

Drivers add an **Orchard card** to Apple Wallet or Google Wallet. There is no mobile app. Tapping the card at an Electric Era charger starts a charging session. The card also opens the club door and pays at the micro market. All driver communication is by **text** (and email for receipts).

The Orchard card is tied to the **default payment card in the driver's mobile wallet** (Apple Pay / Google Pay), chosen at signup and saved with Stripe. Charges are made to that card **after** the charging session completes. There is no prepaid balance.

Each account is identified by the driver's **mobile phone number** (one number, one account). Orchard staff manage accounts in an **internal dashboard**.

**Charging partner:** Electric Era designs and builds the charging infrastructure for Orchard's sites, so tap detection, session events and session control are requirements we specify to them (see "Requirements for Electric Era").

## User journey

1. **Sign up.** The driver signs up on a web page and verifies their phone number by text.
2. **Get the card.** They add the Orchard card to Apple Wallet or Google Wallet.
3. **Link a card.** They authorize their wallet's default card (Apple Pay / Google Pay) so Orchard can charge it after each session.
4. **Arrive and tap.** They tap the card on the charger's NFC reader and charging starts.
5. **Wait in the club.** They tap the card at the club door to enter and wait while the car charges.
6. **Stay informed.** They get a text when the car is done, or an "80% charged, would you like to unplug now?" reminder.
7. **Shop.** They can buy items at the micro market with the card.
8. **Leave.** They get a charging summary and a receipt for micro market purchases.

> **Unconfirmed:** an earlier version of this plan added a "Would you like to use the club?" text with a club fee ($5 public, $3 fleet) when charging starts. The journey above does not mention it. Stories 5 to 7 keep it until confirmed. If the club is simply included for any driver who is charging, stories 5 and 6 and the fee come out and story 7 grants access to any active session.

## Text message format

Each Orchard text to a driver ends with this account summary:

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
* **No prepaid balance:** preloads, top-up links, and the balance ledger are removed from scope.
* **Door:** an NFC reader at the club entrance reads the Orchard pass. The pass carries only an ID; the reader (or its controller) asks Orchard's backend whether that ID may enter, and unlocks the door on approval.
* **Offer expiry (if the club offer stays):** the offer is valid only while the charging session is in progress.

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

* **Is the club offer and fee still in?** See the note under "User journey".
* Apple and Google must approve Orchard for NFC-enabled passes (Apple VAS / NFC pass entitlement, Google Smart Tap). Start the applications early; this gates both the chargers and the door reader.
* Which door reader and micro market hardware, and do they support Apple VAS and Google Smart Tap?
* Micro market: how does the driver pay (tap the card on a register or kiosk)? Which point-of-sale system records purchases against the account?
* Micro market charges are small, and card processing fees have a fixed per-charge part. Combine purchases from one visit into one charge, or charge each purchase?
* Apple Pay / Google Pay default card: confirm with Stripe how a card from the wallet is saved for later charges without the driver present. A wallet card is chosen by the driver at signup; Orchard cannot read the wallet's cards afterwards.
* Does club access last for the whole visit or end when the charging session ends?
* Unplug reminder: percent threshold(s), and whether idle fees apply after charging completes.
* Failed charges: retry schedule, and does the account get blocked from further sessions or the club until paid?
* Is "year to date" the calendar year or a rolling 12 months?
* How fleet status is determined (fleet code, fleet account) and who is billed.
* Text opt-in/opt-out compliance (TCPA) and STOP handling.

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

#### Acceptance Criteria

```gherkin
Given I enter a phone number not used by another account
When I enter the correct verification code from the text
Then my account is created

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
* A linked card is required before a tap can start a session (story 4). The link can be completed before or after the Orchard card is added to the wallet.

#### Acceptance Criteria

```gherkin
Given I am signing up
When I authorize Apple Pay or Google Pay and agree to the terms
Then my card is saved and my account is ready to use

Given I have added the Orchard card but have not linked a card
When I tap my card at a charger
Then the session does not start and I receive a text with a link to link a card
```

### 4. Tap to start charging

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

### 5. Club offer by text (unconfirmed)

**As a** driver
**I need** to be asked by text whether I want club access
**So that** I can add the lounge to my visit

#### Details and Assumptions
* Only if the club offer stays (see the note under "User journey").
* Text is sent to the verified phone number within seconds of the tap.
* The price is by user type: public $5, fleet $3.

#### Acceptance Criteria

```gherkin
Given my charging session has started
When the system sends me the club offer
Then I receive a text stating the club price for my user type
```

### 6. Accept or decline the club offer (unconfirmed)

**As a** driver
**I need** to reply YES or NO to the club offer
**So that** I only pay for the club if I want it

#### Details and Assumptions
* No reply is treated as a decline.
* The offer is valid only during the charging session. A YES after `session_completed` gets a reply that the offer has expired, and no fee is added.
* Replies are matched to the account by the sender's phone number, are case-insensitive, and unrecognized replies get a help message.
* The fee is deducted when the session completes, not at reply time.

#### Acceptance Criteria

```gherkin
Given I received a club offer for my active session
When I reply "YES"
Then the club fee is added to my session and I receive a confirmation text

Given I received a club offer
When I reply "NO" or do not reply
Then no club fee is added to my session
```

### 7. Enter the club with the Orchard card

**As a** driver
**I need** to tap my Orchard card at the club door
**So that** I can wait in the club while my car charges

#### Details and Assumptions
* The door has an NFC reader. A tap sends the pass ID to Orchard's backend, which answers allow or deny (target under 1 second).
* Access rule: an active charging session, plus an accepted offer if the club offer stays.
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
**So that** I pay for what I actually used

#### Details and Assumptions
* Total = charging cost (if Orchard bills it) + club fee (if applicable).
* The charge uses the saved card as an off-session Stripe payment.
* Use an idempotency key per session so a repeated event never charges twice.
* If the charge fails, the failure is flagged in the dashboard and the driver is texted a link to update the card. Retry policy to be decided.

#### Acceptance Criteria

```gherkin
Given my charging session has completed
When the system processes the payment
Then my card is charged once for the session total

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

### 12. Charging summary and purchase receipts

**As a** driver
**I need** a summary of my charge and a receipt for my purchases
**So that** I know what I used and was charged

#### Details and Assumptions
* Charging summary after the session: duration, kWh delivered, amounts, and the card charged (last four digits).
* Purchase receipt for micro market items: itemized, sent by email, with a text showing the total.
* The charging summary and purchase receipt are separate messages.

#### Acceptance Criteria

```gherkin
Given my card has been charged for my session
When the session is finalized
Then I receive a text with the total charged, and an email with the itemized charging summary

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
* Set or change user type (public/fleet) and change an account's phone number.
* View sessions, door taps, purchases, declined taps, and failed payments.
* Refund a charge through Stripe, with a reason recorded.
* Suspend or reactivate an account.

#### Acceptance Criteria

```gherkin
Given I am a signed-in staff member
When I change an account's user type from public to fleet
Then future fleet pricing applies to that account

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
