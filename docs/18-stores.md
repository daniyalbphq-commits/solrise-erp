# 18 - The store list

The 36 stores, their addresses and their managers, as imported from the operator's
"New Stores info" spreadsheet into `Customer`, `Address` and `Contact` records.

Read alongside [`docs/17-customer-portal.md`](17-customer-portal.md): the portal
resolves a reporting user as Contact → Customer, so it is this data that makes a
report say *which store it came from*.

| | |
|---|---|
| Source | "New Stores info.xlsx", `Sheet1`, 36 rows |
| Committed copy | `fixtures/solrise_erp/stores.csv` (normalised, reviewable) |
| Importer | `scripts/import_stores.py`, wrapped by `scripts/import_stores.sh` |
| Target | `stores` (`make stores`, or `SITE_ENV=aws ./scripts/import_stores.sh`) |
| Stores | 17 ND, 13 UT, 1 AZ, 5 MT |

---

## 1. What a row becomes

```
store            -> Customer          "Solrise 39"  (name = customer_name)
store_code       -> Customer.store_code   "ND-9"    (custom field, created on demand)
street/city/
  region/zip     -> Address           linked to the Customer
manager_name     -> Contact           first/last split, linked to the Customer
manager_phone    -> Contact.phone_nos E.164, e.g. +17019892311
```

Everything is **idempotent**: re-running updates in place, creates only what is
missing, fills only blanks, and never raises. That is what makes it safe to run on
production — and to run again after the spreadsheet is corrected.

Two deliberate choices:

* **A `Customer`, not a new DocType.** The portal already keys a report to
  `Issue.customer`, and `portal/identity.py` resolves the reporter's station
  through the same link. A bespoke "Store" doctype would mean a new link field on
  `Issue` and a rewrite of that resolution for no functional gain. If a dedicated
  store record is wanted later (with the state index as its key), it is a
  follow-up, not a prerequisite.
* **`Customer.store_code` is created by the importer**, not shipped as a fixture,
  because the app's fixture directory does not carry `custom_field.json` — the same
  gap `docs/17` section 9.2 describes for permissions. Creating it from code means
  this data works whether or not the app is installed.

## 2. Two identifiers, not one

Including this because it is easy to conflate:

* **`Solrise 39`** — the operator's company-wide store number. It is the Customer's
  *name*, so it is what the Desk shows and what a report's subject carries.
* **`ND-9`** — the state-scoped index, present only for the 17 North Dakota stores
  in the source sheet (the column is headed "ND").

They do not agree (`Solrise 27` is `ND-1`; `Solrise 39` is `ND-9`), so both are
kept. No `UT-n`/`AZ-n`/`MT-n` values were invented for the states the sheet leaves
blank — a made-up identifier is worse than an absent one.

## 3. Gaps in the source sheet

Surfaced by the importer's dry pass, and left as they are rather than guessed at:

| Gap | Count | Effect |
|---|---|---|
| No manager name, no phone | 5 — Solrise 51-55 (MT) | Customer + Address created; **no Contact** |
| No store code | 19 — every UT/AZ/MT store | `store_code` left blank |
| No ZIP in the address text | 5 — Solrise 51-55 (MT) | `Address.pincode` blank |
| `ZIP 94062` for Solrise 26 | 1 | **Suspicious** — Cedar Hills, UT is 84062. Left as printed |
| No e-mail address | **all 36** | Managers cannot be users — see section 5 |

The MT rows are also only a street and a city ("2007 Blue Creek Rd, Billings");
they have no state or ZIP anywhere in the sheet.

## 4. How to run it

```bash
# local
SITE_ENV=local ./scripts/import_stores.sh      # or: make stores

# production (on the workstation; it reaches the host through the compose wrapper)
cd infra/ansible && ansible-playbook site.yml --tags deploy --ask-vault-pass
#   ...or directly, once the stack is up:
SITE_ENV=aws ./scripts/import_stores.sh
```

The importer reads `STORES_CSV` (default `/tmp/solrise-fixtures/stores.csv`), which
the wrapper stages into the container — the CSV lives on the control host and the
importer runs inside `backend`, the same arrangement as `import_app_fixtures.sh`.

It is **not** wired into the Ansible role yet. The data is stable master data, so a
one-off run is enough; putting it in the role would make every deploy re-assert it.
Say the word if you would rather have that.

## 5. The logins

The managers **are** the customers in this deployment, so each one gets a portal
login: a Website User with the `Customer` role, linked from their Contact, which is
how `portal/identity.py` gets from a session to a store.

