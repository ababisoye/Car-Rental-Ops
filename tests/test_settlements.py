from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import Booking, Customer, Expense, Partner, PartnerSettlement, SettlementAdjustment, SettlementPayment, Vehicle
from app.services import settlement_calculation
from tests.test_customers_bookings import booking_values, future_window


def local_now():
    return datetime.now(ZoneInfo("Africa/Lagos")).strftime("%Y-%m-%dT%H:%M")


def complete_partner_booking(client, app, agreement_type="percentage", include_agreement=True):
    client.post("/customers/new", data={
        "name": "Synthetic Settlement Customer", "email": "settle@example.test", "phone": "",
        "address": "", "notes": "", "is_active": "y",
    })
    client.post("/partners/new", data={
        "name": "Synthetic Settlement Owner", "business_name": "Settlement Cars",
        "email": "settlement-owner@example.test", "phone": "", "address": "", "notes": "", "is_active": "y",
    })
    with app.app_context():
        partner_id = Partner.query.one().id
        customer_id = Customer.query.one().id
    pickup, returned = future_window(duration_hours=24)
    if include_agreement:
        agreement_data = {
            "agreement_type": agreement_type,
            "fixed_payout": "15000.00" if agreement_type == "fixed_per_rental" else "",
            "revenue_share_percentage": "62.50" if agreement_type == "percentage" else "",
            "effective_from": pickup[:10], "effective_to": "", "notes": "Synthetic settlement terms",
        }
        client.post(f"/partners/{partner_id}/agreements", data=agreement_data)
    client.post("/vehicles/new", data={
        "internal_id": "SETTLE-CAR-1", "registration_number": "ST 100 AA", "make": "Honda",
        "model": "Civic", "year": "2022", "color": "Black", "transmission": "automatic",
        "fuel_type": "petrol", "ownership_type": "partner_owned", "partner_id": str(partner_id),
        "daily_rate": "35000.00", "operational_status": "operational", "notes": "",
    })
    with app.app_context():
        vehicle_id = Vehicle.query.one().id
    created = client.post("/bookings/new", data=booking_values(
        app, customer_id, vehicle_id, pickup, returned, initial_status="confirmed",
    ))
    assert created.status_code == 302
    with app.app_context():
        booking_id = Booking.query.one().id
        booking = db.session.get(Booking, booking_id)
        now = datetime.now(timezone.utc)
        booking.pickup_at = now - timedelta(minutes=1)
        booking.return_at = now + timedelta(hours=23)
        db.session.commit()
    client.post(f"/bookings/{booking_id}/pickup", data={})
    client.post(f"/bookings/{booking_id}/return", data={})
    return partner_id, vehicle_id, booking_id


def record_customer_refund(client, booking_id, amount):
    return client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "refund", "amount": amount, "payment_method": "bank_transfer",
        "reference": "SYN-REFUND", "transacted_local": local_now(), "notes": "Synthetic customer refund",
    })


def test_percentage_settlement_tracks_refunds_expenses_adjustments_and_payments(client, app):
    partner_id, vehicle_id, booking_id = complete_partner_booking(client, app)
    client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "payment", "amount": "10000.00", "payment_method": "cash",
        "reference": "SYN-DEPOSIT", "transacted_local": local_now(), "notes": "Synthetic deposit",
    })
    record_customer_refund(client, booking_id, "2500.00")
    client.post("/expenses/new", data={
        "vehicle_id": str(vehicle_id), "category": "maintenance", "amount": "1000.00",
        "payment_method": "cash", "reference": "SYN-DED-EXP", "incurred_local": local_now(),
        "notes": "Expense authorized for settlement deduction",
    })
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        expense_id = Expense.query.one().id
        assert booking.expected_rental_total == Decimal("37000.00")
    created = client.post(f"/bookings/{booking_id}/settlement", data={}, follow_redirects=True)
    assert created.status_code == 200
    assert b"Partner settlement created" in created.data
    with app.app_context():
        settlement = PartnerSettlement.query.one()
        settlement_id = settlement.id
        calculations = settlement_calculation(settlement)
        assert calculations["refunds"] == Decimal("2500.00")
        assert calculations["partner_share"] == Decimal("21562.50")
        assert calculations["company_share"] == Decimal("12937.50")
        assert SettlementAdjustment.query.filter_by(adjustment_type="customer_refund").count() == 1

    duplicate = client.post(f"/bookings/{booking_id}/settlement", data={})
    assert duplicate.status_code == 302
    with app.app_context():
        assert PartnerSettlement.query.count() == 1

    adjustment = client.post(f"/settlements/{settlement_id}/adjustments", data={
        "adjustment_type": "expense_deduction", "expense_id": str(expense_id), "amount": "1000.00",
        "effective_local": local_now(), "note": "Partner-approved maintenance deduction",
    }, follow_redirects=True)
    assert adjustment.status_code == 200
    with app.app_context():
        settlement = db.session.get(PartnerSettlement, settlement_id)
        calculations = settlement_calculation(settlement)
        assert calculations["payout_base"] == Decimal("33500.00")
        assert calculations["partner_share"] == Decimal("20937.50")
        assert calculations["company_share"] == Decimal("12562.50")

    payment = client.post(f"/settlements/{settlement_id}/payments", data={
        "amount": "10000.00", "payment_method": "bank_transfer", "reference": "SYN-SETTLE-1",
        "settled_local": local_now(), "notes": "Synthetic partial settlement",
    }, follow_redirects=True)
    assert payment.status_code == 200
    with app.app_context():
        settlement = db.session.get(PartnerSettlement, settlement_id)
        assert settlement_calculation(settlement)["outstanding"] == Decimal("10937.50")
        assert SettlementPayment.query.count() == 1

    record_customer_refund(client, booking_id, "500.00")
    with app.app_context():
        settlement = db.session.get(PartnerSettlement, settlement_id)
        calculations = settlement_calculation(settlement)
        assert calculations["refunds"] == Decimal("3000.00")
        assert calculations["partner_share"] == Decimal("20625.00")
        assert calculations["settled"] == Decimal("10000.00")
        assert calculations["outstanding"] == Decimal("10625.00")


