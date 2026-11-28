import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from vorpal.core import AuditLog, AuthorizationService, Currency, Money, Partner, User
from vorpal.modules.ventes.domain.catalog import SalesItem
from vorpal.modules.ventes.domain.customers import Customer
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.orders import OrderStatus, SalesOrder
from vorpal.modules.ventes.domain.quotes import Quote, QuoteLine, QuoteStatus
from vorpal.modules.ventes.services.common import LineInput
from vorpal.modules.ventes.services.orders import OrderService
from vorpal.modules.ventes.settings import RoundingSettings


class Repo:
    def __init__(self): self.data = {}
    def get(self, key): return self.data.get(key)
    def save(self, obj): self.data[obj.id] = obj
    def list_all(self, **kwargs): return list(self.data.values())

class Catalog:
    def __init__(self): self.items = {}
    def get_item(self, code): return self.items.get(code)

class Numbers:
    def next_number(self, kind, year): return f"{kind.upper()}-{year}-00001"

class Uow:
    def __init__(self, e):
        self.customers, self.orders, self.quotes = e.customers, e.orders, e.quotes
        self.catalog, self.numbers = e.catalog, e.numbers
    def __enter__(self): return self
    def __exit__(self, *args): return None

class Pricing:
    def get_unit_price(self, code, currency, on_date):
        return SimpleNamespace(unit_price=Money(Decimal("100"), currency))

def make_env(perms=frozenset({"ventes.create","ventes.confirm","ventes.view"})):
    e = SimpleNamespace(customers=Repo(), orders=Repo(), quotes=Repo(),
                        catalog=Catalog(), numbers=Numbers())
    audit = AuditLog()
    counter = iter(range(1, 1000))
    settings = SimpleNamespace(
        rounding=RoundingSettings(USD=2, CDF=0),
        get_tax_group=lambda code: SimpleNamespace(code=code, vat_rate=Decimal("0.16"), is_out_of_scope=False)
            if code == "VAT16" else None)
    deps = SimpleNamespace(uow_factory=lambda: Uow(e),
        publisher=SimpleNamespace(invoice_ready_for_certification=lambda x: None, payment_received=lambda x: None),
        audit=audit, authz=AuthorizationService(),
        clock=lambda: datetime(2026,11,20,tzinfo=UTC), logger=logging.getLogger("test_orders"),
        settings=settings, id_factory=lambda: f"ID-{next(counter)}")
    actor = User(id="U1", username="tester", direct_permissions=perms)
    service = OrderService(deps, Pricing())
    e.audit, e.actor, e.service, e.deps = audit, actor, service, deps
    return e

def add_customer(e, active=True):
    c = Customer(Partner(id="C1", name="Client", partner_type="client"), is_active=active)
    e.customers.save(c)
    return c

def manual_line(price="100", **kw):
    return LineInput(designation="Prestation", unit_price_ht=Money(Decimal(price), Currency.USD),
                     tax_group="VAT16", **kw)

def make_order(e, status=OrderStatus.DRAFT):
    return SalesOrder(id="O1", number="CMD-1", customer_id="C1", order_date=date(2026,11,20),
        currency=Currency.USD, lines=(__import__("vorpal.modules.ventes.domain.orders", fromlist=["OrderLine"]).OrderLine(
        line_id="L1", item_code=None, designation="Service", quantity=Decimal("2"),
        unit_price_ht=Money(Decimal("100"),Currency.USD), tax_group="VAT16"),), status=status)

def test_create_order_builds_draft_and_audits():
    e = make_env()
    add_customer(e)
    order = e.service.create_order(e.actor, customer_id="C1", lines=[manual_line()], order_id="O1")
    assert order.status == OrderStatus.DRAFT
    assert order.number == "ORDER-2026-00001"
    assert order.lines[0].designation == "Prestation"
    assert e.orders.get("O1") == order
    assert e.audit.entries[-1].action == "CREATE_ORDER"

def test_create_order_rejects_missing_or_inactive_customer():
    with pytest.raises(SalesNotFoundError):
        make_env().service.create_order(make_env().actor, customer_id="X", lines=[manual_line()])
    e = make_env()
    add_customer(e, False)
    with pytest.raises(SalesConflictError):
        e.service.create_order(e.actor, customer_id="C1", lines=[manual_line()])

def test_create_order_rejects_empty_lines():
    e = make_env()
    add_customer(e)
    with pytest.raises(SalesValidationError):
        e.service.create_order(e.actor, customer_id="C1", lines=[])

