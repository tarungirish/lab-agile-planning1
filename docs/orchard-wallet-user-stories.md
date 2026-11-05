# Orchard Wallet: EV Charging + Club Access

## Product summary

Drivers add an **Orchard card** to Apple Wallet or Google Wallet. There is no mobile app. Tapping the card at an Electric Era charger starts a charging session. The card also opens the club door and pays at the micro market. All driver communication is by **text** (and email for receipts).

Drivers pay in one of two ways, or both:
* a **prepaid balance** (a preload), topped up through a Stripe payment link sent by text; and/or
* a **card on file**, taken from the wallet's default payment card (Apple Pay / Google Pay) at signup.

Each account is identified by the driver's **mobile phone number** (one number, one account). Orchard staff manage accounts in an **internal dashboard**.

**Charging partner:** Electric Era designs and builds the charging infrastructure for Orchard's sites, so tap detection, session events and session control are requirements we specify to them (see "Requirements for Electric Era").

## User journey

1. **Sign up.** The driver signs up on a web page and verifies their phone number by text.
2. **Get the card.** They add the Orchard card to Apple Wallet or Google Wallet.
3. **Set up payment.** They preload a balance, use their wallet's default card, or both. They can top up later by text.
4. **Arrive and tap.** They tap the card on the charger's NFC reader and charging starts.
5. **Wait in the club.** They tap the card at the club door to enter and wait while the car charges.
6. **Stay informed.** They get a text when the car is done, or an "80% charged, would you like to unplug now?" reminder.
7. **Shop.** They can buy items at the micro market with the card.
8. **Leave.** They get a charging summary and a receipt for micro market purchases.

> **Unconfirmed:** an earlier version of this plan added a "Would you like to use the club?" text with a club fee ($5 public, $3 fleet) when charging starts. The journey above does not mention it. Stories 6 to 8 keep it until confirmed. If the club is simply included for any driver who is charging, stories 6 and 7 and the fee come out and story 8 grants access to any active session.

## Text message format

Each Orchard text to a driver ends with this account summary:

```
Current Balance - $__
Year to date kWh usage - __ kWh
In store total spend - $__
```

* **Current Balance:** the prepaid balance from the ledger, after any deduction for the event in the text.
* **Year to date kWh usage:** the sum of energy delivered from completed sessions since January 1. Requires kWh from Electric Era's `session_completed` event.
* **In store total spend:** total spent at the club and micro market since January 1. Requires purchases to be recorded against the account.

## Decisions

* **Payments:** Stripe. Preloads are bought through a Stripe-hosted link sent by text. The balance is credited only after Stripe confirms the payment (webhook).
* **Balance ledger:** Orchard keeps a ledger per account (top-ups, deductions, adjustments, refunds). The balance is never edited directly; every change is a ledger entry.
* **Order of payment:** the prepaid balance is used first. A card on file covers any shortfall.
* **Card on file:** the wallet's default payment card is captured at signup for later charges made without the driver present.
* **Door:** an NFC reader at the club entrance reads the Orchard pass. The pass carries only an ID; the reader (or its controller) asks Orchard's backend whether that ID may enter, and unlocks the door on approval.
* **Offer expiry (if the club offer stays):** the offer is valid only while the charging session is in progress.

## Requirements for Electric Era

To confirm in the build agreement and spec:

1. **Tap credential.** Chargers read an NFC wallet pass (Apple/Google Wallet) and return a stable pass/credential ID. If not feasible, agree on a supported alternative (e.g. an RFID/NFC card credential).
2. **Session events.** Real-time webhooks or an API for `session_started` and `session_completed`, each with credential ID, charger ID, site ID, timestamps, and energy delivered (kWh).
3. **Charge progress.** Real-time state of charge (percent) for the vehicle where the charger can read it, or an event when a configurable level (e.g. 80%) is reached.
4. **Session control.** An API to stop a session on request (for the "unplug now" reply) and to show the driver a status on the charger.
5. **Authorization.** Orchard authorizes taps: Electric Era asks Orchard (or accepts an allow-list) so unknown or suspended accounts do not start sessions.
6. **Billing split.** Decide who bills for electricity (Electric Era or Orchard). Orchard bills at least the club fee and micro market purchases.
7. **Fleet.** Fleet drivers are identified by Orchard's account type; confirm how this interacts with any fleet programs on Electric Era's side.
8. **Sites.** The list of sites with the club and micro market, so club messages are only sent for those chargers.
9. **Reliability.** Retries and idempotency for events, and a way to reconcile missed events.
10. **Test environment.** Sandbox or test charger so the tap-to-text flow can be tested before launch.

