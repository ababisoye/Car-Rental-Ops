from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import Booking, Customer, Expense, Partner, PartnerAgreement, Payment, Vehicle
from app.services import booking_payment_totals, calculate_booking_total, dashboard_metrics, has_vehicle_booking_conflict


def future_window(days=40, hours=0, duration_hours=4):
    local_now = datetime.now(ZoneInfo("Africa/Lagos"))
    pickup = (local_now + timedelta(days=days)).replace(minute=0, second=0, microsecond=0) + timedelta(hours=hours)
    return_at = pickup + timedelta(hours=duration_hours)
    return pickup.strftime("%Y-%m-%dT%H:%M"), return_at.strftime("%Y-%m-%dT%H:%M")


def create_customer(client, name="Ada Customer"):
    return client.post("/customers/new", data={
        "name": name, "email": "ada@example.test", "phone": "+2348000000200",
        "address": "Synthetic address", "notes": "Synthetic test record", "is_active": "y",
    }, follow_redirects=True)


def create_booking_vehicle(client, partner_id=None, **overrides):
    values = {
        "internal_id": "BOOKING-CAR-1", "registration_number": "BK 100 AA",
        "make": "Toyota", "model": "Corolla", "year": "2022", "color": "Gray",
        "transmission": "automatic", "fuel_type": "petrol",
        "ownership_type": "partner_owned" if partner_id else "company_owned",
        "partner_id": str(partner_id or 0), "daily_rate": "35000.00",
        "operational_status": "operational", "notes": "",
    }
    values.update(overrides)
    return client.post("/vehicles/new", data=values, follow_redirects=True)


def booking_values(app, customer_id, vehicle_id, pickup_local, return_local, **overrides):
    values = {
        "customer_id": str(customer_id), "vehicle_id": str(vehicle_id),
        "pickup_local": pickup_local, "return_local": return_local,
        "agreed_daily_rate": "35000.00", "additional_charges": "5000.00",
        "discount_amount": "3000.00", "initial_status": "confirmed", "notes": "Synthetic reservation",
        "payment_confirmed": "y", "initial_payment_amount": "1000.00",
        "initial_payment_method": "cash", "initial_payment_reference": "SYN-INITIAL",
        "initial_payment_local": transaction_date_local(),
    }
    values.update(overrides)
    return values


def customer_and_vehicle_ids(app):
    with app.app_context():
        return Customer.query.one().id, Vehicle.query.one().id


def create_confirmed_booking(client, app, status="confirmed"):
    create_customer(client)
    create_booking_vehicle(client)
    customer_id, vehicle_id = customer_and_vehicle_ids(app)
    pickup, returned = future_window()
    response = client.post("/bookings/new", data=booking_values(
        app, customer_id, vehicle_id, pickup, returned, initial_status=status,
    ))
    assert response.status_code == 302
    with app.app_context():
        return Booking.query.one().id


def transaction_date_local():
    return datetime.now(ZoneInfo("Africa/Lagos")).strftime("%Y-%m-%dT%H:%M")


def test_customer_create_edit_and_booking_history(client, app):
    created = create_customer(client)
    assert created.status_code == 200
    assert b"Ada Customer" in created.data
    with app.app_context():
        customer = Customer.query.one()
        customer_id = customer.id
        assert customer.email == "ada@example.test"
    edited = client.post(f"/customers/{customer_id}/edit", data={
        "name": "Ada Updated", "email": "", "phone": "", "address": "", "notes": "Updated", "is_active": "y",
    }, follow_redirects=True)
    assert edited.status_code == 200
    assert b"Ada Updated" in edited.data
    with app.app_context():
        assert db.session.get(Customer, customer_id).name == "Ada Updated"