def test_create_order_rejects_invalid_manual_line_inputs():
    e = make_env()
    add_customer(e)

    invalid_cases = [
        LineInput(unit_price_ht=Money(Decimal("10"), Currency.USD), tax_group="VAT16"),
        LineInput(designation="X", tax_group="VAT16"),
        LineInput(designation="X", unit_price_ht=Money(Decimal("10"), Currency.USD)),
        LineInput(
            designation="X",
            unit_price_ht=Money(Decimal("10"), Currency.USD),
            tax_group="UNKNOWN",
        ),
    ]

    for line in invalid_cases:
        with pytest.raises(SalesValidationError):
            e.service.create_order(e.actor, customer_id="C1", lines=[line])


def test_create_order_accepts_valid_manual_line():
    e = make_env()
    add_customer(e)

    line = LineInput(
        designation="X",
        unit_price_ht=Money(Decimal("10"), Currency.USD),
        tax_group="VAT16",
    )

    order = e.service.create_order(e.actor, customer_id="C1", lines=[line])

    assert order is not None
    assert len(order.lines) == 1

def test_create_order_resolves_catalog_item_and_price():
    e = make_env()
    add_customer(e)
    e.catalog.items["IT1"] = SalesItem(code="IT1", designation="Article")
    order = e.service.create_order(e.actor, customer_id="C1",
        lines=[LineInput(item_code=" IT1 ", quantity=Decimal("2"))])
    assert order.lines[0].item_code == "IT1"
    assert order.lines[0].unit_price_ht.amount == Decimal("100")

def test_create_order_rejects_unknown_or_inactive_item():
    e = make_env()
    add_customer(e)
    with pytest.raises(SalesNotFoundError):
        e.service.create_order(e.actor, customer_id="C1", lines=[LineInput(item_code="X")])
    e.catalog.items["X"] = SalesItem(code="X", designation="X", is_active=False)
    with pytest.raises(SalesConflictError):
        e.service.create_order(e.actor, customer_id="C1", lines=[LineInput(item_code="X")])

def test_update_order_lines_only_while_draft():
    e = make_env()
    e.orders.save(make_order(e))
    updated = e.service.update_order_lines(e.actor, "O1", [manual_line()])
    assert updated.lines[0].designation == "Prestation"
    e.orders.save(make_order(e, OrderStatus.CONFIRMED))
    with pytest.raises(SalesConflictError):
        e.service.update_order_lines(e.actor, "O1", [manual_line()])

def test_update_order_lines_rejects_missing_order():
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.update_order_lines(e.actor, "X", [manual_line()])

def test_set_deposit_by_percentage_and_amount():
    e = make_env()
    e.orders.save(make_order(e))
    by_percent = e.service.set_deposit(e.actor, "O1", percent=Decimal("50"))
    assert by_percent.deposit_required.amount > 0
    by_amount = e.service.set_deposit(e.actor, "O1", amount=Money(Decimal("25.555"),Currency.USD))
    assert by_amount.deposit_required == Money(Decimal("25.56"),Currency.USD)

@pytest.mark.parametrize("kwargs", [{}, {"percent":Decimal("10"),"amount":Money(Decimal("1"),Currency.USD)},
                                     {"percent":Decimal("-1")}, {"percent":Decimal("101")},
                                     {"amount":Money(Decimal("-1"),Currency.USD)},
                                     {"amount":Money(Decimal("1"),Currency.CDF)}])
def test_set_deposit_rejects_invalid_parameters(kwargs):
    e = make_env()
    e.orders.save(make_order(e))
    with pytest.raises(SalesValidationError):
        e.service.set_deposit(e.actor, "O1", **kwargs)

def test_set_deposit_rejects_unknown_order():
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.set_deposit(e.actor, "X", percent=Decimal("10"))

def test_confirm_and_cancel_order():
    e = make_env()
    e.orders.save(make_order(e))
    confirmed = e.service.confirm_order(e.actor, "O1")
    assert confirmed.status == OrderStatus.CONFIRMED
    cancelled = e.service.cancel_order(e.actor, "O1")
    assert cancelled.status == OrderStatus.CANCELLED

def test_confirm_cancel_and_get_handle_missing_order():
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.confirm_order(e.actor, "X")
    with pytest.raises(SalesNotFoundError):
        e.service.cancel_order(e.actor, "X")
    with pytest.raises(SalesNotFoundError):
        e.service.get_order(e.actor, "X")

