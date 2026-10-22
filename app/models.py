from datetime import datetime, timezone

from app.extensions import db


def utc_now():
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class Partner(TimestampMixin, db.Model):
    __tablename__ = "partners"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    business_name = db.Column(db.String(160))
    email = db.Column(db.String(254))
    phone = db.Column(db.String(40))
    address = db.Column(db.String(300))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    agreements = db.relationship(
        "PartnerAgreement", back_populates="partner", passive_deletes=True,
        order_by="PartnerAgreement.effective_from.desc()",
    )
    vehicles = db.relationship("Vehicle", back_populates="partner", lazy="select")
    settlements = db.relationship("PartnerSettlement", back_populates="partner", passive_deletes=True)


class PartnerAgreement(TimestampMixin, db.Model):
    __tablename__ = "partner_agreements"

    id = db.Column(db.Integer, primary_key=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False, index=True)
    agreement_type = db.Column(db.String(24), nullable=False)
    fixed_payout = db.Column(db.Numeric(12, 2))
    revenue_share_percentage = db.Column(db.Numeric(5, 2))
    percentage_basis = db.Column(db.String(24))
    effective_from = db.Column(db.Date, nullable=False)
    effective_to = db.Column(db.Date)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    partner = db.relationship("Partner", back_populates="agreements")

    __table_args__ = (
        db.CheckConstraint("agreement_type IN ('fixed_per_rental', 'percentage')", name="ck_agreement_type"),
        db.CheckConstraint(
            "(agreement_type = 'fixed_per_rental' AND fixed_payout > 0 AND revenue_share_percentage IS NULL) OR "
            "(agreement_type = 'percentage' AND revenue_share_percentage > 0 AND revenue_share_percentage <= 100 AND fixed_payout IS NULL)",
            name="ck_agreement_terms",
        ),
        db.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_agreement_dates"),
        db.CheckConstraint("percentage_basis IS NULL OR percentage_basis = 'gross_rental'", name="ck_agreement_basis"),
    )