## Open questions

* **Is the club offer and fee still in?** See the note under "User journey".
* Apple and Google must approve Orchard for NFC-enabled passes (Apple VAS / NFC pass entitlement, Google Smart Tap). Start the applications early; this gates both the chargers and the door reader.
* Which door reader and micro market hardware, and do they support Apple VAS and Google Smart Tap?
* Micro market: how does the driver pay (tap the card on a register or kiosk, or a text link)? Which point-of-sale system records purchases against the account?
* Apple Pay / Google Pay default card: confirm with Stripe how a card from the wallet is saved for later charges without the driver present.
* Does club access last for the whole visit or end when the charging session ends?
* Preload rules: minimum balance to start a session, preload amounts, and whether auto-reload is allowed.
* Unplug reminder: percent threshold(s), and whether idle fees apply after charging completes.
* Refunds and expiry: can unused balance be refunded, and does it expire?
* Legal review of stored-value rules (gift card/stored-value, unclaimed property, refund requirements) before launch.
* What happens when a session costs more than balance plus card (negative balance, block future sessions)?
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
Then the Orchard card appears in my wallet and my account is active
```

### 3. Choose how to pay

**As a** driver
**I need** to pay with a preloaded balance, my wallet's default card, or both
**So that** I can use the payment method I prefer

#### Details and Assumptions
* At signup the driver can pay for a first preload, save the default card from Apple Pay / Google Pay, or do both.
* The prepaid balance is used first; a card on file covers any shortfall.
* A driver with neither a balance above the minimum nor a card on file cannot start a session (see story 5).
* Needs confirmation from Stripe on saving a wallet card for later charges.

#### Acceptance Criteria

```gherkin
Given I am signing up
When I pay with Apple Pay or Google Pay and choose to save the card
Then the card is saved to my account for later charges

Given I have both a balance and a saved card
When a session total exceeds my balance
Then my balance is used first and the card is charged the remainder
```

### 4. Preload funds by text

**As a** driver
**I need** to add money to my Orchard balance using a link sent by text
**So that** I can top up without an app

#### Details and Assumptions
* A text with a Stripe-hosted payment link is sent when the balance is below the minimum, or when the driver replies "ADD" or "TOPUP".
* The link is single-use, tied to the account, and expires after a short time.
* The balance is credited only when Stripe confirms payment (webhook), using the Stripe payment ID as the idempotency key.
* A confirmation text states the new balance.

#### Acceptance Criteria

```gherkin
Given my balance is below the minimum
When I tap my card at a charger
Then I receive a text with a link to add funds

Given I open the link and pay successfully
When Stripe confirms the payment
Then my balance increases by the amount paid and I receive a confirmation text with the new balance

Given the same Stripe payment is reported twice
When the system processes the second report
Then my balance is only credited once
```

### 5. Tap to start charging

**As a** driver
**I need** to tap my Orchard card on the charger
**So that** my charging session starts and is linked to my account

#### Details and Assumptions
* Backend receives a "pass tapped at charger X" event (via Electric Era) and looks up the account by pass ID.
* Unknown or suspended accounts do not start a session.
* The session starts if the balance is at or above the minimum, or the account has a card on file.
* Otherwise the tap is declined, the reason is returned to Electric Era's system and shown to staff in the dashboard, and a top-up text is sent.

#### Acceptance Criteria

```gherkin
Given I have an active account and enough balance or a saved card
When I tap my card at an available charger
Then a charging session starts and is associated with my account