def test_get_and_list_orders():
    e = make_env()
    e.orders.save(make_order(e))
    assert e.service.get_order(e.actor, "O1").id == "O1"
    assert [x.id for x in e.service.list_orders(e.actor)] == ["O1"]

def test_convert_accepted_quote_to_order():
    e = make_env()
    q = Quote(id="Q1", number="DEV-1", customer_id="C1", quote_date=date(2026,11,1),
        valid_until=date(2026,12,1), currency=Currency.USD,
        lines=(QuoteLine(item_code=None, designation="Service", quantity=Decimal("1"),
            unit_price_ht=Money(Decimal("100"),Currency.USD)),), status=QuoteStatus.ACCEPTED)
    e.quotes.save(q)
    order = e.service.create_from_quote(e.actor, "Q1", order_id="O2")
    assert order.quote_id == "Q1"
    assert e.quotes.get("Q1").status == QuoteStatus.CONVERTED
    assert e.quotes.get("Q1").converted_order_id == "O2"

def test_convert_quote_rejects_missing_or_unaccepted_quote():
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.create_from_quote(e.actor, "X")
    q = Quote(id="Q1", number="DEV-1", customer_id="C1", quote_date=date(2026,11,1),
        valid_until=date(2026,12,1), currency=Currency.USD,
        lines=(QuoteLine(item_code=None, designation="Service", quantity=Decimal("1"),
            unit_price_ht=Money(Decimal("100"),Currency.USD)),))
    e.quotes.save(q)
    with pytest.raises(SalesConflictError):
        e.service.create_from_quote(e.actor, "Q1")

def test_create_order_requires_permission():
    e = make_env(frozenset({"ventes.view"}))
    add_customer(e)
    with pytest.raises(SalesComplianceError):
        e.service.create_order(e.actor, customer_id="C1", lines=[manual_line()])


def test_create_order_rejects_catalog_price_currency_mismatch():
    e = make_env()
    add_customer(e)
    e.catalog.items["IT1"] = SalesItem(code="IT1", designation="Article")
    e.service._pricing.get_unit_price = lambda code, currency, on_date: SimpleNamespace(
        unit_price=Money(Decimal("100"), Currency.CDF)
    )

    with pytest.raises(SalesValidationError):
        e.service.create_order(
            e.actor,
            customer_id="C1",
            lines=[LineInput(item_code="IT1", quantity=Decimal("1"))],
        )

def test_create_order_rejects_manual_price_currency_mismatch():
    e = make_env()
    add_customer(e)
    invalid_line = LineInput(
        designation="Prestation",
        unit_price_ht=Money(Decimal("100"), Currency.CDF),
        tax_group="VAT16",
    )

    with pytest.raises(SalesValidationError):
        e.service.create_order(e.actor, customer_id="C1", lines=[invalid_line])

def test_create_order_rejects_manual_out_of_scope_tax_group():
    e = make_env()
    add_customer(e)
    e.deps.settings.get_tax_group = lambda code: SimpleNamespace(
        code=code,
        vat_rate=Decimal("0"),
        is_out_of_scope=True,
    )

    with pytest.raises(SalesValidationError):
        e.service.create_order(
            e.actor, customer_id="C1", lines=[manual_line()]
        )

def test_create_from_quote_rejects_already_converted_quote():
    e = make_env()
    quote = Quote(
        id="Q1",
        number="DEV-1",
        customer_id="C1",
        quote_date=date(2026, 11, 1),
        valid_until=date(2026, 12, 1),
        currency=Currency.USD,
        lines=(
            QuoteLine(
                item_code=None,
                designation="Service",
                quantity=Decimal("1"),
                unit_price_ht=Money(Decimal("100"), Currency.USD),
            ),
        ),
        status=QuoteStatus.ACCEPTED,
    )
    e.quotes.save(quote)

    e.service.create_from_quote(e.actor, "Q1", order_id="O2")

    with pytest.raises(SalesConflictError):
        e.service.create_from_quote(e.actor, "Q1", order_id="O3")

def test_set_deposit_rejects_cancelled_order():
    e = make_env()
    e.orders.save(make_order(e, OrderStatus.CANCELLED))

    with pytest.raises(SalesConflictError):
        e.service.set_deposit(e.actor, "O1", percent=Decimal("10"))