def test_booking_creation_snapshots_price_and_partner_agreement(client, app):
    create_customer(client)
    client.post("/partners/new", data={
        "name": "Synthetic Owner", "business_name": "Synthetic Cars", "email": "owner@example.test",
        "phone": "", "address": "", "notes": "", "is_active": "y",
    })
    with app.app_context():
        partner_id = Partner.query.one().id
    pickup, returned = future_window(duration_hours=50)
    client.post(f"/partners/{partner_id}/agreements", data={
        "agreement_type": "percentage", "fixed_payout": "", "revenue_share_percentage": "62.50",
        "effective_from": pickup[:10], "effective_to": "", "notes": "Synthetic booking term",
    })
    create_booking_vehicle(client, partner_id=partner_id)
    customer_id, vehicle_id = customer_and_vehicle_ids(app)
    response = client.post("/bookings/new", data=booking_values(app, customer_id, vehicle_id, pickup, returned), follow_redirects=True)
    assert response.status_code == 200
    assert b"Booking BKG-" in response.data
    assert b"107,000.00" in response.data
    with app.app_context():
        booking = Booking.query.one()
        assert booking.status == "confirmed"
        assert booking.billable_days == 3
        assert booking.agreed_daily_rate == Decimal("35000.00")
        assert booking.expected_rental_total == Decimal("107000.00")
        assert booking.partner_agreement_type_snapshot == "percentage"
        assert booking.partner_share_percentage_snapshot == Decimal("62.50")
        assert booking.partner_percentage_basis_snapshot == "gross_rental"
        assert booking_payment_totals(booking) == (Decimal("1000.00"), Decimal("0.00"), Decimal("106000.00"))
        assert Payment.query.filter_by(booking_id=booking.id, transaction_type="payment").count() == 1
        # Later changes to the vehicle default do not change the saved agreement.
        booking.vehicle.daily_rate = Decimal("50000.00")
        db.session.commit()
        assert booking.agreed_daily_rate == Decimal("35000.00")


def test_booking_rejects_invalid_dates_and_negative_or_excess_discounts(client, app):
    create_customer(client)
    create_booking_vehicle(client)
    customer_id, vehicle_id = customer_and_vehicle_ids(app)
    pickup, returned = future_window()
    response = client.post("/bookings/new", data=booking_values(
        app, customer_id, vehicle_id, returned, pickup,
    ))
    assert response.status_code == 200
    assert b"Return time must be later" in response.data
    response = client.post("/bookings/new", data=booking_values(
        app, customer_id, vehicle_id, pickup, returned, discount_amount="999999.00",
    ))
    assert response.status_code == 200
    assert b"Discount cannot exceed" in response.data
    with app.app_context():
        assert Booking.query.count() == 0


def test_confirmed_overlap_is_rejected_pending_does_not_reserve_and_cancel_releases(client, app):
    create_customer(client)
    create_booking_vehicle(client)
    customer_id, vehicle_id = customer_and_vehicle_ids(app)
    pickup, returned = future_window(duration_hours=48)
    first = client.post("/bookings/new", data=booking_values(app, customer_id, vehicle_id, pickup, returned))
    assert first.status_code == 302
    pending = client.post("/bookings/new", data=booking_values(
        app, customer_id, vehicle_id, pickup, returned, initial_status="pending",
    ))
    assert pending.status_code == 302
    with app.app_context():
        first_id, pending_id = [item.id for item in Booking.query.order_by(Booking.id).all()]
    conflict = client.post(f"/bookings/{pending_id}/confirm", data={}, follow_redirects=True)
    assert b"Another confirmed or active booking now overlaps" in conflict.data
    client.post(f"/bookings/{first_id}/cancel", data={})
    confirmation = client.post(f"/bookings/{pending_id}/confirm", data={})
    assert confirmation.status_code == 302
    with app.app_context():
        assert db.session.get(Booking, pending_id).status == "confirmed"
        assert db.session.get(Booking, first_id).status == "cancelled"