def test_settlement_payment_cannot_exceed_outstanding_and_expense_cannot_be_deducted_twice(client, app):
    _partner_id, vehicle_id, booking_id = complete_partner_booking(client, app)
    client.post("/expenses/new", data={
        "vehicle_id": str(vehicle_id), "category": "repair", "amount": "2000.00",
        "payment_method": "cash", "reference": "SYN-EXP", "incurred_local": local_now(), "notes": "Synthetic",
    })
    with app.app_context():
        expense_id = Expense.query.one().id
    client.post(f"/bookings/{booking_id}/settlement", data={})
    with app.app_context():
        settlement_id = PartnerSettlement.query.one().id
    client.post(f"/settlements/{settlement_id}/adjustments", data={
        "adjustment_type": "expense_deduction", "expense_id": str(expense_id), "amount": "1000.00",
        "effective_local": local_now(), "note": "First partial deduction",
    })
    duplicate_deduction = client.post(f"/settlements/{settlement_id}/adjustments", data={
        "adjustment_type": "expense_deduction", "expense_id": str(expense_id), "amount": "1000.00",
        "effective_local": local_now(), "note": "Should be rejected",
    })
    assert duplicate_deduction.status_code == 400
    overpayment = client.post(f"/settlements/{settlement_id}/payments", data={
        "amount": "30000.00", "payment_method": "cash", "reference": "", "settled_local": local_now(), "notes": "",
    })
    assert overpayment.status_code == 400
    assert b"cannot exceed the outstanding" in overpayment.data
    with app.app_context():
        assert SettlementAdjustment.query.filter_by(adjustment_type="expense_deduction").count() == 1
        assert SettlementPayment.query.count() == 0


def test_fixed_per_rental_settlement_does_not_silently_change_for_refunds(client, app):
    _partner_id, _vehicle_id, booking_id = complete_partner_booking(client, app, agreement_type="fixed_per_rental")
    client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "payment", "amount": "5000.00", "payment_method": "cash",
        "reference": "", "transacted_local": local_now(), "notes": "",
    })
    record_customer_refund(client, booking_id, "1000.00")
    client.post(f"/bookings/{booking_id}/settlement", data={})
    with app.app_context():
        settlement = PartnerSettlement.query.one()
        calculation = settlement_calculation(settlement)
        assert calculation["partner_share"] == Decimal("15000.00")
        assert calculation["gross"] == Decimal("37000.00")
        assert calculation["refunds"] == Decimal("1000.00")


def test_partner_booking_without_effective_terms_cannot_create_settlement(client, app):
    _partner_id, _vehicle_id, booking_id = complete_partner_booking(client, app, include_agreement=False)
    response = client.post(f"/bookings/{booking_id}/settlement", data={}, follow_redirects=True)
    assert response.status_code == 200
    assert b"will not invent a payout" in response.data
    with app.app_context():
        assert PartnerSettlement.query.count() == 0


def test_settlement_keeps_original_partner_after_vehicle_ownership_changes(client, app):
    partner_id, vehicle_id, booking_id = complete_partner_booking(client, app)
    changed = client.post(f"/vehicles/{vehicle_id}/edit", data={
        "internal_id": "SETTLE-CAR-1", "registration_number": "ST 100 AA", "make": "Honda",
        "model": "Civic", "year": "2022", "color": "Black", "transmission": "automatic",
        "fuel_type": "petrol", "ownership_type": "company_owned", "partner_id": "0",
        "daily_rate": "35000.00", "operational_status": "operational", "notes": "",
    }, follow_redirects=True)
    assert changed.status_code == 200
    with app.app_context():
        booking = db.session.get(Booking, booking_id)
        assert booking.partner_id_snapshot == partner_id
        assert booking.vehicle.ownership_type == "company_owned"
    created = client.post(f"/bookings/{booking_id}/settlement", data={}, follow_redirects=True)
    assert created.status_code == 200
    assert b"Partner settlement created" in created.data
    with app.app_context():
        settlement = PartnerSettlement.query.one()
        assert settlement.partner_id == partner_id
