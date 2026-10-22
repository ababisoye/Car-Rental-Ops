from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Partner, PartnerAgreement, Vehicle
from config import TestingConfig


class CsrfEnabledTestingConfig(TestingConfig):
    WTF_CSRF_ENABLED = True


def vehicle_data(**overrides):
    values = {
        "internal_id": "VH-100",
        "registration_number": " ab 123 cd ",
        "make": "Toyota",
        "model": "Corolla",
        "year": "2022",
        "color": "Blue",
        "transmission": "automatic",
        "fuel_type": "petrol",
        "ownership_type": "company_owned",
        "partner_id": "0",
        "daily_rate": "35000.00",
        "operational_status": "operational",
        "notes": "Synthetic test vehicle",
    }
    values.update(overrides)
    return values


def create_partner(client, **overrides):
    values = {"name": "Amina Example", "business_name": "Example Mobility", "email": "amina@example.test", "phone": "+2348000000000", "is_active": "y"}
    values.update(overrides)
    return client.post("/partners/new", data=values, follow_redirects=True)


def test_vehicle_create_normalizes_registration_and_persists_decimal_rate(client, app):
    response = client.post("/vehicles/new", data=vehicle_data(daily_rate="150000"), follow_redirects=True)
    assert response.status_code == 200
    assert b"Vehicle added to the fleet" in response.data
    with app.app_context():
        vehicle = Vehicle.query.one()
        assert vehicle.registration_number == "AB 123 CD"
        assert vehicle.daily_rate == Decimal("150000.00")
        assert vehicle.partner_id is None
        assert vehicle.ownership_type == "company_owned"
    assert "₦150,000.00".encode() in response.data


def test_registration_duplicate_is_case_and_whitespace_insensitive(client, app):
    client.post("/vehicles/new", data=vehicle_data())
    response = client.post("/vehicles/new", data=vehicle_data(internal_id="VH-101", registration_number="ab 123 cd"))
    assert response.status_code == 200
    assert b"already registered" in response.data
    with app.app_context():
        assert Vehicle.query.count() == 1


def test_partner_owned_vehicle_requires_and_links_partner(client, app):
    response = client.post("/vehicles/new", data=vehicle_data(ownership_type="partner_owned", partner_id="0"))
    assert response.status_code == 200
    assert b"Choose the partner that owns this vehicle" in response.data
    with app.app_context():
        assert Vehicle.query.count() == 0

    create_partner(client)
    with app.app_context():
        partner_id = Partner.query.one().id
    response = client.post("/vehicles/new", data=vehicle_data(
        internal_id="VH-102", registration_number="ZZ-555", ownership_type="partner_owned", partner_id=str(partner_id),
    ), follow_redirects=True)
    assert response.status_code == 200
    with app.app_context():
        vehicle = Vehicle.query.one()
        assert vehicle.partner_id == partner_id
        assert vehicle.partner.name == "Amina Example"


def test_invalid_vehicle_year_and_rate_are_rejected(client, app):
    response = client.post("/vehicles/new", data=vehicle_data(year="1700", daily_rate="0"))
    assert response.status_code == 200
    assert b"valid vehicle year" in response.data
    assert b"daily rate above zero" in response.data
    with app.app_context():
        assert Vehicle.query.count() == 0


def test_partner_agreement_terms_are_explicit_and_non_overlapping(client, app):
    create_partner(client)
    with app.app_context():
        partner_id = Partner.query.one().id
    first = client.post(f"/partners/{partner_id}/agreements", data={
        "agreement_type": "fixed_per_rental", "fixed_payout": "12000.00", "revenue_share_percentage": "",
        "effective_from": "2026-01-01", "effective_to": "2026-06-30", "notes": "Synthetic fixed term",
    }, follow_redirects=True)
    assert first.status_code == 200
    assert b"Agreement terms recorded" in first.data
    overlap = client.post(f"/partners/{partner_id}/agreements", data={
        "agreement_type": "percentage", "fixed_payout": "", "revenue_share_percentage": "60.00",
        "effective_from": "2026-06-01", "effective_to": "", "notes": "Overlapping synthetic term",
    })
    assert overlap.status_code == 400
    assert b"overlaps an existing agreement" in overlap.data
    with app.app_context():
        agreement = PartnerAgreement.query.one()
        assert agreement.agreement_type == "fixed_per_rental"
        assert agreement.fixed_payout == Decimal("12000.00")
        assert agreement.revenue_share_percentage is None


def test_dashboard_metrics_are_derived_and_archive_preserves_vehicle(client, app):
    client.post("/vehicles/new", data=vehicle_data())
    client.post("/vehicles/new", data=vehicle_data(
        internal_id="VH-101", registration_number="AB 124 CD", operational_status="maintenance",
    ))
    response = client.get("/")
    assert b">2</div>" in response.data
    assert b">1</div>" in response.data

    with app.app_context():
        vehicle_id = Vehicle.query.filter_by(internal_id="VH-100").one().id
    detail = client.get(f"/vehicles/{vehicle_id}")
    assert detail.status_code == 200
    # The test client skips CSRF only via the testing application configuration.
    archive = client.post(f"/vehicles/{vehicle_id}/archive", data={})
    assert archive.status_code == 302
    with app.app_context():
        vehicle = db.session.get(Vehicle, vehicle_id)
        assert vehicle.is_archived is True
        assert Vehicle.query.count() == 2
    response = client.get("/")
    assert b"Registered vehicles, including archived" in response.data