def test_adjacent_booking_boundary_is_allowed_and_lifecycle_records_pickup_return(client, app):
    create_customer(client)
    create_booking_vehicle(client)
    customer_id, vehicle_id = customer_and_vehicle_ids(app)
    pickup, returned = future_window(duration_hours=24)
    first = client.post("/bookings/new", data=booking_values(app, customer_id, vehicle_id, pickup, returned))
    assert first.status_code == 302
    next_pickup = returned
    next_return = (datetime.strptime(returned, "%Y-%m-%dT%H:%M") + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
    adjacent = client.post("/bookings/new", data=booking_values(
        app, customer_id, vehicle_id, next_pickup, next_return,
    ))
    assert adjacent.status_code == 302
    with app.app_context():
        first_id, second_id = [item.id for item in Booking.query.order_by(Booking.id).all()]
    early_pickup = client.post(f"/bookings/{first_id}/pickup", data={}, follow_redirects=True)
    assert b"Pickup cannot be recorded before the scheduled pickup time" in early_pickup.data
    with app.app_context():
        first_booking = db.session.get(Booking, first_id)
        assert first_booking.status == "confirmed"
        assert first_booking.actual_pickup_at is None
        now = datetime.now(timezone.utc)
        first_booking.pickup_at = now - timedelta(minutes=1)
        first_booking.return_at = now + timedelta(hours=23)
        db.session.commit()
    client.post(f"/bookings/{first_id}/pickup", data={})
    with app.app_context():
        active = db.session.get(Booking, first_id)
        assert active.status == "active"
        assert active.actual_pickup_at is not None
    client.post(f"/bookings/{first_id}/return", data={})
    with app.app_context():
        completed = db.session.get(Booking, first_id)
        assert completed.status == "completed"
        assert completed.actual_return_at is not None
        assert db.session.get(Booking, second_id).status == "confirmed"


def test_decimal_booking_calculation_uses_ceiling_days_and_rejects_negative_total():
    start = datetime(2027, 1, 1, tzinfo=ZoneInfo("UTC"))
    end = start + timedelta(hours=24)
    days, total = calculate_booking_total(start, end, Decimal("10.10"), Decimal("0.20"), Decimal("0.10"))
    assert days == 1
    assert total == Decimal("10.20")
    try:
        calculate_booking_total(start, end, Decimal("5.00"), Decimal("0.00"), Decimal("6.00"))
    except ValueError as error:
        assert "Discount cannot exceed" in str(error)
    else:
        raise AssertionError("a negative rental total must be rejected")


def test_overdue_active_booking_keeps_vehicle_unavailable(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        customer = Customer(name="Synthetic Overdue Customer")
        vehicle = Vehicle(
            internal_id="OVERDUE-1", registration_number="OD 100 AA", make="Example", model="Sedan",
            year=2022, ownership_type="company_owned", daily_rate=Decimal("10000.00"), operational_status="operational",
        )
        db.session.add_all([customer, vehicle])
        db.session.flush()
        booking = Booking(
            booking_reference="BKG-OVERDUE-TEST", customer_id=customer.id, vehicle_id=vehicle.id,
            status="active", pickup_at=now - timedelta(days=2), return_at=now - timedelta(hours=1),
            actual_pickup_at=now - timedelta(days=2), agreed_daily_rate=Decimal("10000.00"), billable_days=2,
            additional_charges=Decimal("0.00"), discount_amount=Decimal("0.00"), expected_rental_total=Decimal("20000.00"),
        )
        db.session.add(booking)
        db.session.commit()
        metrics = dashboard_metrics()
        assert metrics["active_bookings"] == 1
        assert metrics["overdue_returns"] == 1
        assert metrics["available_vehicles"] == 0
        assert has_vehicle_booking_conflict(vehicle.id, now + timedelta(days=3), now + timedelta(days=4)) is True


def test_active_booking_allows_future_reservation_after_scheduled_return(app):
    now = datetime.now(timezone.utc)
    with app.app_context():
        customer = Customer(name="Synthetic Current Rental Customer")
        vehicle = Vehicle(
            internal_id="ACTIVE-1", registration_number="AC 100 AA", make="Example", model="Sedan",
            year=2022, ownership_type="company_owned", daily_rate=Decimal("10000.00"), operational_status="operational",
        )
        db.session.add_all([customer, vehicle])
        db.session.flush()
        db.session.add(Booking(
            booking_reference="BKG-ACTIVE-TEST", customer_id=customer.id, vehicle_id=vehicle.id,
            status="active", pickup_at=now - timedelta(days=1), return_at=now + timedelta(days=5),
            actual_pickup_at=now - timedelta(days=1), agreed_daily_rate=Decimal("10000.00"), billable_days=1,
            additional_charges=Decimal("0.00"), discount_amount=Decimal("0.00"), expected_rental_total=Decimal("10000.00"),
        ))
        db.session.commit()
        assert has_vehicle_booking_conflict(vehicle.id, now + timedelta(days=1), now + timedelta(days=2)) is True
        assert has_vehicle_booking_conflict(vehicle.id, now + timedelta(days=6), now + timedelta(days=7)) is False


def test_payments_refunds_and_customer_balance_are_decimal_and_auditable(client, app):
    booking_id = create_confirmed_booking(client, app)
    payment = client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "payment", "amount": "10000.00", "payment_method": "bank_transfer",
        "reference": "SYN-PAY-001", "transacted_local": transaction_date_local(), "notes": "Synthetic deposit",
    }, follow_redirects=True)
    assert payment.status_code == 200
    assert b"Financial transaction recorded" in payment.data
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.expected_rental_total == Decimal("37000.00")
        assert booking_payment_totals(booking) == (Decimal("11000.00"), Decimal("0.00"), Decimal("26000.00"))

    overpayment = client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "payment", "amount": "30000.00", "payment_method": "cash",
        "reference": "", "transacted_local": transaction_date_local(), "notes": "",
    })
    assert overpayment.status_code == 400
    assert b"Payment cannot exceed" in overpayment.data

    refund = client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "refund", "amount": "2500.00", "payment_method": "cash",
        "reference": "SYN-REF-001", "transacted_local": transaction_date_local(), "notes": "Synthetic adjustment",
    }, follow_redirects=True)
    assert refund.status_code == 200
    too_much_refund = client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "refund", "amount": "9000.00", "payment_method": "cash",
        "reference": "", "transacted_local": transaction_date_local(), "notes": "",
    })
    assert too_much_refund.status_code == 400
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking_payment_totals(booking) == (Decimal("11000.00"), Decimal("2500.00"), Decimal("28500.00"))
        assert Payment.query.count() == 3
        assert dashboard_metrics()["outstanding_customer_balance"] == Decimal("28500.00")


