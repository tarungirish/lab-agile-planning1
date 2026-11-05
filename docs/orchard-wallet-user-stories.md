# Orchard Wallet: EV Charging + Club Access

## Product summary

Drivers add an **Orchard card** to Apple Wallet or Google Wallet. There is no mobile app. When a driver taps the card at a charger, the charging session starts and the driver gets a **text message** asking whether they want club (lounge) access. Replying **YES** adds a club fee to the session. The payment method on file is charged **only after the charging session completes**.

| User type | Club fee |
|-----------|----------|
| Public    | $5       |
| Fleet     | $3       |

Each account is identified by the driver's **mobile phone number** (one number, one account). Accounts are managed by Orchard staff through an **internal dashboard**. All driver communication is by text (and email for receipts).

**Charging partner:** Electric Era. Charging hardware, tap detection, and session events depend on what Electric Era can provide (see open questions).

## Core flow

1. Driver taps the Orchard card (wallet pass) at the charger.
2. Backend identifies the account and starts the charging session.
3. Driver receives a text: "Would you like to use the club? Reply YES for $X."
4. Driver replies YES (club added) or NO / no reply (no club).
5. Charging session completes.
6. Payment method on file is charged for charging plus the club fee, if accepted.
7. Driver receives a text and email receipt.

## Open questions

* Electric Era: can their chargers read an NFC wallet pass, or only RFID cards/their app? If not, the Orchard card may need to be a credential they already support.
* Electric Era: do they provide webhooks/API for session started and session completed (driver ID, charger ID, energy delivered)?
* Electric Era: who bills the driver for the electricity (them or Orchard)? Orchard may only bill the club fee.
* Electric Era: can we reuse their existing fleet setup (e.g. WEX) to identify fleet drivers?
* Which Electric Era sites have the club? Offers should only go out for taps at those chargers.
* Payments provider (e.g. Stripe) and how a payment method is captured at pass issuance.
* How the club verifies access at the door (QR on pass, staff lookup, etc.).
* How fleet status is determined (fleet code, fleet account) and who is billed for the fleet fee.
* Behavior if the driver replies YES after the session has ended, or never replies.
* Does the club offer expire after a time window, and is the fee per visit or per session?
* Text opt-in/opt-out compliance (TCPA) and STOP handling.

---

## Stories

### 1. Add Orchard card to wallet

**As a** driver
**I need** to add an Orchard card to my Apple or Google Wallet
**So that** I can tap to start charging without installing an app

#### Details and Assumptions
* Issued from a web signup page; collects name, mobile number, email, payment method, and optional fleet code.
* The phone number is the account identifier and must be unique. It is verified with a one-time text code before the pass is issued.
* The pass contains an identifier only, no card data.

#### Acceptance Criteria

```gherkin
Given I have completed signup with a valid phone number and payment method
When I choose "Add to Apple Wallet" or "Add to Google Wallet"
Then the Orchard card appears in my wallet and my account is active
```

### 2. Tap to start charging

**As a** driver
**I need** to tap my Orchard card on a charger
**So that** my charging session starts and is linked to my account

#### Details and Assumptions
* Backend receives a "pass tapped at charger X" event (via Electric Era) and looks up the account by pass ID / phone number.
* Unknown or suspended accounts do not start a session.

#### Acceptance Criteria

```gherkin
Given I have an active Orchard account
When I tap my card at an available charger
Then a charging session starts and is associated with my account
```

### 3. Club offer by text

**As a** driver
**I need** to be asked by text whether I want club access
**So that** I can add the lounge to my visit without an app

#### Details and Assumptions
* Text is sent to the account's verified phone number within seconds of the tap.
* Replies are matched to the account by the sender's phone number.
* Message states the price for the driver's user type.
* Public = $5, Fleet = $3.

#### Acceptance Criteria

```gherkin
Given my charging session has started
When the system sends me the club offer
Then I receive a text stating the club price for my user type

Given I am a public user
When I receive the offer
Then the price shown is $5

Given I am a fleet user
When I receive the offer
Then the price shown is $3
```

### 4. Accept or decline the club offer

**As a** driver
**I need** to reply YES or NO to the offer
**So that** I only pay for the club if I want it

#### Details and Assumptions
* No reply is treated as a decline.
* Replies are case-insensitive; unrecognized replies get a help message.
* No charge is made at the time of reply.

#### Acceptance Criteria

```gherkin
Given I received a club offer for my active session
When I reply "YES"
Then the club fee is added to my session and I receive a confirmation text

Given I received a club offer
When I reply "NO" or do not reply
Then no club fee is added to my session
```

### 5. Charge after the session completes

**As a** driver
**I need** to be charged only after my charging session ends
**So that** I pay for what I actually used

#### Details and Assumptions
* Charge uses the payment method tied to the wallet account.
* Total = charging cost + club fee (if accepted).
* Failed payments are flagged on the dashboard for follow-up.

#### Acceptance Criteria

```gherkin
Given my charging session has completed
When the system processes the payment
Then my payment method is charged once for the session total, including the club fee if I accepted

Given my session is still in progress
When I have accepted the club offer
Then my payment method has not been charged yet
```

### 6. Receipt by text and email

**As a** driver
**I need** a receipt after my session
**So that** I know what I was charged

#### Acceptance Criteria

```gherkin
Given my payment has been processed
When the session is finalized
Then I receive a text with the total and an email with an itemized receipt
```

### 7. Manage accounts in the internal dashboard

**As an** Orchard staff member
**I need** a dashboard to view and manage driver accounts and sessions
**So that** I can support drivers, set user type (public/fleet), and resolve payment issues

#### Details and Assumptions
* Search by phone, email, or name.
* Set or change user type, which drives the club price.
* View sessions, club acceptances, charges, and failed payments.
* Suspend or reactivate an account.

#### Acceptance Criteria

```gherkin
Given I am a signed-in staff member
When I change an account's user type from public to fleet
Then future club offers for that account are priced at $3

Given a payment has failed
When I open the dashboard
Then the failed payment is visible with the account and session details
```

### 8. Text opt-out

**As a** driver
**I need** to be able to stop text messages
**So that** I control how Orchard contacts me

#### Acceptance Criteria

```gherkin
Given I receive a text from Orchard
When I reply "STOP"
Then I receive no further texts and the dashboard shows my opt-out
```
