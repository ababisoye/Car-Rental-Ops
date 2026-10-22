from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import Booking, Customer, Vehicle
from app.services import build_operational_report
from tests.test_customers_bookings import booking_values, create_booking_vehicle, future_window
from tests.test_settlements import complete_partner_booking


def local_now():
    return datetime.now(ZoneInfo("Africa/Lagos")).strftime("%Y-%m-%dT%H:%M")


def test_report_page_has_empty_state_and_rejects_reverse_date_range(client):
    response = client.get("/reports?start=2026-01-01&end=2026-01-31")
    assert response.status_code == 200
    assert b"No non-cancelled bookings have pickups" in response.data
    invalid = client.get("/reports?start=2026-02-01&end=2026-01-01")
    assert invalid.status_code == 400
    assert b"Request could not be accepted" in invalid.data


def test_operational_report_separates_bookings_receipts_expenses_and_balance(client, app):
    client.post("/customers/new", data={
        "name": "Report Customer", "email": "report@example.test", "phone": "",
        "address": "", "notes": "", "is_active": "y",
    })
    create_booking_vehicle(client)
    with app.app_context():
        customer_id = Customer.query.one().id
        vehicle_id = Vehicle.query.one().id
    pickup, returned = future_window(duration_hours=4)
    client.post("/bookings/new", data=booking_values(app, customer_id, vehicle_id, pickup, returned))
    with app.app_context():
        booking_id = Booking.query.one().id
    client.post(f"/bookings/{booking_id}/payments", data={
        "transaction_type": "payment", "amount": "10000.00", "payment_method": "cash",
        "reference": "SYN-REPORT", "transacted_local": local_now(), "notes": "",
    })
    client.post("/expenses/new", data={
        "vehicle_id": str(vehicle_id), "category": "fuel", "amount": "500.00",
        "payment_method": "cash", "reference": "SYN-REPORT-EXP", "incurred_local": local_now(), "notes": "",
    })
    today = datetime.now(ZoneInfo("Africa/Lagos")).date()
    pickup_date = datetime.strptime(pickup, "%Y-%m-%dT%H:%M").date()
    response = client.get(f"/reports?start={today.isoformat()}&end={pickup_date.isoformat()}")
    assert response.status_code == 200
    assert b"37,000.00" in response.data
    assert b"11,000.00" in response.data
    assert b"500.00" in response.data
    assert b"26,000.00" in response.data
    assert b"not profit" in response.data


def test_csv_export_neutralizes_formula_like_customer_values(client, app):
    dangerous_name = '=HYPERLINK("https://example.invalid","Click")'
    client.post("/customers/new", data={
        "name": dangerous_name, "email": "formula@example.test", "phone": "",
        "address": "", "notes": "", "is_active": "y",
    })
    create_booking_vehicle(client)
    with app.app_context():
        customer_id = Customer.query.one().id
        vehicle_id = Vehicle.query.one().id
    pickup, returned = future_window()
    client.post("/bookings/new", data=booking_values(app, customer_id, vehicle_id, pickup, returned))
    pickup_date = datetime.strptime(pickup, "%Y-%m-%dT%H:%M").date()
    response = client.get(f"/reports/bookings.csv?start={pickup_date.isoformat()}&end={(pickup_date + timedelta(days=1)).isoformat()}")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert b"'=HYPERLINK" in response.data
    assert b"Pickup (local)" in response.data
    assert pickup.replace("T", " ").encode() in response.data
    assert b"attachment; filename=car-rental-bookings-" in response.headers["Content-Disposition"].encode()


def test_report_service_returns_utilization_for_current_fleet(app):
    with app.app_context():
        vehicle = Vehicle(
            internal_id="REPORT-CAR", registration_number="RP 100 AA", make="Example", model="Sedan",
            year=2022, ownership_type="company_owned", daily_rate=10000, operational_status="operational",
        )
        customer = Customer(name="Report Utilization Customer")
        db.session.add_all([vehicle, customer])
        db.session.flush()
        pickup_local = datetime(2027, 1, 5, 0, 0, tzinfo=ZoneInfo("Africa/Lagos"))
        return_local = pickup_local + timedelta(days=1)
        pickup = pickup_local.astimezone(ZoneInfo("UTC"))
        returned = return_local.astimezone(ZoneInfo("UTC"))
        db.session.add(Booking(
            booking_reference="BKG-REPORT-UTIL", customer_id=customer.id, vehicle_id=vehicle.id,
            status="confirmed", pickup_at=pickup, return_at=returned, agreed_daily_rate=10000,
            billable_days=1, additional_charges=0, discount_amount=0, expected_rental_total=10000,
        ))
        db.session.commit()
        report = build_operational_report(pickup_local.date(), pickup_local.date(), "Africa/Lagos")
        assert report["fleet_vehicle_count"] == 1
        assert report["fleet_utilization_percent"] == 100


def test_report_partner_summary_uses_settlement_liability(client, app):
    _partner_id, _vehicle_id, booking_id = complete_partner_booking(client, app)
    client.post(f"/bookings/{booking_id}/settlement", data={})
    with app.app_context():
        booking = Booking.query.one()
        pickup_date = booking.pickup_at.date()
    report = client.get(f"/reports?start={pickup_date.isoformat()}&end={pickup_date.isoformat()}")
    assert report.status_code == 200
    assert b"Partner payout summaries" in report.data
    assert b"23,125.00" in report.data