def test_cancelled_booking_has_no_outstanding_balance_but_can_be_refunded(client, app):
    booking_id = create_confirmed_booking(client, app)
    client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "payment", "amount": "5000.00", "payment_method": "cash",
        "reference": "", "transacted_local": transaction_date_local(), "notes": "",
    })
    client.post(f"/bookings/{booking_id}/cancel", data={})
    response = client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "refund", "amount": "5000.00", "payment_method": "bank_transfer",
        "reference": "SYN-CANCEL-REFUND", "transacted_local": transaction_date_local(), "notes": "Cancelled booking refund",
    }, follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking_payment_totals(booking)[2] == Decimal("0.00")
        assert dashboard_metrics()["outstanding_customer_balance"] == Decimal("0.00")
        assert Payment.query.filter_by(transaction_type="refund").count() == 1


def test_booking_requires_payment_confirmation_and_records_initial_payment_atomically(client, app):
    create_customer(client)
    create_booking_vehicle(client)
    customer_id, vehicle_id = customer_and_vehicle_ids(app)
    pickup, returned = future_window()
    values = booking_values(app, customer_id, vehicle_id, pickup, returned)
    values.pop("payment_confirmed")
    rejected = client.post("/bookings/new", data=values)
    assert rejected.status_code == 200
    assert b"Confirm payment has been received" in rejected.data
    with app.app_context():
        assert Booking.query.count() == 0
        assert Payment.query.count() == 0

    values = booking_values(app, customer_id, vehicle_id, pickup, returned, initial_payment_amount="37000.00")
    created = client.post("/bookings/new", data=values, follow_redirects=True)
    assert created.status_code == 200
    assert b"initial payment recorded" in created.data
    assert b"Outstanding customer balance" in created.data
    assert b"0.00" in created.data
    with app.app_context():
        booking = Booking.query.one()
        assert booking_payment_totals(booking) == (Decimal("37000.00"), Decimal("0.00"), Decimal("0.00"))
        assert Payment.query.filter_by(booking_id=booking.id).count() == 1


def test_vehicle_expenses_are_recorded_and_preserved(client, app):
    create_booking_vehicle(client)
    with app.app_context():
        vehicle_id = Vehicle.query.one().id
    response = client.post("/expenses/new", data={
        "vehicle_id": str(vehicle_id), "category": "maintenance", "amount": "12500.50",
        "payment_method": "cash", "reference": "SYN-EXP-001", "incurred_local": transaction_date_local(),
        "notes": "Synthetic service visit",
    }, follow_redirects=True)
    assert response.status_code == 200
    assert b"Vehicle expense recorded" in response.data
    page = client.get("/expenses?category=maintenance")
    assert b"12,500.50" in page.data
    with app.app_context():
        expense = Expense.query.one()
        assert expense.amount == Decimal("12500.50")
        assert expense.vehicle.internal_id == "BOOKING-CAR-1"
