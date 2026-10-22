import csv
import io
from datetime import date, datetime
from decimal import Decimal
import secrets
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, url_for
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import (
    BookingForm, ConfirmForm, CustomerForm, ExpenseForm, PartnerAgreementForm,
    PartnerForm, PaymentForm, SettlementAdjustmentForm, SettlementPaymentForm, VehicleForm,
)
from app.models import (
    Booking, Customer, Expense, Partner, PartnerAgreement, PartnerSettlement,
    Payment, SettlementAdjustment, SettlementPayment, Vehicle,
)
from app.services import (
    booking_payment_totals, calculate_booking_total, dashboard_metrics,
    build_operational_report, has_vehicle_booking_conflict, normalize_registration, now_utc,
    parse_local_datetime, settlement_calculation, validate_payment_transaction, display_local_datetime, as_utc,
)


main = Blueprint("main", __name__)


def _text(value):
    return (value or "").strip()


@main.get("/")
def index():
    return render_template("index.html", metrics=dashboard_metrics())


@main.get("/health")
def health():
    try:
        db.session.execute(db.text("SELECT 1"))
    except Exception:
        db.session.rollback()
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", database="ok")


@main.get("/vehicles")
def vehicle_list():
    query = Vehicle.query.join(Partner, isouter=True)
    if request.args.get("archived") != "1":
        query = query.filter(Vehicle.is_archived.is_(False))
    search = request.args.get("q", "").strip()
    ownership = request.args.get("ownership", "")
    status = request.args.get("status", "")
    partner_id = request.args.get("partner", "")
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(
            Vehicle.internal_id.ilike(pattern), Vehicle.registration_number.ilike(pattern),
            Vehicle.make.ilike(pattern), Vehicle.model.ilike(pattern), Partner.name.ilike(pattern),
            Partner.business_name.ilike(pattern),
        ))
    if ownership in {"company_owned", "partner_owned"}:
        query = query.filter(Vehicle.ownership_type == ownership)
    if status in {"operational", "maintenance", "retired"}:
        query = query.filter(Vehicle.operational_status == status)
    if partner_id.isdigit() and int(partner_id) > 0:
        query = query.filter(Vehicle.partner_id == int(partner_id))
    vehicles = query.order_by(Vehicle.is_archived.asc(), Vehicle.internal_id.asc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    partners = Partner.query.order_by(Partner.name).all()
    return render_template("vehicles/list.html", vehicles=vehicles, partners=partners)


def _vehicle_form(form, vehicle=None):
    available_partners = Partner.query.filter_by(is_active=True).order_by(Partner.name).all()
    if vehicle and vehicle.partner and vehicle.partner not in available_partners:
        available_partners.append(vehicle.partner)
    form.partner_id.choices = [(0, "Select a partner")] + [
        (partner.id, partner.business_name or partner.name) for partner in available_partners
    ]
    return form


@main.route("/vehicles/new", methods=["GET", "POST"])
def vehicle_create():
    form = _vehicle_form(VehicleForm())
    if form.validate_on_submit():
        ownership = form.ownership_type.data
        partner_id = form.partner_id.data if ownership == "partner_owned" else None
        if ownership == "partner_owned" and not partner_id:
            form.partner_id.errors.append("Choose the partner that owns this vehicle.")
        elif partner_id and not db.session.get(Partner, partner_id):
            form.partner_id.errors.append("Choose a valid partner.")
        else:
            registration = normalize_registration(form.registration_number.data)
            internal_id = _text(form.internal_id.data).upper()
            duplicate_registration = Vehicle.query.filter_by(registration_number=registration).first()
            duplicate_internal = Vehicle.query.filter_by(internal_id=internal_id).first()
            if duplicate_registration:
                form.registration_number.errors.append("That registration number is already registered.")
            elif duplicate_internal:
                form.internal_id.errors.append("That internal vehicle ID is already in use.")
            else:
                vehicle = Vehicle(
                    internal_id=internal_id,
                    registration_number=registration,
                    make=_text(form.make.data),
                    model=_text(form.model.data),
                    year=int(form.year.data),
                    color=_text(form.color.data) or None,
                    transmission=form.transmission.data or None,
                    fuel_type=form.fuel_type.data or None,
                    ownership_type=ownership,
                    partner_id=partner_id,
                    daily_rate=form.daily_rate.data,
                    operational_status=form.operational_status.data,
                    notes=_text(form.notes.data) or None,
                )
                db.session.add(vehicle)
                try:
                    db.session.commit()
                    flash("Vehicle added to the fleet.", "success")
                    return redirect(url_for("main.vehicle_detail", vehicle_id=vehicle.id))
                except IntegrityError:
                    db.session.rollback()
                    flash("The vehicle could not be saved because a unique value is already in use.", "error")
    return render_template("vehicles/form.html", form=form, title="Add vehicle", vehicle=None)


@main.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
def vehicle_edit(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    form = _vehicle_form(VehicleForm(obj=vehicle), vehicle)
    if request.method == "GET":
        form.partner_id.data = vehicle.partner_id or 0
        form.year.data = str(vehicle.year)
    if form.validate_on_submit():
        ownership = form.ownership_type.data
        partner_id = form.partner_id.data if ownership == "partner_owned" else None
        if ownership == "partner_owned" and not partner_id:
            form.partner_id.errors.append("Choose the partner that owns this vehicle.")
        elif partner_id and not db.session.get(Partner, partner_id):
            form.partner_id.errors.append("Choose a valid partner.")
        else:
            registration = normalize_registration(form.registration_number.data)
            internal_id = _text(form.internal_id.data).upper()
            duplicate_registration = Vehicle.query.filter(Vehicle.registration_number == registration, Vehicle.id != vehicle.id).first()
            duplicate_internal = Vehicle.query.filter(Vehicle.internal_id == internal_id, Vehicle.id != vehicle.id).first()
            if duplicate_registration:
                form.registration_number.errors.append("That registration number is already registered.")
            elif duplicate_internal:
                form.internal_id.errors.append("That internal vehicle ID is already in use.")
            else:
                vehicle.internal_id = internal_id
                vehicle.registration_number = registration
                vehicle.make = _text(form.make.data)
                vehicle.model = _text(form.model.data)
                vehicle.year = int(form.year.data)
                vehicle.color = _text(form.color.data) or None
                vehicle.transmission = form.transmission.data or None
                vehicle.fuel_type = form.fuel_type.data or None
                vehicle.ownership_type = ownership
                vehicle.partner_id = partner_id
                vehicle.daily_rate = form.daily_rate.data
                vehicle.operational_status = form.operational_status.data
                vehicle.notes = _text(form.notes.data) or None
                try:
                    db.session.commit()
                    flash("Vehicle details updated.", "success")
                    return redirect(url_for("main.vehicle_detail", vehicle_id=vehicle.id))
                except IntegrityError:
                    db.session.rollback()
                    flash("The vehicle could not be saved because a unique value is already in use.", "error")
    return render_template("vehicles/form.html", form=form, title="Edit vehicle", vehicle=vehicle)


@main.get("/vehicles/<int:vehicle_id>")
def vehicle_detail(vehicle_id):
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    return render_template("vehicles/detail.html", vehicle=vehicle, confirm_form=ConfirmForm())


@main.post("/vehicles/<int:vehicle_id>/archive")
def vehicle_archive(vehicle_id):
    form = ConfirmForm()
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if not form.validate_on_submit():
        abort(400)
    vehicle.is_archived = True
    db.session.commit()
    flash("Vehicle archived. Its record is preserved.", "success")
    return redirect(url_for("main.vehicle_list"))


@main.post("/vehicles/<int:vehicle_id>/restore")
def vehicle_restore(vehicle_id):
    form = ConfirmForm()
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    if not form.validate_on_submit():
        abort(400)
    vehicle.is_archived = False
    db.session.commit()
    flash("Vehicle restored to the fleet.", "success")
    return redirect(url_for("main.vehicle_detail", vehicle_id=vehicle.id))


@main.get("/partners")
def partner_list():
    partners = Partner.query.order_by(Partner.is_active.desc(), Partner.name.asc()).all()
    return render_template("partners/list.html", partners=partners)


@main.route("/partners/new", methods=["GET", "POST"])
def partner_create():
    form = PartnerForm()
    if form.validate_on_submit():
        partner = Partner(
            name=_text(form.name.data), business_name=_text(form.business_name.data) or None,
            email=_text(form.email.data) or None, phone=_text(form.phone.data) or None,
            address=_text(form.address.data) or None, notes=_text(form.notes.data) or None,
            is_active=form.is_active.data,
        )
        db.session.add(partner)
        db.session.commit()
        flash("Partner added.", "success")
        return redirect(url_for("main.partner_detail", partner_id=partner.id))
    return render_template("partners/form.html", form=form, title="Add partner")


@main.route("/partners/<int:partner_id>/edit", methods=["GET", "POST"])
def partner_edit(partner_id):
    partner = db.get_or_404(Partner, partner_id)
    form = PartnerForm(obj=partner)
    if form.validate_on_submit():
        partner.name = _text(form.name.data)
        partner.business_name = _text(form.business_name.data) or None
        partner.email = _text(form.email.data) or None
        partner.phone = _text(form.phone.data) or None
        partner.address = _text(form.address.data) or None
        partner.notes = _text(form.notes.data) or None
        partner.is_active = form.is_active.data
        db.session.commit()
        flash("Partner details updated.", "success")
        return redirect(url_for("main.partner_detail", partner_id=partner.id))
    return render_template("partners/form.html", form=form, title="Edit partner")


@main.get("/partners/<int:partner_id>")
def partner_detail(partner_id):
    partner = db.get_or_404(Partner, partner_id)
    agreement_form = PartnerAgreementForm()
    today = date.today()
    return render_template(
        "partners/detail.html", partner=partner, agreement_form=agreement_form,
        today=today, confirm_form=ConfirmForm(),
        settlement_rows=[(item, settlement_calculation(item)) for item in partner.settlements],
    )


@main.post("/partners/<int:partner_id>/agreements")
def partner_add_agreement(partner_id):
    partner = db.get_or_404(Partner, partner_id)
    form = PartnerAgreementForm()
    if form.validate_on_submit():
        candidate_start = form.effective_from.data
        candidate_end = form.effective_to.data or date.max
        for existing in partner.agreements:
            existing_end = existing.effective_to or date.max
            if candidate_start <= existing_end and existing.effective_from <= candidate_end:
                form.effective_from.errors.append("This agreement overlaps an existing agreement date range.")
                break
        else:
            agreement = PartnerAgreement(
                partner_id=partner.id,
                agreement_type=form.agreement_type.data,
                fixed_payout=form.fixed_payout.data if form.agreement_type.data == "fixed_per_rental" else None,
                revenue_share_percentage=form.revenue_share_percentage.data if form.agreement_type.data == "percentage" else None,
                percentage_basis="gross_rental" if form.agreement_type.data == "percentage" else None,
                effective_from=form.effective_from.data,
                effective_to=form.effective_to.data,
                notes=_text(form.notes.data) or None,
            )
            db.session.add(agreement)
            db.session.commit()
            flash("Agreement terms recorded.", "success")
            return redirect(url_for("main.partner_detail", partner_id=partner.id))
    return render_template(
        "partners/detail.html", partner=partner, agreement_form=form,
        today=date.today(), confirm_form=ConfirmForm(),
    ), 400


@main.post("/partners/<int:partner_id>/archive")
def partner_archive(partner_id):
    partner = db.get_or_404(Partner, partner_id)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    partner.is_active = False
    db.session.commit()
    flash("Partner archived. Linked vehicle and agreement history is preserved.", "success")
    return redirect(url_for("main.partner_detail", partner_id=partner.id))


@main.post("/partners/<int:partner_id>/restore")
def partner_restore(partner_id):
    partner = db.get_or_404(Partner, partner_id)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    partner.is_active = True
    db.session.commit()
    flash("Partner restored.", "success")
    return redirect(url_for("main.partner_detail", partner_id=partner.id))


@main.get("/customers")
def customer_list():
    customers = Customer.query.order_by(Customer.is_active.desc(), Customer.name.asc()).all()
    return render_template("customers/list.html", customers=customers)


@main.route("/customers/new", methods=["GET", "POST"])
def customer_create():
    form = CustomerForm()
    if form.validate_on_submit():
        customer = Customer(
            name=_text(form.name.data), email=_text(form.email.data) or None,
            phone=_text(form.phone.data) or None, address=_text(form.address.data) or None,
            notes=_text(form.notes.data) or None, is_active=form.is_active.data,
        )
        db.session.add(customer)
        db.session.commit()
        flash("Customer added.", "success")
        return redirect(url_for("main.customer_detail", customer_id=customer.id))
    return render_template("customers/form.html", form=form, title="Add customer")


@main.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
def customer_edit(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    form = CustomerForm(obj=customer)
    if form.validate_on_submit():
        customer.name = _text(form.name.data)
        customer.email = _text(form.email.data) or None
        customer.phone = _text(form.phone.data) or None
        customer.address = _text(form.address.data) or None
        customer.notes = _text(form.notes.data) or None
        customer.is_active = form.is_active.data
        db.session.commit()
        flash("Customer details updated.", "success")
        return redirect(url_for("main.customer_detail", customer_id=customer.id))
    return render_template("customers/form.html", form=form, title="Edit customer")


@main.get("/customers/<int:customer_id>")
def customer_detail(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    return render_template("customers/detail.html", customer=customer)


def _booking_form(form):
    customers = Customer.query.filter_by(is_active=True).order_by(Customer.name).all()
    vehicles = Vehicle.query.filter_by(is_archived=False, operational_status="operational").filter(
        Vehicle.daily_rate.is_not(None),
    ).order_by(Vehicle.internal_id).all()
    vehicles = [vehicle for vehicle in vehicles if vehicle.partner is None or vehicle.partner.is_active]
    form.customer_id.choices = [(0, "Select a customer")] + [(customer.id, customer.name) for customer in customers]
    form.vehicle_id.choices = [(0, "Select a vehicle with a recorded rate")] + [
        (vehicle.id, f"{vehicle.internal_id} · {vehicle.make} {vehicle.model} · {vehicle.registration_number} · ₦{vehicle.daily_rate:,.2f}")
        for vehicle in vehicles
    ]
    return customers, vehicles


@main.get("/bookings")
def booking_list():
    query = Booking.query.join(Customer).join(Vehicle)
    status = request.args.get("status", "")
    search = request.args.get("q", "").strip()
    if status in {"pending", "confirmed", "active", "completed", "cancelled"}:
        query = query.filter(Booking.status == status)
    if search:
        pattern = f"%{search}%"
        query = query.filter(or_(Booking.booking_reference.ilike(pattern), Customer.name.ilike(pattern), Vehicle.registration_number.ilike(pattern), Vehicle.internal_id.ilike(pattern)))
    bookings = query.order_by(Booking.pickup_at.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    return render_template("bookings/list.html", bookings=bookings)


@main.route("/bookings/new", methods=["GET", "POST"])
def booking_create():
    form = BookingForm()
    _customers, vehicles = _booking_form(form)
    vehicle_rates = {str(vehicle.id): f"{vehicle.daily_rate:.2f}" for vehicle in vehicles}
    if request.method == "GET":
        customer_id = request.args.get("customer", type=int)
        if customer_id and any(customer.id == customer_id for customer in _customers):
            form.customer_id.data = customer_id
        form.initial_payment_local.data = now_utc().astimezone(ZoneInfo(app_timezone())).strftime("%Y-%m-%dT%H:%M")
    if form.validate_on_submit():
        customer = db.session.get(Customer, form.customer_id.data)
        vehicle = db.session.get(Vehicle, form.vehicle_id.data)
        if customer is None or not customer.is_active:
            form.customer_id.errors.append("Choose an active customer.")
        if vehicle is None or vehicle not in vehicles:
            form.vehicle_id.errors.append("Choose an operational, unarchived vehicle with a recorded rate.")
        if not form.customer_id.errors and not form.vehicle_id.errors:
            pickup_at = None
            return_at = None
            try:
                pickup_at = parse_local_datetime(form.pickup_local.data, app_timezone())
            except ValueError as error:
                form.pickup_local.errors.append(str(error))
            try:
                return_at = parse_local_datetime(form.return_local.data, app_timezone())
            except ValueError as error:
                form.return_local.errors.append(str(error))
            if pickup_at is not None and return_at is not None:
                if pickup_at >= return_at:
                    form.return_local.errors.append("Return time must be later than pickup time.")
                if pickup_at <= now_utc():
                    form.pickup_local.errors.append("Pickup must be in the future.")
                additional = form.additional_charges.data or Decimal("0.00")
                discount = form.discount_amount.data or Decimal("0.00")
                try:
                    billable_days, total = calculate_booking_total(
                        pickup_at, return_at, form.agreed_daily_rate.data, additional, discount,
                    )
                except ValueError as error:
                    form.discount_amount.errors.append(str(error))
                    billable_days, total = 0, Decimal("0.00")
                payment_at = None
                try:
                    payment_at = parse_local_datetime(form.initial_payment_local.data, app_timezone())
                    if payment_at > now_utc():
                        raise ValueError("Payment date cannot be in the future.")
                except ValueError as error:
                    form.initial_payment_local.errors.append(str(error))
                if form.initial_payment_amount.data is not None and form.initial_payment_amount.data > total:
                    form.initial_payment_amount.errors.append("Payment cannot exceed the expected rental total.")
                if form.initial_status.data == "confirmed" and has_vehicle_booking_conflict(vehicle.id, pickup_at, return_at):
                    form.vehicle_id.errors.append("This vehicle already has a confirmed or active booking during those dates.")
                if not form.errors:
                    pickup_date_local = pickup_at.astimezone(ZoneInfo(app_timezone())).date()
                    agreement = None
                    if vehicle.partner:
                        agreement = next((item for item in vehicle.partner.agreements if
                                          item.effective_from <= pickup_date_local and
                                          (item.effective_to is None or item.effective_to >= pickup_date_local)), None)
                    reference = f"BKG-{now_utc():%Y%m%d%H%M%S}-{secrets.token_hex(3).upper()}"
                    booking = Booking(
                        booking_reference=reference, customer_id=customer.id, vehicle_id=vehicle.id,
                        status=form.initial_status.data, pickup_at=pickup_at, return_at=return_at,
                        agreed_daily_rate=form.agreed_daily_rate.data, billable_days=billable_days,
                        additional_charges=additional, discount_amount=discount,
                        expected_rental_total=total, notes=_text(form.notes.data) or None,
                        partner_id_snapshot=vehicle.partner_id if vehicle.ownership_type == "partner_owned" else None,
                        partner_agreement_id=agreement.id if agreement else None,
                        partner_agreement_type_snapshot=agreement.agreement_type if agreement else None,
                        partner_fixed_payout_snapshot=agreement.fixed_payout if agreement and agreement.agreement_type == "fixed_per_rental" else None,
                        partner_share_percentage_snapshot=agreement.revenue_share_percentage if agreement and agreement.agreement_type == "percentage" else None,
                        partner_percentage_basis_snapshot=agreement.percentage_basis if agreement and agreement.agreement_type == "percentage" else None,
                    )
                    db.session.add(booking)
                    try:
                        db.session.flush()
                        db.session.add(Payment(
                            booking_id=booking.id, transaction_type="payment",
                            amount=form.initial_payment_amount.data,
                            payment_method=form.initial_payment_method.data,
                            reference=_text(form.initial_payment_reference.data) or None,
                            transacted_at=payment_at,
                            notes="Payment recorded during booking creation after operator confirmation.",
                        ))
                        db.session.commit()
                        flash(f"Booking {reference} created and initial payment recorded.", "success")
                        return redirect(url_for("main.booking_detail", booking_id=booking.id))
                    except IntegrityError:
                        db.session.rollback()
                        flash("The booking could not be saved because a related record changed. Reload and try again.", "error")
    return render_template("bookings/form.html", form=form, vehicles=vehicles, vehicle_rates=vehicle_rates, title="Create booking")


def app_timezone():
    from flask import current_app
    return current_app.config["LOCAL_TIMEZONE"]


@main.get("/bookings/<int:booking_id>")
def booking_detail(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    paid, refunded, balance = booking_payment_totals(booking)
    return render_template(
        "bookings/detail.html", booking=booking, confirm_form=ConfirmForm(),
        payment_form=PaymentForm(), paid=paid, refunded=refunded, balance=balance,
        can_record_pickup=booking.status == "confirmed" and now_utc() >= as_utc(booking.pickup_at),
    )


def _booking_transition(booking_id, action):
    booking = db.get_or_404(Booking, booking_id)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    current_time = now_utc()
    if action == "confirm":
        if booking.status != "pending":
            abort(409)
        if has_vehicle_booking_conflict(booking.vehicle_id, booking.pickup_at, booking.return_at, booking.id):
            flash("Another confirmed or active booking now overlaps these dates.", "error")
            return redirect(url_for("main.booking_detail", booking_id=booking.id))
        booking.status = "confirmed"
    elif action == "pickup":
        if booking.status != "confirmed":
            abort(409)
        if current_time < as_utc(booking.pickup_at):
            flash("Pickup cannot be recorded before the scheduled pickup time. The booking remains confirmed.", "error")
            return redirect(url_for("main.booking_detail", booking_id=booking.id))
        booking.status = "active"
        booking.actual_pickup_at = current_time
    elif action == "return":
        if booking.status != "active":
            abort(409)
        booking.status = "completed"
        booking.actual_return_at = current_time
    elif action == "cancel":
        if booking.status not in {"pending", "confirmed"}:
            abort(409)
        booking.status = "cancelled"
    db.session.commit()
    flash(f"Booking {booking.booking_reference} marked {booking.status}.", "success")
    return redirect(url_for("main.booking_detail", booking_id=booking.id))


@main.post("/bookings/<int:booking_id>/confirm")
def booking_confirm(booking_id):
    return _booking_transition(booking_id, "confirm")


@main.post("/bookings/<int:booking_id>/pickup")
def booking_pickup(booking_id):
    return _booking_transition(booking_id, "pickup")


@main.post("/bookings/<int:booking_id>/return")
def booking_return(booking_id):
    return _booking_transition(booking_id, "return")


@main.post("/bookings/<int:booking_id>/cancel")
def booking_cancel(booking_id):
    return _booking_transition(booking_id, "cancel")


@main.post("/bookings/<int:booking_id>/payments")
def booking_record_payment(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    form = PaymentForm()
    if form.validate_on_submit():
        try:
            transacted_at = parse_local_datetime(form.transacted_local.data, app_timezone())
            if transacted_at > now_utc():
                raise ValueError("Transaction date cannot be in the future.")
            amount = validate_payment_transaction(booking, form.transaction_type.data, form.amount.data)
        except ValueError as error:
            form.amount.errors.append(str(error))
        else:
            payment = Payment(
                booking_id=booking.id, transaction_type=form.transaction_type.data, amount=amount,
                payment_method=form.payment_method.data, reference=_text(form.reference.data) or None,
                transacted_at=transacted_at, notes=_text(form.notes.data) or None,
            )
            db.session.add(payment)
            try:
                db.session.flush()
                if form.transaction_type.data == "refund" and booking.settlement:
                    db.session.add(SettlementAdjustment(
                        settlement_id=booking.settlement.id, adjustment_type="customer_refund",
                        amount=amount, effective_at=transacted_at, payment_id=payment.id,
                        note=f"Customer refund recorded on booking {booking.booking_reference}.",
                    ))
                db.session.commit()
                flash("Financial transaction recorded. Entries cannot be edited or deleted.", "success")
                return redirect(url_for("main.booking_detail", booking_id=booking.id))
            except IntegrityError:
                db.session.rollback()
                flash("The financial entry could not be saved. Reload and try again.", "error")
    paid, refunded, balance = booking_payment_totals(booking)
    return render_template(
        "bookings/detail.html", booking=booking, confirm_form=ConfirmForm(),
        payment_form=form, paid=paid, refunded=refunded, balance=balance,
        can_record_pickup=booking.status == "confirmed" and now_utc() >= as_utc(booking.pickup_at),
    ), 400


@main.get("/expenses")
def expense_list():
    query = Expense.query.join(Vehicle)
    category = request.args.get("category", "")
    vehicle_id = request.args.get("vehicle", "")
    if category in {"maintenance", "fuel", "cleaning", "insurance", "licensing", "repair", "other"}:
        query = query.filter(Expense.category == category)
    if vehicle_id.isdigit() and int(vehicle_id) > 0:
        query = query.filter(Expense.vehicle_id == int(vehicle_id))
    expenses = query.order_by(Expense.incurred_at.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    vehicles = Vehicle.query.order_by(Vehicle.internal_id).all()
    return render_template("expenses/list.html", expenses=expenses, vehicles=vehicles)


@main.route("/expenses/new", methods=["GET", "POST"])
def expense_create():
    form = ExpenseForm()
    vehicles = Vehicle.query.order_by(Vehicle.internal_id).all()
    form.vehicle_id.choices = [(0, "Select a vehicle")] + [
        (vehicle.id, f"{vehicle.internal_id} · {vehicle.make} {vehicle.model} · {vehicle.registration_number}")
        for vehicle in vehicles
    ]
    if request.method == "GET":
        selected_vehicle_id = request.args.get("vehicle_id", type=int)
        if selected_vehicle_id and any(vehicle.id == selected_vehicle_id for vehicle in vehicles):
            form.vehicle_id.data = selected_vehicle_id
    if form.validate_on_submit():
        vehicle = db.session.get(Vehicle, form.vehicle_id.data)
        if vehicle is None:
            form.vehicle_id.errors.append("Choose a valid vehicle.")
        else:
            try:
                incurred_at = parse_local_datetime(form.incurred_local.data, app_timezone())
                if incurred_at > now_utc():
                    raise ValueError("Expense date cannot be in the future.")
            except ValueError as error:
                form.incurred_local.errors.append(str(error))
            else:
                expense = Expense(
                    vehicle_id=vehicle.id, category=form.category.data, amount=form.amount.data,
                    payment_method=form.payment_method.data, reference=_text(form.reference.data) or None,
                    incurred_at=incurred_at, notes=_text(form.notes.data) or None,
                )
                db.session.add(expense)
                db.session.commit()
                flash("Vehicle expense recorded. Entries cannot be edited or deleted.", "success")
                return redirect(url_for("main.expense_list"))
    return render_template("expenses/form.html", form=form, title="Record vehicle expense")


@main.get("/settlements")
def settlement_list():
    settlements = PartnerSettlement.query.order_by(PartnerSettlement.created_at.desc()).all()
    rows = [(item, settlement_calculation(item)) for item in settlements]
    return render_template("settlements/list.html", rows=rows)


@main.post("/bookings/<int:booking_id>/settlement")
def settlement_create(booking_id):
    booking = db.get_or_404(Booking, booking_id)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    if booking.status != "completed":
        abort(409)
    if booking.partner_id_snapshot is None:
        flash("Only completed partner-owned rentals can have a partner settlement.", "error")
        return redirect(url_for("main.booking_detail", booking_id=booking.id))
    if not booking.partner_agreement_type_snapshot:
        flash("No agreement terms were effective for this booking. Add or correct the recorded terms before settling; the system will not invent a payout.", "error")
        return redirect(url_for("main.booking_detail", booking_id=booking.id))
    if booking.settlement:
        flash("A settlement already exists for this booking.", "error")
        return redirect(url_for("main.settlement_detail", settlement_id=booking.settlement.id))
    settlement = PartnerSettlement(
        booking_id=booking.id, partner_id=booking.partner_id_snapshot,
        gross_rental_amount=booking.expected_rental_total,
        agreement_type_snapshot=booking.partner_agreement_type_snapshot,
        fixed_payout_snapshot=booking.partner_fixed_payout_snapshot,
        revenue_share_percentage_snapshot=booking.partner_share_percentage_snapshot,
        percentage_basis_snapshot=booking.partner_percentage_basis_snapshot,
    )
    db.session.add(settlement)
    try:
        db.session.flush()
        for payment in booking.payments:
            if payment.transaction_type == "refund":
                db.session.add(SettlementAdjustment(
                    settlement_id=settlement.id, adjustment_type="customer_refund",
                    amount=payment.amount, effective_at=payment.transacted_at,
                    payment_id=payment.id,
                    note=f"Customer refund recorded on booking {booking.booking_reference}.",
                ))
        db.session.commit()
        flash("Partner settlement created from the booking’s recorded agreement snapshot.", "success")
        return redirect(url_for("main.settlement_detail", settlement_id=settlement.id))
    except IntegrityError:
        db.session.rollback()
        flash("A settlement already exists for this booking.", "error")
        return redirect(url_for("main.booking_detail", booking_id=booking.id))


@main.get("/settlements/<int:settlement_id>")
def settlement_detail(settlement_id):
    settlement = db.get_or_404(PartnerSettlement, settlement_id)
    calculation = settlement_calculation(settlement)
    adjustment_form = SettlementAdjustmentForm()
    used_expense_ids = [item.expense_id for item in settlement.adjustments if item.expense_id]
    expenses = Expense.query.filter_by(vehicle_id=settlement.booking.vehicle_id).order_by(Expense.incurred_at.desc()).all()
    expenses = [item for item in expenses if item.id not in used_expense_ids]
    adjustment_form.expense_id.choices = [(0, "Select an eligible expense")] + [
        (item.id, f"{item.incurred_at.strftime('%Y-%m-%d')} · {item.category.title()} · ₦{item.amount:,.2f}")
        for item in expenses
    ]
    return render_template(
        "settlements/detail.html", settlement=settlement, calculation=calculation,
        adjustment_form=adjustment_form, settlement_payment_form=SettlementPaymentForm(),
    )


@main.post("/settlements/<int:settlement_id>/adjustments")
def settlement_add_adjustment(settlement_id):
    settlement = db.get_or_404(PartnerSettlement, settlement_id)
    form = SettlementAdjustmentForm()
    used_expense_ids = [item.expense_id for item in settlement.adjustments if item.expense_id]
    expenses = Expense.query.filter_by(vehicle_id=settlement.booking.vehicle_id).all()
    eligible_expenses = [item for item in expenses if item.id not in used_expense_ids]
    form.expense_id.choices = [(0, "Select an eligible expense")] + [
        (item.id, f"{item.incurred_at.strftime('%Y-%m-%d')} · {item.category.title()} · ₦{item.amount:,.2f}")
        for item in eligible_expenses
    ]
    if form.validate_on_submit():
        try:
            effective_at = parse_local_datetime(form.effective_local.data, app_timezone())
            if effective_at > now_utc():
                raise ValueError("Adjustment date cannot be in the future.")
        except ValueError as error:
            form.effective_local.errors.append(str(error))
        if not form.effective_local.errors:
            amount = form.amount.data
            expense_id = None
            if form.adjustment_type.data == "expense_deduction":
                expense = next((item for item in eligible_expenses if item.id == form.expense_id.data), None)
                if expense is None:
                    form.expense_id.errors.append("Choose an eligible expense for this vehicle.")
                elif amount > expense.amount:
                    form.amount.errors.append("Deduction cannot exceed the recorded expense amount.")
                else:
                    current = settlement_calculation(settlement)
                    if current["expense_deductions"] + amount > max(Decimal("0.00"), current["gross"] - current["refunds"]):
                        form.amount.errors.append("Expense deductions cannot exceed the remaining gross rental base.")
                    else:
                        expense_id = expense.id
            elif form.adjustment_type.data == "partner_debit":
                if amount > settlement_calculation(settlement)["partner_share"]:
                    form.amount.errors.append("Partner debit cannot exceed the current partner share.")
            if not form.errors:
                adjustment = SettlementAdjustment(
                    settlement_id=settlement.id, adjustment_type=form.adjustment_type.data,
                    amount=amount, effective_at=effective_at, expense_id=expense_id,
                    note=_text(form.note.data),
                )
                db.session.add(adjustment)
                db.session.commit()
                flash("Settlement adjustment recorded in the audit history.", "success")
                return redirect(url_for("main.settlement_detail", settlement_id=settlement.id))
    return render_template(
        "settlements/detail.html", settlement=settlement,
        calculation=settlement_calculation(settlement), adjustment_form=form,
        settlement_payment_form=SettlementPaymentForm(),
    ), 400


@main.post("/settlements/<int:settlement_id>/payments")
def settlement_record_payment(settlement_id):
    settlement = db.get_or_404(PartnerSettlement, settlement_id)
    form = SettlementPaymentForm()
    if form.validate_on_submit():
        try:
            settled_at = parse_local_datetime(form.settled_local.data, app_timezone())
            if settled_at > now_utc():
                raise ValueError("Settlement date cannot be in the future.")
        except ValueError as error:
            form.settled_local.errors.append(str(error))
        calculation = settlement_calculation(settlement)
        if form.amount.data > calculation["outstanding"]:
            form.amount.errors.append("Settlement payment cannot exceed the outstanding partner amount.")
        if not form.errors:
            entry = SettlementPayment(
                settlement_id=settlement.id, amount=form.amount.data,
                payment_method=form.payment_method.data, reference=_text(form.reference.data) or None,
                settled_at=settled_at, notes=_text(form.notes.data) or None,
            )
            db.session.add(entry)
            db.session.commit()
            flash("Partner settlement payment recorded. The entry cannot be edited or deleted.", "success")
            return redirect(url_for("main.settlement_detail", settlement_id=settlement.id))
    return render_template(
        "settlements/detail.html", settlement=settlement,
        calculation=settlement_calculation(settlement), adjustment_form=SettlementAdjustmentForm(),
        settlement_payment_form=form,
    ), 400


def _report_dates():
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo(app_timezone())).date()
    start_default = today.replace(day=1)
    try:
        start_date = date.fromisoformat(request.args.get("start", start_default.isoformat()))
        end_date = date.fromisoformat(request.args.get("end", today.isoformat()))
    except ValueError:
        abort(400)
    if end_date < start_date:
        abort(400)
    return start_date, end_date


@main.get("/reports")
def reports():
    start_date, end_date = _report_dates()
    report = build_operational_report(start_date, end_date, app_timezone())
    return render_template("reports/index.html", report=report)


def _csv_safe(value):
    text = str(value if value is not None else "")
    if text.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
        return "'" + text
    return text


@main.get("/reports/bookings.csv")
def report_bookings_csv():
    start_date, end_date = _report_dates()
    report = build_operational_report(start_date, end_date, app_timezone())
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "Booking reference", "Pickup (local)", "Return (local)", "Vehicle ID",
        "Registration", "Customer", "Status", "Gross rental amount (NGN)",
        "Customer payments (NGN)", "Customer refunds (NGN)", "Outstanding balance (NGN)",
        "Partner share (NGN)", "Partner settled (NGN)", "Partner outstanding (NGN)",
    ])
    for booking in report["bookings"]:
        paid, refunded, balance = booking_payment_totals(booking)
        settlement_data = settlement_calculation(booking.settlement) if booking.settlement else None
        values = [
            booking.booking_reference,
            display_local_datetime(booking.pickup_at, app_timezone()),
            display_local_datetime(booking.return_at, app_timezone()),
            booking.vehicle.internal_id,
            booking.vehicle.registration_number,
            booking.customer.name,
            booking.status,
            booking.expected_rental_total,
            paid,
            refunded,
            balance,
            settlement_data["partner_share"] if settlement_data else Decimal("0.00"),
            settlement_data["settled"] if settlement_data else Decimal("0.00"),
            settlement_data["outstanding"] if settlement_data else Decimal("0.00"),
        ]
        writer.writerow([_csv_safe(value) for value in values])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=car-rental-bookings-{start_date}-{end_date}.csv"
    return response
