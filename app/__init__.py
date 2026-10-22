import os
from pathlib import Path
from datetime import date
from decimal import Decimal

import click
from flask import Flask, render_template
from sqlalchemy import event

from config import DevelopmentConfig, ProductionConfig, TestingConfig
from app.extensions import db, migrate
from app.services import display_local_datetime


def create_app(config_object=None):
    app = Flask(__name__)
    selected_config = config_object or os.environ.get("APP_CONFIG", "development")
    if selected_config == "testing":
        app.config.from_object(TestingConfig)
    elif selected_config == "production":
        ProductionConfig.validate()
        app.config.from_object(ProductionConfig)
    elif isinstance(selected_config, str):
        app.config.from_object(DevelopmentConfig)
    else:
        app.config.from_object(selected_config)

    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if database_uri == DevelopmentConfig.SQLALCHEMY_DATABASE_URI:
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    elif database_uri.startswith("sqlite:///"):
        database_path = Path(database_uri.removeprefix("sqlite:///"))
        database_path.parent.mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    if database_uri.startswith("sqlite:"):
        with app.app_context():
            @event.listens_for(db.engine, "connect")
            def enable_sqlite_foreign_keys(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    from app import models  # noqa: F401
    from app.routes import main

    app.register_blueprint(main)

    @app.template_filter("localdatetime")
    def local_datetime_filter(value):
        return display_local_datetime(value, app.config["LOCAL_TIMEZONE"])

    @app.cli.command("seed-demo")
    def seed_demo():
        """Add a small synthetic fleet without replacing existing records."""
        from app.models import Partner, PartnerAgreement, Vehicle

        partner = Partner.query.filter_by(email="fleet.partner@example.test").first()
        if partner is None:
            partner = Partner(
                name="Amina Example", business_name="Example Mobility",
                email="fleet.partner@example.test", phone="+2348000000101",
                address="12 Sample Road, Lagos", notes="Synthetic development record.",
            )
            db.session.add(partner)
            db.session.flush()

        added_vehicles = 0
        if not PartnerAgreement.query.filter_by(partner_id=partner.id).first():
            db.session.add(PartnerAgreement(
                partner_id=partner.id, agreement_type="percentage",
                revenue_share_percentage=Decimal("65.00"), percentage_basis="gross_rental",
                effective_from=date.today(), notes="Synthetic demo terms; not a real commercial agreement.",
            ))
        demo_vehicles = [
            {
                "internal_id": "DEMO-COMPANY-001", "registration_number": "SYN 100 AA",
                "make": "Toyota", "model": "Corolla", "year": 2022, "color": "Silver",
                "transmission": "automatic", "fuel_type": "petrol", "ownership_type": "company_owned",
                "partner_id": None, "daily_rate": Decimal("45000.00"), "operational_status": "operational",
                "notes": "Synthetic company-owned demo vehicle.",
            },
            {
                "internal_id": "DEMO-PARTNER-001", "registration_number": "SYN 200 BB",
                "make": "Honda", "model": "CR-V", "year": 2021, "color": "Blue",
                "transmission": "automatic", "fuel_type": "petrol", "ownership_type": "partner_owned",
                "partner_id": partner.id, "daily_rate": Decimal("70000.00"), "operational_status": "operational",
                "notes": "Synthetic partner-owned demo vehicle.",
            },
        ]
        for values in demo_vehicles:
            if not Vehicle.query.filter_by(internal_id=values["internal_id"]).first():
                db.session.add(Vehicle(**values))
                added_vehicles += 1
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        click.echo(f"Synthetic demo data ready; added {added_vehicles} vehicle(s). Existing records were left intact.")

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", title="Page not found", message="That page does not exist."), 404

    @app.errorhandler(409)
    def conflict(_error):
        return render_template("error.html", title="Booking conflict", message="That booking action is no longer available. Reload the record and check its current status."), 409

    @app.errorhandler(400)
    def bad_request(_error):
        return render_template("error.html", title="Request could not be accepted", message="Reload the page and submit a valid form."), 400

    @app.errorhandler(500)
    def internal_error(_error):
        app.logger.exception("Unhandled application error")
        return render_template("error.html", title="Something went wrong", message="Please try again."), 500

    return app
