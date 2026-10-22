# Car Rental Operations Management System

A local-first operations system for a rental business managing company-owned and partner-owned vehicles. Implemented so far: the Flask application foundation, database-backed dashboard, partner registry and agreement records, fleet CRUD/search/filtering, customer registry, reservation workflow, payment and expense ledgers, partner settlements, date-filtered reports, CSV export, validation, and synthetic demo data. Authentication and authorization are not implemented yet.

## Requirements

- macOS on Apple Silicon or Intel
- Python 3.9 or newer (Python 3.11 or newer is recommended)
- No cloud account, paid API, or external service is needed

## Setup

From this project directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The default configuration uses a local SQLite database at `instance/car_rental.sqlite3` and displays booking times in `Africa/Lagos`. To override settings, export `DATABASE_URL`, `SECRET_KEY`, or `LOCAL_TIMEZONE` in your shell before starting the app. Do not commit `.env` files or real secrets.

Apply the included database migrations:

```sh
flask --app wsgi:app db upgrade
```

Migrations are stored in `migrations/`. Back up the local database before applying future schema changes. For new model changes, generate a migration with `flask --app wsgi:app db migrate -m "describe change"`, inspect the generated file, then apply it with `flask --app wsgi:app db upgrade`. Do not delete the database to make schema changes.

Load synthetic development records (safe to repeat; existing records are not overwritten):

```sh
flask --app wsgi:app seed-demo
```

The seed command creates one synthetic partner with explicitly labeled percentage-based demo agreement terms and two synthetic vehicles. No real contact information or external communications are used.

## Run

```sh
flask --app wsgi:app run --host 127.0.0.1 --port 5001
```

Port 5001 avoids the macOS Control Center process, which may already use port 5000. Open <http://127.0.0.1:5001/> for the local landing page or <http://127.0.0.1:5001/health> for the database health check. Stop the server with Ctrl-C.

## Tests

```sh
pytest
```

Tests use a separate in-memory SQLite database and synthetic records.

## Database backup and reset

Stop the application before copying the database:

```sh
cp instance/car_rental.sqlite3 "instance/car_rental-backup-$(date +%Y%m%d-%H%M%S).sqlite3"
```

To reset this local database, stop the application, back up the file if it contains anything you need, then remove `instance/car_rental.sqlite3` and run `flask --app wsgi:app db upgrade`. Run `flask --app wsgi:app seed-demo` afterward if you want synthetic starter records. This reset is manual and never runs on application startup.

## Implemented behavior

- Dashboard counts are derived from persisted fleet and booking records. Total registered vehicles includes archived records; available vehicles are operational, unarchived, and not in a currently active or scheduled confirmed rental.
- Vehicle records include ownership, partner association, registration, make/model/year, optional specifications, a positive decimal daily rental rate, operational status, notes, and archive state. Enter `150000` for ₦150,000; the app formats displayed amounts with separators.
- Registration numbers are stored uppercase with normalized whitespace and protected by a unique database constraint.
- Partner records can be edited and archived. Archive actions preserve linked vehicle and agreement history.
- Agreement records support a fixed payout per rental or a percentage of gross rental, with effective dates. Percentage basis is explicitly stored as gross rental. Settlement calculations use the booking's captured agreement terms.
- All state-changing HTML forms use Flask-WTF CSRF tokens. SQLite foreign keys are enabled for local connections.
- Customers can be created, edited, archived by marking inactive, and viewed with their booking history. Identity documents and sensitive personal data are not collected.
- Reservations preserve the agreed daily rate, billable days, additional charges, discount, expected total, and any partner agreement effective on the pickup date.
- Partner-owned bookings also preserve the partner identity from booking creation, so later vehicle ownership edits do not redirect historical settlements.
- Booking creation requires the operator to confirm that payment was received and verified, and records the amount, method, reference, and transaction time atomically with the booking. A partial deposit is allowed and the remaining balance stays outstanding. This is an internal ledger entry, not payment processing.
- Reservation times are entered in the configured local timezone and stored as UTC. The default is `Africa/Lagos`.
- Rental time is billed in 24-hour units rounded up, with a one-day minimum. Expected rental total is `(agreed daily rate × billable days) + additional charges − discount`; the total cannot be negative.
- Pending bookings do not reserve a vehicle. Confirmed bookings reserve the half-open interval `[pickup, return)`, so a booking can begin exactly when another ends. An active rental blocks reservations that overlap its scheduled return; if it becomes overdue, it blocks later reservations until the return is recorded.
- A booking remains confirmed until its scheduled pickup time. Pickup cannot be recorded early; recording pickup at or after that time moves it to active.
- A partner booking with no agreement effective on its pickup date records no payout terms. The system does not infer a partner rate.
- Customer payments and refunds are decimal-safe ledger entries. Payments cannot exceed the booking balance, refunds cannot exceed net recorded payments, and cancellation removes the customer balance while leaving any unrefunded amount available to refund. Entries have no edit/delete workflow.
- Vehicle operating expenses are recorded separately and are not automatically deducted from a partner share.
- Settlements can be created once per completed partner booking with captured agreement terms. Gross rental is the booking expected total after discount and including additional charges. Percentage agreements use that gross amount less recorded refunds and only explicitly approved expense deductions. Fixed payouts remain fixed unless a partner credit/debit is explicitly recorded. Company share is shown after listed adjustments and is not labeled as profit.
- Customer refunds recorded after settlement are added automatically to settlement adjustment history. Partner payments are append-only and cannot exceed the current outstanding liability; over-settlement is shown if a later adjustment reduces the liability.
- Reports filter by local date range and distinguish booked rental amounts, recorded customer receipts, refunds, vehicle operating expenses, outstanding customer balances, and partner liabilities. CSV export quotes fields and prefixes formula-like values to reduce spreadsheet injection risk.

## Safety and scope

This local development version is not ready for public internet exposure or real company data. Before that, it needs a security review, authentication and authorization, backups, and a deployment plan. Debug mode is disabled by default, and the development server binds to localhost in the documented command. No payment processing or customer messaging is included.
