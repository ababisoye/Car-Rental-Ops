from datetime import date

from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, DecimalField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional, Regexp, ValidationError


class PartnerForm(FlaskForm):
    name = StringField("Contact name", validators=[DataRequired(), Length(max=160)])
    business_name = StringField("Business name", validators=[Optional(), Length(max=160)])
    email = StringField("Email", validators=[Optional(), Length(max=254), Regexp(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", message="Enter a valid email address.")])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    address = StringField("Address", validators=[Optional(), Length(max=300)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])
    is_active = BooleanField("Active partner", default=True)
    submit = SubmitField("Save partner")


class ConfirmForm(FlaskForm):
    submit = SubmitField("Confirm")


class PartnerAgreementForm(FlaskForm):
    agreement_type = SelectField(
        "Payout arrangement", choices=[("", "Choose arrangement"), ("fixed_per_rental", "Fixed payout per rental"), ("percentage", "Percentage of gross rental")],
        validators=[DataRequired()],
    )
    fixed_payout = DecimalField("Fixed payout per rental", places=2, validators=[Optional(), NumberRange(min=0.01, message="Enter an amount above zero.")])
    revenue_share_percentage = DecimalField("Partner share (%)", places=2, validators=[Optional(), NumberRange(min=0.01, max=100, message="Enter a percentage from 0.01 to 100.")])
    effective_from = DateField("Effective from", validators=[DataRequired()])
    effective_to = DateField("Effective to (optional)", validators=[Optional()])
    notes = TextAreaField("Terms notes", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Save agreement")

    def validate_effective_to(self, field):
        if field.data and self.effective_from.data and field.data < self.effective_from.data:
            raise ValidationError("End date must be on or after the start date.")

    def validate(self, extra_validators=None):
        valid = super().validate(extra_validators=extra_validators)
        if self.agreement_type.data == "fixed_per_rental":
            if self.fixed_payout.data is None:
                self.fixed_payout.errors.append("Enter the agreed fixed payout.")
                valid = False
            if self.revenue_share_percentage.data is not None:
                self.revenue_share_percentage.errors.append("Percentage must be blank for a fixed payout.")
                valid = False
        elif self.agreement_type.data == "percentage":
            if self.revenue_share_percentage.data is None:
                self.revenue_share_percentage.errors.append("Enter the agreed percentage.")
                valid = False
            if self.fixed_payout.data is not None:
                self.fixed_payout.errors.append("Fixed payout must be blank for a percentage agreement.")
                valid = False
        return valid


class VehicleForm(FlaskForm):
    internal_id = StringField("Internal vehicle ID", validators=[DataRequired(), Length(max=40), Regexp(r"^[A-Za-z0-9_-]+$", message="Use letters, numbers, hyphens, or underscores.")])
    registration_number = StringField("Registration number", validators=[DataRequired(), Length(max=24)])
    make = StringField("Make", validators=[DataRequired(), Length(max=80)])
    model = StringField("Model", validators=[DataRequired(), Length(max=80)])
    year = StringField("Year", validators=[DataRequired(), Regexp(r"^\d{4}$", message="Enter a four-digit year.")])
    color = StringField("Color", validators=[Optional(), Length(max=40)])
    transmission = SelectField("Transmission", choices=[("", "Not specified"), ("automatic", "Automatic"), ("manual", "Manual"), ("other", "Other")], validators=[Optional()])
    fuel_type = SelectField("Fuel type", choices=[("", "Not specified"), ("petrol", "Petrol"), ("diesel", "Diesel"), ("hybrid", "Hybrid"), ("electric", "Electric"), ("other", "Other")], validators=[Optional()])
    ownership_type = SelectField("Ownership", choices=[("company_owned", "Company owned"), ("partner_owned", "Partner owned")], validators=[DataRequired()])
    partner_id = SelectField("Partner", coerce=int, choices=[(0, "Select a partner")], validators=[Optional()])
    daily_rate = DecimalField("Default daily rental rate (₦ per day)", places=2, validators=[InputRequired(), NumberRange(min=0.01, message="Enter a daily rate above zero.")])
    operational_status = SelectField("Operational status", choices=[("operational", "Operational"), ("maintenance", "In maintenance"), ("retired", "Retired")], validators=[DataRequired()])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Save vehicle")

    def validate_year(self, field):
        if field.data:
            year = int(field.data)
            if year < 1886 or year > date.today().year + 1:
                raise ValidationError("Enter a valid vehicle year.")


class CustomerForm(FlaskForm):
    name = StringField("Customer name", validators=[DataRequired(), Length(max=160)])
    email = StringField("Email", validators=[Optional(), Length(max=254), Regexp(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", message="Enter a valid email address.")])
    phone = StringField("Phone", validators=[Optional(), Length(max=40)])
    address = StringField("Address", validators=[Optional(), Length(max=300)])
    notes = TextAreaField("Internal notes", validators=[Optional(), Length(max=4000)])
    is_active = BooleanField("Active customer", default=True)
    submit = SubmitField("Save customer")


class BookingForm(FlaskForm):
    customer_id = SelectField("Customer", coerce=int, choices=[(0, "Select a customer")], validators=[DataRequired()])
    vehicle_id = SelectField("Vehicle", coerce=int, choices=[(0, "Select an available vehicle")], validators=[DataRequired()])
    pickup_local = StringField("Pickup date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    return_local = StringField("Return date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    agreed_daily_rate = DecimalField("Agreed daily rate (₦)", places=2, validators=[DataRequired(), NumberRange(min=0, message="Rate cannot be negative.")])
    additional_charges = DecimalField("Additional charges (₦)", places=2, validators=[Optional(), NumberRange(min=0, message="Charges cannot be negative.")])
    discount_amount = DecimalField("Discount (₦)", places=2, validators=[Optional(), NumberRange(min=0, message="Discount cannot be negative.")])
    payment_confirmed = BooleanField("I confirm payment has been received and verified", validators=[InputRequired(message="Confirm payment has been received before creating this booking.")])
    initial_payment_amount = DecimalField("Amount received (₦)", places=2, validators=[DataRequired(), NumberRange(min=0.01, message="Enter an amount above zero.")])
    initial_payment_method = SelectField("Recorded payment method", choices=[
        ("cash", "Cash"), ("bank_transfer", "Bank transfer"), ("cheque", "Cheque"),
        ("card_manual", "Card (recorded manually)"), ("other", "Other"),
    ], validators=[DataRequired()])
    initial_payment_reference = StringField("Payment reference (optional)", validators=[Optional(), Length(max=120)])
    initial_payment_local = StringField("Payment date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    initial_status = SelectField("Initial status", choices=[("pending", "Pending"), ("confirmed", "Confirmed")], validators=[DataRequired()])
    notes = TextAreaField("Booking notes", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Create booking")


class PaymentForm(FlaskForm):
    transaction_type = SelectField("Transaction", choices=[("payment", "Customer payment"), ("refund", "Refund recorded")], validators=[DataRequired()])
    amount = DecimalField("Amount (₦)", places=2, validators=[DataRequired(), NumberRange(min=0.01, message="Enter an amount above zero.")])
    payment_method = SelectField("Recorded method", choices=[
        ("cash", "Cash"), ("bank_transfer", "Bank transfer"), ("cheque", "Cheque"),
        ("card_manual", "Card (recorded manually)"), ("other", "Other"),
    ], validators=[DataRequired()])
    reference = StringField("Reference (optional)", validators=[Optional(), Length(max=120)])
    transacted_local = StringField("Transaction date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Record transaction")


class ExpenseForm(FlaskForm):
    vehicle_id = SelectField("Vehicle", coerce=int, choices=[(0, "Select a vehicle")], validators=[DataRequired()])
    category = SelectField("Expense category", choices=[
        ("maintenance", "Maintenance"), ("fuel", "Fuel"), ("cleaning", "Cleaning"),
        ("insurance", "Insurance"), ("licensing", "Licensing"), ("repair", "Repair"), ("other", "Other"),
    ], validators=[DataRequired()])
    amount = DecimalField("Amount (₦)", places=2, validators=[DataRequired(), NumberRange(min=0.01, message="Enter an amount above zero.")])
    payment_method = SelectField("Recorded payment method", choices=[
        ("cash", "Cash"), ("bank_transfer", "Bank transfer"), ("cheque", "Cheque"),
        ("card_manual", "Card (recorded manually)"), ("other", "Other"),
    ], validators=[DataRequired()])
    reference = StringField("Reference (optional)", validators=[Optional(), Length(max=120)])
    incurred_local = StringField("Expense date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Record expense")


class SettlementAdjustmentForm(FlaskForm):
    adjustment_type = SelectField("Adjustment", choices=[
        ("expense_deduction", "Deduct an approved vehicle expense from payout base"),
        ("partner_credit", "Increase partner share"), ("partner_debit", "Reduce partner share"),
    ], validators=[DataRequired()])
    expense_id = SelectField("Expense record", coerce=int, choices=[(0, "Select expense")], validators=[Optional()])
    amount = DecimalField("Amount (₦)", places=2, validators=[DataRequired(), NumberRange(min=0.01, message="Enter an amount above zero.")])
    effective_local = StringField("Effective date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    note = StringField("Reason / commercial authorization", validators=[DataRequired(), Length(max=500)])
    submit = SubmitField("Record adjustment")


class SettlementPaymentForm(FlaskForm):
    amount = DecimalField("Amount settled (₦)", places=2, validators=[DataRequired(), NumberRange(min=0.01, message="Enter an amount above zero.")])
    payment_method = SelectField("Recorded method", choices=[
        ("cash", "Cash"), ("bank_transfer", "Bank transfer"), ("cheque", "Cheque"),
        ("card_manual", "Card (recorded manually)"), ("other", "Other"),
    ], validators=[DataRequired()])
    reference = StringField("Reference (optional)", validators=[Optional(), Length(max=120)])
    settled_local = StringField("Settlement date and time (Africa/Lagos)", validators=[DataRequired(), Regexp(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", message="Enter a valid local date and time.")])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])
    submit = SubmitField("Record settlement payment")