**There are 26 logins, not 31**, because `User.mobile_no` is unique and the sheet's
31 manager rows describe 26 people:

| Manager | Phone | Stores |
|---|---|---|
| Tammy Hoeppner | +16085667278 | Solrise 28, 30, 31, 42 |
| Weldon, Jessica N. | +17014256733 | Solrise 45, 46 |
| Mary Johnson | +17017215126 | Solrise 27, 29 |

Those people see a **store picker** on the report form; a single-store manager sees
the same one-tap screen as before (docs/17 section 11).

### 5.1 Signing in without an e-mail address

The sheet has no e-mail addresses, and a Frappe user *is* an e-mail address, so:

* each login's ID is a **placeholder** - `brett.van.dam@stores.invalid`. `.invalid`
  is reserved by RFC 2606 and can never resolve, so a placeholder cannot reach a real
  stranger by accident. Guessing `first.last@solrisestores.com` could;
* two alternative sign-in forms are switched **on** in System Settings, because the
  placeholder cannot be typed and cannot receive anything.

A manager can sign in with any of these:

| Type | Example | Notes |
|---|---|---|
| **Username** | `6085667278` | Their number in **national digits** - no `+`, no country code. The easiest on a phone keypad, so this is the one to document |
| Mobile number | `+16085667278` | The stored `mobile_no`, which must match **exactly** |
| Placeholder e-mail | `tammy.hoeppner@stores.invalid` | Works, but nothing can be sent to it |

**Why the username is the digits.** Frappe's login page has no country-code picker
(`login.py` honours `allow_login_using_mobile_number`, but the form is a plain text
field), so the stored `+1` form asks a user to find and hold `+` on a keypad. The
first hand-typed attempt at this dropped a digit - `+1608566727` instead of
`+16085667278` - and was rejected. The username carries the same number in the form
a phone keyboard produces without effort, and `mobile_no` keeps its proper E.164
value for data quality.

### 5.2 Passwords, and the trap that has no workaround yet

`STORES_DEFAULT_PASSWORD` supplies the initial password. With it unset the importer
creates **no** logins rather than half-creating accounts nobody can use:

```bash
STORES_DEFAULT_PASSWORD='...' SITE_ENV=aws ./scripts/import_stores.sh
```

Two consequences, worth stating plainly:

* **one shared password across 26 accounts** until each is changed - fine for a
  rehearsal, not for a rollout;
* **a forgotten password must be reset in the Desk**, because nothing can be mailed
  to a `.invalid` address, so "Forgot password" cannot work yet.

### 5.3 What would make this production-grade

Filling the CSV's `email` column is the whole fix: the next run replaces the
placeholder as the login ID, "Forgot password" starts working, and the username
becomes a convenience instead of the credential. Ask each store for one address.

Until then the practical rollout is to **sign each phone in once**: the session
cookie lasts 63 days, so a manager who never signs out is not typing anything after
day one.

## 6. What was verified

Run against a live local site, not just compiled:

| Check | Result |
|---|---|
| First run | 36 customers, 36 addresses, 31 contacts created; `Customer.store_code` created |
| Second run | 0 created, 31 phones backfilled (see below) |
| Third run | complete no-op |
| Spot check | `Solrise 39` → `ND-9`, `808 Boundary RD NW`, Mandan ND 58554, Brett Van Dam +17019892311 |
| Spot check | `Solrise 48` (AZ) → `7180 US 89`, Flagstaff AZ 86004 |
| Spot check | `Solrise 53` (MT) → `3443 Central Ave`, Billings MT, no ZIP, no Contact |
| Logins | 26 created; `+17019892311` signs in as *Brett Van Dam* and lands on `/start` |
| Multi-store | Tammy Hoeppner signs in; `/start` says "Your 4 stores" and the form offers all 4 |
| Multi-store, **wrong** store | posting `Solrise 39` as Tammy is **refused** (`ValidationError`) — no report is filed |
| Single store | Brett's form has no picker; a report with no store is attributed to `Solrise 39` |

**The `phone_nos` trap, found by that second run.** `Contact.phone` and
`Contact.mobile_no` are `read_only` fields *derived* from the `phone_nos` child
table. Assigning them directly is accepted without error and then silently
discarded, so the first import stored 36 stores and **zero phone numbers** while
reporting complete success. Numbers now go on `phone_nos`
(`is_primary_phone` + `is_primary_mobile_no`), and the importer backfills a contact
that is missing one — which is why the second run did work rather than nothing.
