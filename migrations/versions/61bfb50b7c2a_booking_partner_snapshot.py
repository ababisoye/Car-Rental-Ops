"""preserve partner identity on bookings

Revision ID: 61bfb50b7c2a
Revises: 8c70ac7d1554
Create Date: 2026-10-21

"""
from alembic import op
import sqlalchemy as sa


revision = "61bfb50b7c2a"
down_revision = "8c70ac7d1554"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        # SQLite supports a nullable REFERENCES column through direct ALTER TABLE,
        # while Alembic's generic add_column tries a separate unsupported ALTER.
        op.execute(sa.text(
            "ALTER TABLE bookings ADD COLUMN partner_id_snapshot INTEGER "
            "REFERENCES partners (id)"
        ))
    else:
        op.add_column(
            "bookings",
            sa.Column(
                "partner_id_snapshot", sa.Integer(),
                sa.ForeignKey("partners.id"), nullable=True,
            ),
        )
    op.create_index("ix_bookings_partner_id_snapshot", "bookings", ["partner_id_snapshot"], unique=False)

    # Prefer the partner on an existing settlement, then the captured agreement;
    # use the current vehicle owner only as a fallback for legacy bookings.
    op.execute(sa.text("""
        UPDATE bookings
        SET partner_id_snapshot = COALESCE(
            (SELECT partner_settlements.partner_id
             FROM partner_settlements
             WHERE partner_settlements.booking_id = bookings.id),
            (SELECT partner_agreements.partner_id
             FROM partner_agreements
             WHERE partner_agreements.id = bookings.partner_agreement_id),
            (SELECT vehicles.partner_id
             FROM vehicles
             WHERE vehicles.id = bookings.vehicle_id
               AND vehicles.ownership_type = 'partner_owned')
        )
        WHERE partner_id_snapshot IS NULL
          AND (
            EXISTS (SELECT 1 FROM partner_settlements WHERE partner_settlements.booking_id = bookings.id)
            OR EXISTS (SELECT 1 FROM partner_agreements WHERE partner_agreements.id = bookings.partner_agreement_id)
            OR EXISTS (SELECT 1 FROM vehicles WHERE vehicles.id = bookings.vehicle_id AND vehicles.ownership_type = 'partner_owned')
          )
    """))


def downgrade():
    op.drop_index("ix_bookings_partner_id_snapshot", table_name="bookings")
    if op.get_bind().dialect.name == "sqlite":
        op.execute(sa.text("ALTER TABLE bookings DROP COLUMN partner_id_snapshot"))
    else:
        op.drop_column("bookings", "partner_id_snapshot")