Given my balance is below the minimum and I have no saved card
When I tap my card at a charger
Then the session does not start and I receive a text with a top-up link
```

### 6. Club offer by text (unconfirmed)

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

### 7. Accept or decline the club offer (unconfirmed)

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

### 8. Enter the club with the Orchard card

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

### 9. Unplug reminder at 80%

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

### 10. Charging complete notification

**As a** driver
**I need** a text when my car has finished charging
**So that** I know when to come and unplug

#### Acceptance Criteria

```gherkin
Given my charging session has ended
When the system receives session_completed
Then I receive a text telling me charging is complete, with the account summary
```

### 11. Settle the session

**As a** driver
**I need** the session cost taken from my balance, then my card if needed, only after charging ends
**So that** I pay for what I actually used

#### Details and Assumptions
* Total = charging cost (if Orchard bills it) + club fee (if applicable).
* The balance is used first (a ledger entry). Any shortfall is charged to the saved card.
* Use an idempotency key per session so a repeated event never charges twice.
* If neither can cover the total, the shortfall is recorded, the dashboard flags it, and a top-up link is texted (policy to be decided).

#### Acceptance Criteria

```gherkin
Given my charging session has completed
When the system settles the session
Then my balance is reduced once by the session total

Given my balance is less than the session total and I have a saved card
When the system settles the session
Then my balance is used first and my card is charged the remainder

Given my session is still in progress
When anything is owed
Then nothing has been charged yet
```

### 12. Buy at the micro market

**As a** driver
**I need** to pay at the micro market with my Orchard card
**So that** I can buy items without cash or another card

#### Details and Assumptions
* Payment method: balance first, then the card on file, like story 11.
* The way the driver pays at the market (tap at a register or kiosk) depends on the point-of-sale system chosen.
* Each purchase is recorded against the account and counts toward In store total spend.
* Not limited to when a charging session is active.

#### Acceptance Criteria

```gherkin
Given I have enough balance or a saved card
When I tap my Orchard card to pay for a purchase
Then the purchase is paid and recorded against my account

Given I have neither enough balance nor a saved card
When I tap my Orchard card to pay
Then the purchase is declined and I receive a text with a top-up link
```

### 13. Charging summary and purchase receipts

**As a** driver
**I need** a summary of my charge and a receipt for my purchases
**So that** I know what I used and was charged

#### Details and Assumptions
* Charging summary after the session: duration, kWh delivered, amounts, and how it was paid.
* Purchase receipt for micro market items: itemized, sent by email, with a text showing the total.
* The charging summary and purchase receipt are separate messages.

#### Acceptance Criteria

```gherkin
Given my charging session has been settled
When the session is finalized
Then I receive a text with the total and remaining balance, and an email with the itemized charging summary

Given I bought items at the micro market
When the purchase completes
Then I receive an itemized receipt by email and a text with the total
```

### 14. Account summary in texts

**As a** driver
**I need** to see my balance, year-to-date charging, and in-store spend in each text
**So that** I always know where my account stands

#### Details and Assumptions
* Format is defined in "Text message format". Values: $ with two decimals, kWh with one.
* Values reflect the state after the event that triggered the text.
* Summary lines are appended after the main message.

#### Acceptance Criteria

```gherkin
Given I am a driver with an active account
When I receive a text from Orchard
Then it ends with my Current Balance, Year to date kWh usage, and In store total spend

Given I have had no sessions or purchases this year
When I receive a text
Then kWh usage and in-store spend show zero, not blank
```

### 15. Manage accounts in the internal dashboard

**As an** Orchard staff member
**I need** a dashboard to view and manage driver accounts, sessions and purchases
**So that** I can support drivers and resolve payment issues

#### Details and Assumptions
* Search by phone, email, or name.
* Set or change user type (public/fleet) and change an account's phone number.
* View the ledger, sessions, door taps, purchases, declined taps, and failed payments.
* Adjust or refund the balance (recorded as ledger entries with a reason).
* Suspend or reactivate an account.

#### Acceptance Criteria

```gherkin
Given I am a signed-in staff member
When I change an account's user type from public to fleet
Then future fleet pricing applies to that account

Given a settlement left a shortfall
When I open the dashboard
Then the account and session are flagged with the amount owed
```

### 16. Text opt-out

**As a** driver
**I need** to be able to stop text messages
**So that** I control how Orchard contacts me

#### Acceptance Criteria

```gherkin
Given I receive a text from Orchard
When I reply "STOP"
Then I receive no further texts and the dashboard shows my opt-out
```