def test_partner_archive_preserves_vehicle_association(client, app):
    create_partner(client)
    with app.app_context():
        partner_id = Partner.query.one().id
    client.post("/vehicles/new", data=vehicle_data(
        internal_id="VH-102", registration_number="CD-100", ownership_type="partner_owned", partner_id=str(partner_id),
    ))
    response = client.post(f"/partners/{partner_id}/archive", data={})
    assert response.status_code == 302
    with app.app_context():
        partner = db.session.get(Partner, partner_id)
        assert partner.is_active is False
        assert partner.vehicles[0].partner_id == partner_id
        assert Partner.query.count() == 1


def test_vehicle_search_and_filters_use_persisted_records(client, app):
    client.post("/vehicles/new", data=vehicle_data())
    client.post("/vehicles/new", data=vehicle_data(
        internal_id="VH-101", registration_number="EF-200", ownership_type="company_owned", operational_status="maintenance",
    ))
    response = client.get("/vehicles?q=VH-101&status=maintenance")
    assert response.status_code == 200
    assert b"EF-200" in response.data
    assert b"AB 123 CD" not in response.data


def test_vehicle_pagination_keeps_filters_and_renders_next_page(client, app):
    with app.app_context():
        for index in range(21):
            db.session.add(Vehicle(
                internal_id=f"PAGE-{index:03d}", registration_number=f"PG {index:03d}",
                make="Example", model="Sedan", year=2022, ownership_type="company_owned",
                daily_rate=Decimal("10000.00"), operational_status="operational",
            ))
        db.session.commit()
    first_page = client.get("/vehicles?ownership=company_owned&page=1")
    assert first_page.status_code == 200
    assert b"Page 1 of 2" in first_page.data
    assert b"page=2" in first_page.data
    second_page = client.get("/vehicles?ownership=company_owned&page=2")
    assert second_page.status_code == 200
    assert b"Page 2 of 2" in second_page.data


def test_partner_edit_and_restore_preserve_record(client, app):
    create_partner(client)
    with app.app_context():
        partner_id = Partner.query.one().id
    edited = client.post(f"/partners/{partner_id}/edit", data={
        "name": "Amina Updated", "business_name": "Updated Mobility", "email": "updated@example.test",
        "phone": "", "address": "", "notes": "", "is_active": "y",
    }, follow_redirects=True)
    assert edited.status_code == 200
    client.post(f"/partners/{partner_id}/archive", data={})
    client.post(f"/partners/{partner_id}/restore", data={})
    with app.app_context():
        partner = db.session.get(Partner, partner_id)
        assert partner.name == "Amina Updated"
        assert partner.is_active is True
        assert Partner.query.count() == 1


def test_partner_cannot_be_deleted_while_linked_vehicle_exists(client, app):
    create_partner(client)
    with app.app_context():
        partner = Partner.query.one()
        partner_id = partner.id
    client.post("/vehicles/new", data=vehicle_data(
        internal_id="VH-103", registration_number="GH-555", ownership_type="partner_owned", partner_id=str(partner_id),
    ))
    with app.app_context():
        partner = db.session.get(Partner, partner_id)
        db.session.delete(partner)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise AssertionError("linked partner deletion should be restricted")
        assert Partner.query.count() == 1


def test_partner_cannot_be_deleted_while_agreement_history_exists(client, app):
    create_partner(client)
    with app.app_context():
        partner = Partner.query.one()
        partner_id = partner.id
    client.post(f"/partners/{partner_id}/agreements", data={
        "agreement_type": "percentage", "fixed_payout": "", "revenue_share_percentage": "50",
        "effective_from": "2026-01-01", "effective_to": "", "notes": "Synthetic historic agreement",
    })
    with app.app_context():
        partner = db.session.get(Partner, partner_id)
        db.session.delete(partner)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
        else:
            raise AssertionError("partner with agreement history should be restricted from deletion")
        assert Partner.query.count() == 1
        assert PartnerAgreement.query.count() == 1


def test_missing_partner_and_invalid_agreement_terms_are_rejected(client, app):
    missing_partner = client.post("/vehicles/new", data=vehicle_data(
        internal_id="VH-104", registration_number="JK-222", ownership_type="partner_owned", partner_id="999",
    ))
    assert missing_partner.status_code == 200
    with app.app_context():
        assert Vehicle.query.count() == 0

    create_partner(client)
    with app.app_context():
        partner_id = Partner.query.one().id
    response = client.post(f"/partners/{partner_id}/agreements", data={
        "agreement_type": "fixed_per_rental", "fixed_payout": "", "revenue_share_percentage": "35",
        "effective_from": "2026-03-01", "effective_to": "2026-02-01", "notes": "",
    })
    assert response.status_code == 400
    with app.app_context():
        assert PartnerAgreement.query.count() == 0


def test_demo_seed_is_synthetic_and_idempotent(app):
    runner = app.test_cli_runner()
    first = runner.invoke(args=["seed-demo"])
    second = runner.invoke(args=["seed-demo"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "added 2 vehicle(s)" in first.output
    assert "added 0 vehicle(s)" in second.output
    with app.app_context():
        assert Partner.query.count() == 1
        assert Vehicle.query.count() == 2
        assert PartnerAgreement.query.count() == 1


def test_partner_write_rejects_missing_csrf_token():
    app = create_app(CsrfEnabledTestingConfig)
    with app.app_context():
        db.create_all()
    try:
        response = app.test_client().post("/partners/new", data={"name": "No token"})
        assert response.status_code == 200
        assert b"Form security check failed" in response.data
        with app.app_context():
            assert Partner.query.count() == 0
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
