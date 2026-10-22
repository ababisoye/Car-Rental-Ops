from app.extensions import db
from app.models import Partner, Vehicle


def test_home_page_renders_foundation_status(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_health_route_checks_database(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_initial_models_persist_and_enforce_ownership(app):
    with app.app_context():
        partner = Partner(name="Synthetic Partner")
        db.session.add(partner)
        db.session.flush()
        vehicle = Vehicle(
            internal_id="CAR-0001", registration_number="SYN-100", make="Example",
            model="Model A", year=2022, ownership_type="partner_owned", partner_id=partner.id,
            daily_rate=25000,
        )
        db.session.add(vehicle)
        db.session.commit()
        assert Vehicle.query.one().partner.name == "Synthetic Partner"

        invalid = Vehicle(
            internal_id="CAR-0002", registration_number="SYN-101", make="Example",
            model="Model B", year=2023, ownership_type="partner_owned", daily_rate=25000,
        )
        db.session.add(invalid)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        else:
            raise AssertionError("partner-owned vehicle without partner should fail")
