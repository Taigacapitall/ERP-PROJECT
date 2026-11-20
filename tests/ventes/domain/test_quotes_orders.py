from datetime import date
from decimal import Decimal

import pytest

from vorpal.core import Currency, Money
from vorpal.modules.ventes.domain.errors import (
    SalesConflictError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.orders import (
    OrderLine,
    OrderStatus,
    SalesOrder,
)
from vorpal.modules.ventes.domain.quotes import (
    Quote,
    QuoteLine,
    QuoteStatus,
)


def money(value: str, currency: Currency = Currency.USD) -> Money:
    return Money(Decimal(value), currency)


def quote_line(
    quantity: str = "2",
    price: str = "100",
    **kwargs: object,
) -> QuoteLine:
    return QuoteLine(
        item_code="ART-001",
        designation="Ordinateur",
        quantity=Decimal(quantity),
        unit_price_ht=money(price),
        **kwargs,
    )


def make_quote(
    status: QuoteStatus = QuoteStatus.DRAFT,
    valid_until: date = date(2026, 12, 31),
    lines: tuple[QuoteLine, ...] | None = None,
    converted_order_id: str | None = None,
) -> Quote:
    return Quote(
        id="Q-001",
        number="DEV-001",
        customer_id="C-001",
        quote_date=date(2026, 11, 1),
        valid_until=valid_until,
        currency=Currency.USD,
        lines=lines if lines is not None else (quote_line(),),
        status=status,
        converted_order_id=converted_order_id,
    )


def order_line(
    line_id: str = "L-001",
    quantity: str = "10",
    invoiced: str = "0",
    **kwargs: object,
) -> OrderLine:
    return OrderLine(
        line_id=line_id,
        item_code="ART-001",
        designation="Ordinateur",
        quantity=Decimal(quantity),
        unit_price_ht=money("100"),
        invoiced_quantity=Decimal(invoiced),
        **kwargs,
    )


def make_order(
    status: OrderStatus = OrderStatus.DRAFT,
    lines: tuple[OrderLine, ...] | None = None,
) -> SalesOrder:
    return SalesOrder(
        id="O-001",
        number="CMD-001",
        customer_id="C-001",
        order_date=date(2026, 11, 1),
        currency=Currency.USD,
        lines=lines if lines is not None else (order_line(),),
        status=status,
    )


class TestQuoteLine:
    def test_valid_line(self) -> None:
        line = quote_line()
        assert line.quantity == Decimal("2")
        assert line.discount_percent == Decimal("0")

    @pytest.mark.parametrize("designation", ["", " "])
    def test_rejects_empty_designation(self, designation: str) -> None:
        with pytest.raises(SalesValidationError):
            QuoteLine(
                item_code="ART-001",
                designation=designation,
                quantity=Decimal("1"),
                unit_price_ht=money("10"),
            )

    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_rejects_invalid_quantity(self, quantity: str) -> None:
        with pytest.raises(SalesValidationError):
            quote_line(quantity=quantity)

    def test_rejects_float_quantity(self) -> None:
        with pytest.raises(SalesValidationError):
            QuoteLine(
                item_code="ART-001",
                designation="Article",
                quantity=1.0,  # type: ignore[arg-type]
                unit_price_ht=money("10"),
            )

    def test_rejects_negative_price(self) -> None:
        with pytest.raises(SalesValidationError):
            quote_line(price="-1")

    @pytest.mark.parametrize("discount", ["-1", "100.01"])
    def test_rejects_invalid_discount(self, discount: str) -> None:
        with pytest.raises(SalesValidationError):
            quote_line(discount_percent=Decimal(discount))

    def test_rejects_negative_vat(self) -> None:
        with pytest.raises(SalesValidationError):
            quote_line(vat_rate=Decimal("-0.01"))

    def test_out_of_scope_requires_item_code(self) -> None:
        with pytest.raises(SalesValidationError):
            QuoteLine(
                item_code=None,
                designation="Service hors champ",
                quantity=Decimal("1"),
                unit_price_ht=money("10"),
                is_out_of_scope=True,
            )


class TestQuote:
    def test_valid_quote(self) -> None:
        quote = make_quote()
        assert quote.status == QuoteStatus.DRAFT
        assert quote.customer_id == "C-001"

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("id", ""),
            ("number", ""),
            ("customer_id", ""),
        ],
    )
    def test_rejects_missing_required_identifiers(
        self, field: str, value: str
    ) -> None:
        values = {
            "id": "Q-001",
            "number": "DEV-001",
            "customer_id": "C-001",
            "quote_date": date(2026, 11, 1),
            "valid_until": date(2026, 12, 31),
            "currency": Currency.USD,
            "lines": (quote_line(),),
        }
        values[field] = value
        with pytest.raises(SalesValidationError):
            Quote(**values)  # type: ignore[arg-type]

    def test_rejects_validity_before_quote_date(self) -> None:
        with pytest.raises(SalesValidationError):
            make_quote(valid_until=date(2026, 10, 31))

    def test_rejects_empty_lines(self) -> None:
        with pytest.raises(SalesValidationError):
            make_quote(lines=())

    def test_rejects_mixed_currency(self) -> None:
        line = QuoteLine(
            item_code="ART-001",
            designation="Article",
            quantity=Decimal("1"),
            unit_price_ht=money("10", Currency.CDF),
        )
        with pytest.raises(SalesValidationError):
            make_quote(lines=(line,))

    def test_computes_totals(self) -> None:
        quote = make_quote(lines=(quote_line(quantity="2", price="100"),))
        totals = quote.compute_totals()
        assert totals.total_ht == money("200.00")
        assert totals.total_vat == money("32.00")
        assert totals.total_ttc == money("232.00")

    def test_send_draft_quote(self) -> None:
        sent = make_quote().send(date(2026, 11, 20))
        assert sent.status == QuoteStatus.SENT
        assert make_quote().status == QuoteStatus.DRAFT

    def test_cannot_send_non_draft_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote(status=QuoteStatus.SENT).send(date(2026, 11, 20))

    def test_cannot_send_expired_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote(valid_until=date(2026, 11, 19)).send(date(2026, 11, 20))

    def test_can_send_on_last_validity_day(self) -> None:
        sent = make_quote(valid_until=date(2026, 11, 20)).send(date(2026, 11, 20))
        assert sent.status == QuoteStatus.SENT

    def test_mark_expired(self) -> None:
        expired = make_quote().mark_expired(date(2027, 1, 1))
        assert expired.status == QuoteStatus.EXPIRED

    def test_cannot_expire_before_deadline(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote().mark_expired(date(2026, 12, 31))

    def test_cannot_expire_rejected_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote(status=QuoteStatus.REJECTED).mark_expired(date(2027, 1, 1))

    def test_accept_sent_quote(self) -> None:
        accepted = make_quote(status=QuoteStatus.SENT).accept(date(2026, 11, 20))
        assert accepted.status == QuoteStatus.ACCEPTED

    def test_cannot_accept_draft_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote().accept(date(2026, 11, 20))

    def test_cannot_accept_expired_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote(
                status=QuoteStatus.SENT,
                valid_until=date(2026, 11, 19),
            ).accept(date(2026, 11, 20))

    def test_reject_sent_quote(self) -> None:
        rejected = make_quote(status=QuoteStatus.SENT).reject()
        assert rejected.status == QuoteStatus.REJECTED

    def test_cannot_reject_draft_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote().reject()

    def test_convert_accepted_quote(self) -> None:
        converted = make_quote(status=QuoteStatus.ACCEPTED).mark_converted("O-001")
        assert converted.status == QuoteStatus.CONVERTED
        assert converted.converted_order_id == "O-001"

    def test_cannot_convert_unaccepted_quote(self) -> None:
        with pytest.raises(SalesConflictError):
            make_quote().mark_converted("O-001")

    def test_cannot_convert_twice(self) -> None:
        quote = make_quote(
            status=QuoteStatus.CONVERTED,
            converted_order_id="O-001",
        )
        with pytest.raises(SalesConflictError):
            quote.mark_converted("O-002")

    def test_conversion_requires_order_id(self) -> None:
        with pytest.raises(SalesValidationError):
            make_quote(status=QuoteStatus.ACCEPTED).mark_converted(" ")


class TestOrderLine:
    def test_remaining_quantity(self) -> None:
        line = order_line(quantity="10", invoiced="3")
        assert line.remaining_quantity == Decimal("7")

    def test_remaining_quantity_when_not_invoiced(self) -> None:
        assert order_line().remaining_quantity == Decimal("10")

    def test_rejects_empty_line_id(self) -> None:
        with pytest.raises(SalesValidationError):
            order_line(line_id=" ")

    def test_rejects_empty_designation(self) -> None:
        with pytest.raises(SalesValidationError):
            OrderLine(
                line_id="L-001",
                item_code="ART-001",
                designation=" ",
                quantity=Decimal("1"),
                unit_price_ht=money("10"),
            )


    @pytest.mark.parametrize("quantity", ["0", "-1"])
    def test_rejects_non_positive_quantity(self, quantity: str) -> None:
        with pytest.raises(SalesValidationError):
            order_line(quantity=quantity)

    def test_rejects_negative_unit_price(self) -> None:
        with pytest.raises(SalesValidationError):
            OrderLine(
                line_id="L-001",
                item_code="ART-001",
                designation="Ordinateur",
                quantity=Decimal("1"),
                unit_price_ht=money("-10"),
            )

    def test_rejects_invoiced_quantity_above_ordered(self) -> None:
        with pytest.raises(SalesValidationError):
            order_line(quantity="5", invoiced="6")

    def test_rejects_negative_invoiced_quantity(self) -> None:
        with pytest.raises(SalesValidationError):
            order_line(invoiced="-1")

    def test_rejects_invalid_discount(self) -> None:
        with pytest.raises(SalesValidationError):
            order_line(discount_percent=Decimal("101"))

    def test_rejects_negative_vat(self) -> None:
        with pytest.raises(SalesValidationError):
            order_line(vat_rate=Decimal("-0.01"))

    def test_out_of_scope_requires_item_code(self) -> None:
        with pytest.raises(SalesValidationError):
            OrderLine(
                line_id="L-001",
                item_code=None,
                designation="Service",
                quantity=Decimal("1"),
                unit_price_ht=money("10"),
                is_out_of_scope=True,
            )


class TestSalesOrder:
    def test_valid_order(self) -> None:
        order = make_order()
        assert order.status == OrderStatus.DRAFT

    def test_rejects_missing_id(self) -> None:
        with pytest.raises(SalesValidationError):
            SalesOrder(
                id=" ",
                number="CMD-001",
                customer_id="C-001",
                order_date=date(2026, 11, 1),
                currency=Currency.USD,
                lines=(order_line(),),
            )

    def test_rejects_missing_number(self) -> None:
        with pytest.raises(SalesValidationError):
            SalesOrder(
                id="O-001",
                number="",
                customer_id="C-001",
                order_date=date(2026, 11, 1),
                currency=Currency.USD,
                lines=(order_line(),),
            )

    def test_rejects_missing_customer(self) -> None:
        with pytest.raises(SalesValidationError):
            SalesOrder(
                id="O-001",
                number="CMD-001",
                customer_id=" ",
                order_date=date(2026, 11, 1),
                currency=Currency.USD,
                lines=(order_line(),),
            )

    def test_rejects_empty_lines(self) -> None:
        with pytest.raises(SalesValidationError):
            make_order(lines=())

    def test_rejects_duplicate_line_ids(self) -> None:
        with pytest.raises(SalesValidationError, match="double"):
            make_order(lines=(order_line(), order_line()))

    def test_rejects_mixed_currency(self) -> None:
        line = OrderLine(
            line_id="L-002",
            item_code="ART-002",
            designation="Article CDF",
            quantity=Decimal("1"),
            unit_price_ht=money("10", Currency.CDF),
        )
        with pytest.raises(SalesValidationError):
            make_order(lines=(line,))

    def test_rejects_deposit_in_wrong_currency(self) -> None:
        with pytest.raises(SalesValidationError):
            SalesOrder(
                id="O-001",
                number="CMD-001",
                customer_id="C-001",
                order_date=date(2026, 11, 1),
                currency=Currency.USD,
                lines=(order_line(),),
                deposit_required=money("10", Currency.CDF),
            )

    def test_rejects_negative_deposit(self) -> None:
        with pytest.raises(SalesValidationError):
            SalesOrder(
                id="O-001",
                number="CMD-001",
                customer_id="C-001",
                order_date=date(2026, 11, 1),
                currency=Currency.USD,
                lines=(order_line(),),
                deposit_required=money("-1"),
            )

    def test_computes_totals(self) -> None:
        totals = make_order().compute_totals()
        assert totals.total_ht == money("1000.00")
        assert totals.total_vat == money("160.00")
        assert totals.total_ttc == money("1160.00")

    def test_updates_draft_lines(self) -> None:
        order = make_order()
        new_lines = (order_line(line_id="L-002"),)
        updated = order.update_lines(new_lines)
        assert updated.lines == new_lines
        assert order.lines != new_lines

    def test_cannot_update_confirmed_order(self) -> None:
        with pytest.raises(SalesConflictError):
            make_order(status=OrderStatus.CONFIRMED).update_lines((order_line(),))

    def test_confirms_draft_order(self) -> None:
        assert make_order().confirm().status == OrderStatus.CONFIRMED

    def test_cannot_confirm_twice(self) -> None:
        with pytest.raises(SalesConflictError):
            make_order(status=OrderStatus.CONFIRMED).confirm()

    def test_cancels_draft_order(self) -> None:
        assert make_order().cancel().status == OrderStatus.CANCELLED

    def test_cancels_confirmed_order(self) -> None:
        assert make_order(status=OrderStatus.CONFIRMED).cancel().status == OrderStatus.CANCELLED

    def test_cannot_cancel_invoiced_order(self) -> None:
        with pytest.raises(SalesConflictError):
            make_order(status=OrderStatus.INVOICED).cancel()

    def test_cannot_cancel_partially_invoiced_order(self) -> None:
        order = make_order(
            status=OrderStatus.CONFIRMED,
            lines=(order_line(invoiced="2"),),
        )
        with pytest.raises(SalesConflictError):
            order.cancel()

    def test_records_partial_invoicing(self) -> None:
        order = make_order(status=OrderStatus.CONFIRMED)
        updated = order.record_invoiced_quantities({"L-001": Decimal("4")})
        assert updated.status == OrderStatus.PARTIALLY_INVOICED
        assert updated.lines[0].invoiced_quantity == Decimal("4")
        assert updated.lines[0].remaining_quantity == Decimal("6")

    def test_records_full_invoicing(self) -> None:
        order = make_order(status=OrderStatus.CONFIRMED)
        updated = order.record_invoiced_quantities({"L-001": Decimal("10")})
        assert updated.status == OrderStatus.INVOICED
        assert updated.lines[0].remaining_quantity == Decimal("0")

    def test_completes_invoicing_in_multiple_steps(self) -> None:
        order = make_order(status=OrderStatus.CONFIRMED)
        partial = order.record_invoiced_quantities({"L-001": Decimal("4")})
        complete = partial.record_invoiced_quantities({"L-001": Decimal("6")})
        assert complete.status == OrderStatus.INVOICED

    def test_rejects_invoicing_on_draft_order(self) -> None:
        with pytest.raises(SalesConflictError):
            make_order().record_invoiced_quantities({"L-001": Decimal("1")})

    def test_rejects_empty_billing_data(self) -> None:
        with pytest.raises(SalesValidationError):
            make_order(status=OrderStatus.CONFIRMED).record_invoiced_quantities({})

    def test_rejects_unknown_line(self) -> None:
        with pytest.raises(SalesValidationError, match="introuvable"):
            make_order(status=OrderStatus.CONFIRMED).record_invoiced_quantities(
                {"UNKNOWN": Decimal("1")}
            )

    def test_rejects_negative_billed_quantity(self) -> None:
        with pytest.raises(SalesValidationError):
            make_order(status=OrderStatus.CONFIRMED).record_invoiced_quantities(
                {"L-001": Decimal("-1")}
            )

    def test_rejects_float_billed_quantity(self) -> None:
        with pytest.raises(SalesValidationError):
            make_order(status=OrderStatus.CONFIRMED).record_invoiced_quantities(
                {"L-001": 1.0}  # type: ignore[dict-item]
            )

    def test_rejects_billing_above_remaining_quantity(self) -> None:
        with pytest.raises(SalesConflictError):
            make_order(status=OrderStatus.CONFIRMED).record_invoiced_quantities(
                {"L-001": Decimal("11")}
            )

    def test_billing_can_leave_other_lines_untouched(self) -> None:
        order = make_order(
            status=OrderStatus.CONFIRMED,
            lines=(
                order_line("L-001", "5"),
                order_line("L-002", "3"),
            ),
        )
        updated = order.record_invoiced_quantities({"L-001": Decimal("5")})
        assert updated.status == OrderStatus.PARTIALLY_INVOICED
        assert updated.lines[0].remaining_quantity == Decimal("0")
        assert updated.lines[1].remaining_quantity == Decimal("3")