class Customer(TimestampMixin, db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    email = db.Column(db.String(254))
    phone = db.Column(db.String(40))
    address = db.Column(db.String(300))
    notes = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    bookings = db.relationship("Booking", back_populates="customer", passive_deletes=True)


class Vehicle(TimestampMixin, db.Model):
    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    internal_id = db.Column(db.String(40), nullable=False, unique=True)
    registration_number = db.Column(db.String(24), nullable=False, unique=True, index=True)
    make = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(80), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    color = db.Column(db.String(40))
    transmission = db.Column(db.String(20))
    fuel_type = db.Column(db.String(20))
    ownership_type = db.Column(db.String(20), nullable=False)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id", ondelete="RESTRICT"))
    # Keep legacy Phase 1 records valid when no rate was captured yet. The form
    # requires an explicit rate when new or edited records are saved.
    daily_rate = db.Column(db.Numeric(12, 2), nullable=True)
    operational_status = db.Column(db.String(20), nullable=False, default="operational")
    notes = db.Column(db.Text)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    partner = db.relationship("Partner", back_populates="vehicles")
    bookings = db.relationship("Booking", back_populates="vehicle", passive_deletes=True)

    __table_args__ = (
        db.CheckConstraint("ownership_type IN ('company_owned', 'partner_owned')", name="ck_vehicle_ownership"),
        db.CheckConstraint(
            "(ownership_type = 'company_owned' AND partner_id IS NULL) OR "
            "(ownership_type = 'partner_owned' AND partner_id IS NOT NULL)",
            name="ck_vehicle_partner_ownership",
        ),
        db.CheckConstraint("year >= 1886 AND year <= 2100", name="ck_vehicle_year"),
        db.CheckConstraint("daily_rate >= 0", name="ck_vehicle_daily_rate"),
        db.CheckConstraint("operational_status IN ('operational', 'maintenance', 'retired')", name="ck_vehicle_operational_status"),
        db.CheckConstraint("transmission IS NULL OR transmission IN ('automatic', 'manual', 'other')", name="ck_vehicle_transmission"),
        db.CheckConstraint("fuel_type IS NULL OR fuel_type IN ('petrol', 'diesel', 'hybrid', 'electric', 'other')", name="ck_vehicle_fuel_type"),
    )


class Booking(TimestampMixin, db.Model):
    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    booking_reference = db.Column(db.String(32), nullable=False, unique=True, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    pickup_at = db.Column(db.DateTime(timezone=True), nullable=False)
    return_at = db.Column(db.DateTime(timezone=True), nullable=False)
    actual_pickup_at = db.Column(db.DateTime(timezone=True))
    actual_return_at = db.Column(db.DateTime(timezone=True))
    agreed_daily_rate = db.Column(db.Numeric(12, 2), nullable=False)
    billable_days = db.Column(db.Integer, nullable=False)
    additional_charges = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    expected_rental_total = db.Column(db.Numeric(12, 2), nullable=False)
    notes = db.Column(db.Text)
    # Preserve the booking's commercial counterparty if vehicle ownership changes later.
    partner_id_snapshot = db.Column(db.Integer, db.ForeignKey("partners.id"), index=True)
    partner_agreement_id = db.Column(db.Integer, db.ForeignKey("partner_agreements.id", ondelete="RESTRICT"))
    partner_agreement_type_snapshot = db.Column(db.String(24))
    partner_fixed_payout_snapshot = db.Column(db.Numeric(12, 2))
    partner_share_percentage_snapshot = db.Column(db.Numeric(5, 2))
    partner_percentage_basis_snapshot = db.Column(db.String(24))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    customer = db.relationship("Customer", back_populates="bookings")
    vehicle = db.relationship("Vehicle", back_populates="bookings")
    partner_snapshot = db.relationship("Partner", foreign_keys=[partner_id_snapshot])
    partner_agreement = db.relationship("PartnerAgreement")
    payments = db.relationship("Payment", back_populates="booking", passive_deletes=True, order_by="Payment.transacted_at.desc()")
    settlement = db.relationship("PartnerSettlement", back_populates="booking", uselist=False, passive_deletes=True)

    __table_args__ = (
        db.CheckConstraint("status IN ('pending', 'confirmed', 'active', 'completed', 'cancelled')", name="ck_booking_status"),
        db.CheckConstraint("pickup_at < return_at", name="ck_booking_date_range"),
        db.CheckConstraint("agreed_daily_rate >= 0", name="ck_booking_daily_rate"),
        db.CheckConstraint("billable_days >= 1", name="ck_booking_billable_days"),
        db.CheckConstraint("additional_charges >= 0", name="ck_booking_additional_charges"),
        db.CheckConstraint("discount_amount >= 0", name="ck_booking_discount"),
        db.CheckConstraint("expected_rental_total >= 0", name="ck_booking_total"),
        db.CheckConstraint(
            "(partner_agreement_type_snapshot IS NULL AND partner_fixed_payout_snapshot IS NULL AND "
            "partner_share_percentage_snapshot IS NULL AND partner_percentage_basis_snapshot IS NULL) OR "
            "(partner_agreement_type_snapshot = 'fixed_per_rental' AND partner_fixed_payout_snapshot > 0 AND "
            "partner_share_percentage_snapshot IS NULL AND partner_percentage_basis_snapshot IS NULL) OR "
            "(partner_agreement_type_snapshot = 'percentage' AND partner_share_percentage_snapshot > 0 AND "
            "partner_share_percentage_snapshot <= 100 AND partner_fixed_payout_snapshot IS NULL AND "
            "partner_percentage_basis_snapshot = 'gross_rental')",
            name="ck_booking_agreement_snapshot",
        ),
    )


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False, index=True)
    transaction_type = db.Column(db.String(12), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_method = db.Column(db.String(24), nullable=False)
    reference = db.Column(db.String(120))
    transacted_at = db.Column(db.DateTime(timezone=True), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    booking = db.relationship("Booking", back_populates="payments")

    __table_args__ = (
        db.CheckConstraint("transaction_type IN ('payment', 'refund')", name="ck_payment_transaction_type"),
        db.CheckConstraint("amount > 0", name="ck_payment_amount"),
        db.CheckConstraint("payment_method IN ('cash', 'bank_transfer', 'cheque', 'card_manual', 'other')", name="ck_payment_method"),
    )


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True)
    category = db.Column(db.String(24), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_method = db.Column(db.String(24), nullable=False)
    reference = db.Column(db.String(120))
    incurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    vehicle = db.relationship("Vehicle", backref=db.backref("expenses", passive_deletes=True, order_by="Expense.incurred_at.desc()"))

    __table_args__ = (
        db.CheckConstraint("category IN ('maintenance', 'fuel', 'cleaning', 'insurance', 'licensing', 'repair', 'other')", name="ck_expense_category"),
        db.CheckConstraint("amount > 0", name="ck_expense_amount"),
        db.CheckConstraint("payment_method IN ('cash', 'bank_transfer', 'cheque', 'card_manual', 'other')", name="ck_expense_payment_method"),
    )


class PartnerSettlement(db.Model):
    __tablename__ = "partner_settlements"

    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False, unique=True)
    partner_id = db.Column(db.Integer, db.ForeignKey("partners.id", ondelete="RESTRICT"), nullable=False, index=True)
    gross_rental_amount = db.Column(db.Numeric(12, 2), nullable=False)
    agreement_type_snapshot = db.Column(db.String(24), nullable=False)
    fixed_payout_snapshot = db.Column(db.Numeric(12, 2))
    revenue_share_percentage_snapshot = db.Column(db.Numeric(5, 2))
    percentage_basis_snapshot = db.Column(db.String(24))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    booking = db.relationship("Booking", back_populates="settlement")
    partner = db.relationship("Partner", back_populates="settlements")
    adjustments = db.relationship("SettlementAdjustment", back_populates="settlement", passive_deletes=True, order_by="SettlementAdjustment.effective_at.asc()")
    payments = db.relationship("SettlementPayment", back_populates="settlement", passive_deletes=True, order_by="SettlementPayment.settled_at.asc()")

    __table_args__ = (
        db.CheckConstraint("gross_rental_amount >= 0", name="ck_settlement_gross_amount"),
        db.CheckConstraint(
            "(agreement_type_snapshot = 'fixed_per_rental' AND fixed_payout_snapshot > 0 AND revenue_share_percentage_snapshot IS NULL AND percentage_basis_snapshot IS NULL) OR "
            "(agreement_type_snapshot = 'percentage' AND revenue_share_percentage_snapshot > 0 AND revenue_share_percentage_snapshot <= 100 AND fixed_payout_snapshot IS NULL AND percentage_basis_snapshot = 'gross_rental')",
            name="ck_settlement_agreement_snapshot",
        ),
    )


class SettlementAdjustment(db.Model):
    __tablename__ = "settlement_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(db.Integer, db.ForeignKey("partner_settlements.id", ondelete="RESTRICT"), nullable=False, index=True)
    adjustment_type = db.Column(db.String(24), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    effective_at = db.Column(db.DateTime(timezone=True), nullable=False)
    expense_id = db.Column(db.Integer, db.ForeignKey("expenses.id", ondelete="RESTRICT"), unique=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("payments.id", ondelete="RESTRICT"), unique=True)
    note = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    settlement = db.relationship("PartnerSettlement", back_populates="adjustments")

    __table_args__ = (
        db.CheckConstraint("adjustment_type IN ('customer_refund', 'expense_deduction', 'partner_credit', 'partner_debit')", name="ck_settlement_adjustment_type"),
        db.CheckConstraint("amount > 0", name="ck_settlement_adjustment_amount"),
        db.CheckConstraint(
            "(adjustment_type = 'customer_refund' AND payment_id IS NOT NULL AND expense_id IS NULL) OR "
            "(adjustment_type = 'expense_deduction' AND expense_id IS NOT NULL AND payment_id IS NULL) OR "
            "(adjustment_type IN ('partner_credit', 'partner_debit') AND expense_id IS NULL AND payment_id IS NULL)",
            name="ck_settlement_adjustment_source",
        ),
    )


class SettlementPayment(db.Model):
    __tablename__ = "settlement_payments"

    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(db.Integer, db.ForeignKey("partner_settlements.id", ondelete="RESTRICT"), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    payment_method = db.Column(db.String(24), nullable=False)
    reference = db.Column(db.String(120))
    settled_at = db.Column(db.DateTime(timezone=True), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)

    settlement = db.relationship("PartnerSettlement", back_populates="payments")

    __table_args__ = (
        db.CheckConstraint("amount > 0", name="ck_settlement_payment_amount"),
        db.CheckConstraint("payment_method IN ('cash', 'bank_transfer', 'cheque', 'card_manual', 'other')", name="ck_settlement_payment_method"),
    )
