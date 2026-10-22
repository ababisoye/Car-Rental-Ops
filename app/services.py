import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_

from app.extensions import db
from app.models import Partner, Vehicle


def normalize_registration(value):
    """Store registration numbers in a consistent uppercase, trimmed form."""
    return " ".join((value or "").upper().split())


def dashboard_metrics():
    from app.models import Booking, PartnerSettlement

    active_fleet = Vehicle.query.filter_by(is_archived=False)
    current_time = now_utc()
    occupied_now = db.session.query(Booking.vehicle_id).filter(
        or_(
            Booking.status == "active",
            and_(Booking.status == "confirmed", Booking.pickup_at <= current_time, Booking.return_at > current_time),
        ),
    )
    return {
        "total_vehicles": Vehicle.query.count(),
        "available_vehicles": active_fleet.filter_by(operational_status="operational").filter(~Vehicle.id.in_(occupied_now)).count(),
        "maintenance_vehicles": active_fleet.filter_by(operational_status="maintenance").count(),
        "company_vehicles": active_fleet.filter_by(ownership_type="company_owned").count(),
        "partner_vehicles": active_fleet.filter_by(ownership_type="partner_owned").count(),
        "active_partners": Partner.query.filter_by(is_active=True).count(),
        "active_bookings": Booking.query.filter_by(status="active").count(),
        "upcoming_pickups": Booking.query.filter(
            Booking.status == "confirmed", Booking.pickup_at >= current_time,
            Booking.pickup_at < current_time + timedelta(days=7),
        ).count(),
        "overdue_returns": Booking.query.filter(
            Booking.status == "active", Booking.return_at < current_time,
        ).count(),
        "outstanding_customer_balance": _outstanding_balances(Booking),
        "outstanding_partner_settlements": sum(
            (settlement_calculation(item)["outstanding"] for item in PartnerSettlement.query.all()),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    }


def _outstanding_balances(Booking):
    total = Decimal("0.00")
    for booking in Booking.query.filter(Booking.status != "cancelled").all():
        _paid, _refunded, balance = booking_payment_totals(booking)
        total += balance
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def booking_payment_totals(booking):
    paid = sum((item.amount for item in booking.payments if item.transaction_type == "payment"), Decimal("0.00"))
    refunded = sum((item.amount for item in booking.payments if item.transaction_type == "refund"), Decimal("0.00"))
    net_received = paid - refunded
    balance = Decimal(booking.expected_rental_total) - net_received
    if booking.status == "cancelled":
        balance = Decimal("0.00")
    return paid, refunded, balance.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def validate_payment_transaction(booking, transaction_type, amount):
    paid, refunded, balance = booking_payment_totals(booking)
    value = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value <= 0:
        raise ValueError("Amount must be greater than zero.")
    if transaction_type == "payment":
        if booking.status == "cancelled":
            raise ValueError("Cancelled bookings cannot receive new payments.")
        if value > balance:
            raise ValueError("Payment cannot exceed the outstanding customer balance.")
    elif transaction_type == "refund":
        if value > paid - refunded:
            raise ValueError("Refund cannot exceed the net amount recorded as paid.")
    else:
        raise ValueError("Choose a valid transaction type.")
    return value


def settlement_calculation(settlement):
    gross = Decimal(settlement.gross_rental_amount)
    refunds = sum((item.amount for item in settlement.adjustments if item.adjustment_type == "customer_refund"), Decimal("0.00"))
    expense_deductions = sum((item.amount for item in settlement.adjustments if item.adjustment_type == "expense_deduction"), Decimal("0.00"))
    credits = sum((item.amount for item in settlement.adjustments if item.adjustment_type == "partner_credit"), Decimal("0.00"))
    debits = sum((item.amount for item in settlement.adjustments if item.adjustment_type == "partner_debit"), Decimal("0.00"))
    payout_base = max(Decimal("0.00"), gross - refunds - expense_deductions)
    if settlement.agreement_type_snapshot == "fixed_per_rental":
        contractual_share = Decimal(settlement.fixed_payout_snapshot)
    else:
        percentage = Decimal(settlement.revenue_share_percentage_snapshot) / Decimal("100")
        contractual_share = (payout_base * percentage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    partner_share = (contractual_share + credits - debits).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    company_share = (gross - refunds - expense_deductions - partner_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    settled = sum((item.amount for item in settlement.payments), Decimal("0.00"))
    outstanding = max(Decimal("0.00"), partner_share - settled).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    over_settled = max(Decimal("0.00"), settled - partner_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if over_settled:
        status = "over_settled"
    elif outstanding:
        status = "partially_settled" if settled else "outstanding"
    else:
        status = "settled"
    return {
        "gross": gross.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "refunds": refunds.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "expense_deductions": expense_deductions.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "payout_base": payout_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "partner_share": partner_share,
        "company_share": company_share,
        "settled": settled.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "outstanding": outstanding,
        "over_settled": over_settled,
        "status": status,
    }


def as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_operational_report(start_date, end_date, timezone_name):
    from app.models import Booking, Expense, PartnerSettlement, Payment, Vehicle

    zone = ZoneInfo(timezone_name)
    start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=zone)
    end_exclusive_local = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=zone)
    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_exclusive_local.astimezone(timezone.utc)
    period_seconds = Decimal(str((end_utc - start_utc).total_seconds()))
    period_days = (end_date - start_date).days + 1

    bookings = Booking.query.order_by(Booking.pickup_at).all()
    period_bookings = [item for item in bookings if start_utc <= as_utc(item.pickup_at) < end_utc]
    report_bookings = [item for item in period_bookings if item.status != "cancelled"]
    vehicles = Vehicle.query.filter_by(is_archived=False).order_by(Vehicle.internal_id).all()
    occupancies = []
    total_booked_seconds = Decimal("0.00")
    for vehicle in vehicles:
        booked_seconds = Decimal("0.00")
        for booking in bookings:
            if booking.vehicle_id != vehicle.id or booking.status not in {"confirmed", "active", "completed"}:
                continue
            pickup_value = booking.actual_pickup_at or booking.pickup_at if booking.status in {"active", "completed"} else booking.pickup_at
            pickup = as_utc(pickup_value)
            if booking.status == "completed" and booking.actual_return_at:
                returned = as_utc(booking.actual_return_at)
            elif booking.status == "active":
                returned = min(now_utc(), end_utc)
            else:
                returned = as_utc(booking.return_at)
            seconds = max(Decimal("0"), Decimal(str((min(returned, end_utc) - max(pickup, start_utc)).total_seconds())))
            booked_seconds += seconds
        total_booked_seconds += booked_seconds
        booked_days = (booked_seconds / Decimal("86400")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        denominator = period_seconds
        utilization = (booked_seconds * Decimal("100") / denominator).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if denominator else Decimal("0.00")
        occupancies.append({"vehicle": vehicle, "booked_days": booked_days, "utilization_percent": utilization})
    fleet_denominator = period_seconds * len(vehicles)
    fleet_utilization = (
        total_booked_seconds * Decimal("100") / fleet_denominator
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if fleet_denominator else Decimal("0.00")

    expected_rental_charges = sum((item.expected_rental_total for item in report_bookings), Decimal("0.00"))
    earned_gross_rentals = sum((item.expected_rental_total for item in report_bookings if item.status in {"active", "completed"}), Decimal("0.00"))
    period_payments = [payment for payment in Payment.query.all() if start_utc <= as_utc(payment.transacted_at) < end_utc]
    payments_received = sum((item.amount for item in period_payments if item.transaction_type == "payment"), Decimal("0.00"))
    refunds_recorded = sum((item.amount for item in period_payments if item.transaction_type == "refund"), Decimal("0.00"))
    expenses = [item for item in Expense.query.all() if start_utc <= as_utc(item.incurred_at) < end_utc]
    total_expenses = sum((item.amount for item in expenses), Decimal("0.00"))
    expenses_by_vehicle = {}
    for expense in expenses:
        expenses_by_vehicle.setdefault(expense.vehicle_id, Decimal("0.00"))
        expenses_by_vehicle[expense.vehicle_id] += expense.amount

    outstanding_customer = Decimal("0.00")
    booking_rows = []
    for booking in report_bookings:
        _paid, _refunded, balance = booking_payment_totals(booking)
        outstanding_customer += balance
        booking_rows.append({"booking": booking, "balance": balance})

    expense_vehicle_ids = list(expenses_by_vehicle)
    expense_vehicles = Vehicle.query.filter(Vehicle.id.in_(expense_vehicle_ids)).all() if expense_vehicle_ids else []
    expense_rows = [{"vehicle": vehicle, "amount": expenses_by_vehicle[vehicle.id]} for vehicle in expense_vehicles]

    settlements = [
        settlement for settlement in PartnerSettlement.query.all()
        if start_utc <= as_utc(settlement.booking.pickup_at) < end_utc
    ]
    partner_summary = {}
    for settlement in settlements:
        data = settlement_calculation(settlement)
        partner = settlement.partner
        row = partner_summary.setdefault(partner.id, {
            "partner": partner, "gross": Decimal("0.00"), "share": Decimal("0.00"),
            "settled": Decimal("0.00"), "outstanding": Decimal("0.00"),
        })
        row["gross"] += data["gross"]
        row["share"] += data["partner_share"]
        row["settled"] += data["settled"]
        row["outstanding"] += data["outstanding"]

    for name, row in partner_summary.items():
        for key in ("gross", "share", "settled", "outstanding"):
            row[key] = row[key].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "period_days": period_days,
        "fleet_vehicle_count": len(vehicles),
        "fleet_utilization_percent": fleet_utilization,
        "vehicle_utilization": occupancies,
        "bookings": report_bookings,
        "booking_rows": booking_rows,
        "expected_rental_charges": expected_rental_charges.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "gross_rental_amount": earned_gross_rentals.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "payments_received": payments_received.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "refunds_recorded": refunds_recorded.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "net_customer_receipts": (payments_received - refunds_recorded).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_vehicle_expenses": total_expenses.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "expenses_by_vehicle": expenses_by_vehicle,
        "expense_rows": expense_rows,
        "outstanding_customer_balance": outstanding_customer.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "partner_summaries": sorted(partner_summary.values(), key=lambda row: row["partner"].name.lower()),
        "settlements": settlements,
    }


def now_utc():
    return datetime.now(timezone.utc)


def parse_local_datetime(value, timezone_name):
    try:
        local = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter a valid date and time.") from exc
    zone = ZoneInfo(timezone_name)
    aware_local = local.replace(tzinfo=zone)
    # Reject local times that do not round-trip, such as a skipped DST hour.
    if aware_local.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != local:
        raise ValueError("That local time does not exist because of a timezone change.")
    return aware_local.astimezone(timezone.utc)


def display_local_datetime(value, timezone_name):
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")


def calculate_booking_total(pickup_at, return_at, daily_rate, additional_charges=Decimal("0.00"), discount=Decimal("0.00")):
    duration_seconds = Decimal(str((return_at - pickup_at).total_seconds()))
    billable_days = max(1, int(math.ceil(duration_seconds / Decimal("86400"))))
    rate = Decimal(daily_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    charges = Decimal(additional_charges or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    discount_value = Decimal(discount or 0).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = rate * billable_days + charges - discount_value
    if total < 0:
        raise ValueError("Discount cannot exceed the rental charges and additional charges.")
    return billable_days, total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def has_vehicle_booking_conflict(vehicle_id, pickup_at, return_at, exclude_booking_id=None):
    from app.models import Booking

    query = Booking.query.filter(
        Booking.vehicle_id == vehicle_id,
        or_(
            and_(
                Booking.status == "active",
                or_(Booking.return_at > pickup_at, Booking.return_at <= now_utc()),
            ),
            and_(Booking.status == "confirmed", Booking.pickup_at < return_at, Booking.return_at > pickup_at),
        ),
    )
    if exclude_booking_id is not None:
        query = query.filter(Booking.id != exclude_booking_id)
    return query.first() is not None
