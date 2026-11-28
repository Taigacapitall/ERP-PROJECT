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
from vorpal.modules.ventes.domain.quotes import Quote, QuoteLine, QuoteStatus
from vorpal.modules.ventes.services.common import LineInput
from vorpal.modules.ventes.services.quotes import QuoteService
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
        self.customers, self.quotes = e.customers, e.quotes
        self.catalog, self.numbers = e.catalog, e.numbers
    def __enter__(self): return self
    def __exit__(self, *args): return None

class Pricing:
    def get_unit_price(self, code, currency, on_date):
        return SimpleNamespace(unit_price=Money(Decimal("100"), currency))

def make_env(perms=frozenset({"ventes.create","ventes.view"})):
    e = SimpleNamespace(customers=Repo(), quotes=Repo(), catalog=Catalog(), numbers=Numbers())
    audit = AuditLog()
    counter = iter(range(1, 1000))
    settings = SimpleNamespace(rounding=RoundingSettings(USD=2,CDF=0),
        get_tax_group=lambda code: SimpleNamespace(code=code,vat_rate=Decimal("0.16"),is_out_of_scope=False)
            if code=="VAT16" else None)
    deps = SimpleNamespace(uow_factory=lambda: Uow(e),
        publisher=SimpleNamespace(invoice_ready_for_certification=lambda x:None,payment_received=lambda x:None),
        audit=audit,authz=AuthorizationService(),
        clock=lambda:datetime(2026,11,20,tzinfo=UTC),logger=logging.getLogger("test_quotes"),
        settings=settings,id_factory=lambda:f"ID-{next(counter)}")
    actor=User(id="U1",username="tester",direct_permissions=perms)
    e.audit,e.actor,e.service,e.deps=audit,actor,QuoteService(deps,Pricing()),deps
    return e

def add_customer(e, active=True):
    c=Customer(Partner(id="C1",name="Client",partner_type="client"),is_active=active)
    e.customers.save(c)
    return c

def line(price="100", **kw):
    return LineInput(designation="Prestation",unit_price_ht=Money(Decimal(price),Currency.USD),
                     tax_group="VAT16",**kw)

def make_quote(status=QuoteStatus.DRAFT, valid_until=date(2026,12,1), qid="Q1"):
    return Quote(id=qid,number="DEV-1",customer_id="C1",quote_date=date(2026,11,1),
        valid_until=valid_until,currency=Currency.USD,
        lines=(QuoteLine(item_code=None,designation="Service",quantity=Decimal("1"),
                         unit_price_ht=Money(Decimal("100"),Currency.USD)),),status=status)

def test_create_quote_builds_document_and_audit():
    e=make_env()
    add_customer(e)
    q=e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),
        lines=[line()],quote_id="Q2")
    assert q.id=="Q2"
    assert q.status==QuoteStatus.DRAFT
    assert q.number=="QUOTE-2026-00001"
    assert e.quotes.get("Q2")==q
    assert e.audit.entries[-1].action=="CREATE_QUOTE"

def test_create_quote_uses_catalog_item():
    e=make_env()
    add_customer(e)
    e.catalog.items["IT1"]=SalesItem(code="IT1",designation="Article")
    q=e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),
        lines=[LineInput(item_code="IT1")])
    assert q.lines[0].designation=="Article"
    assert q.lines[0].unit_price_ht.amount==Decimal("100")

def test_create_quote_rejects_missing_or_inactive_customer():
    e=make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.create_quote(e.actor,customer_id="X",valid_until=date(2026,12,1),lines=[line()])
    e=make_env()
    add_customer(e,False)
    with pytest.raises(SalesConflictError):
        e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),lines=[line()])

def test_create_quote_rejects_empty_lines_and_invalid_manual_line():
    e=make_env()
    add_customer(e)
    with pytest.raises(SalesValidationError):
        e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),lines=[])
    invalid=[
        LineInput(unit_price_ht=Money(Decimal("10"),Currency.USD),tax_group="VAT16"),
        LineInput(designation="X",tax_group="VAT16"),
        LineInput(designation="X",unit_price_ht=Money(Decimal("10"),Currency.USD)),
        LineInput(designation="X",unit_price_ht=Money(Decimal("10"),Currency.USD),tax_group="BAD"),
    ]
    for item in invalid:
        with pytest.raises(SalesValidationError):
            e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),lines=[item])

def test_create_quote_rejects_unknown_and_inactive_catalog_item():
    e=make_env()
    add_customer(e)
    with pytest.raises(SalesNotFoundError):
        e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),lines=[LineInput(item_code="X")])
    e.catalog.items["X"]=SalesItem(code="X",designation="X",is_active=False)
    with pytest.raises(SalesConflictError):
        e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),lines=[LineInput(item_code="X")])

def test_send_accept_and_reject_quote():
    e=make_env()
    e.quotes.save(make_quote())
    sent=e.service.send_quote(e.actor,"Q1")
    assert sent.status==QuoteStatus.SENT
    accepted=e.service.accept_quote(e.actor,"Q1")
    assert accepted.status==QuoteStatus.ACCEPTED
    e.quotes.save(make_quote(QuoteStatus.SENT,qid="Q2"))
    rejected=e.service.reject_quote(e.actor,"Q2")
    assert rejected.status==QuoteStatus.REJECTED

def test_send_accept_reject_raise_for_missing_quote():
    e=make_env()
    for method in (e.service.send_quote,e.service.accept_quote,e.service.reject_quote):
        with pytest.raises(SalesNotFoundError):
            method(e.actor,"X")

def test_send_rejects_expired_quote():
    e=make_env()
    e.quotes.save(make_quote(valid_until=date(2026,11,1)))
    with pytest.raises(SalesConflictError):
        e.service.send_quote(e.actor,"Q1")

def test_expire_due_quotes_only_expired_draft_and_sent():
    e=make_env()
    e.quotes.save(make_quote(QuoteStatus.DRAFT,date(2026,11,1),"A"))
    e.quotes.save(make_quote(QuoteStatus.SENT,date(2026,11,1),"B"))
    e.quotes.save(make_quote(QuoteStatus.SENT,date(2026,12,1),"C"))
    e.quotes.save(make_quote(QuoteStatus.ACCEPTED,date(2026,11,1),"D"))
    expired=e.service.expire_due_quotes(e.actor)
    assert [q.id for q in expired]==["A","B"]
    assert e.quotes.get("A").status==QuoteStatus.EXPIRED
    assert e.quotes.get("C").status==QuoteStatus.SENT
    assert e.quotes.get("D").status==QuoteStatus.ACCEPTED
    assert len([x for x in e.audit.entries if x.action=="EXPIRE_QUOTE"])==2

def test_expire_quotes_respects_explicit_reference_date():
    e=make_env()
    e.quotes.save(make_quote(valid_until=date(2026,11,15)))
    assert len(e.service.expire_due_quotes(e.actor,as_of=date(2026,11,15)))==0
    assert len(e.service.expire_due_quotes(e.actor,as_of=date(2026,11,16)))==1

def test_get_and_list_quotes_are_sorted():
    e=make_env()
    e.quotes.save(make_quote(qid="Z"))
    e.quotes.save(make_quote(qid="A"))
    assert e.service.get_quote(e.actor,"A").id=="A"
    assert [q.id for q in e.service.list_quotes(e.actor)]==["A","Z"]

def test_get_quote_rejects_unknown_id():
    e=make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.get_quote(e.actor,"X")

def test_quote_creation_requires_permission():
    e=make_env(frozenset({"ventes.view"}))
    add_customer(e)
    with pytest.raises(SalesComplianceError):
        e.service.create_quote(e.actor,customer_id="C1",valid_until=date(2026,12,1),lines=[line()])


def test_create_quote_rejects_catalog_price_currency_mismatch():
    e = make_env()
    add_customer(e)
    e.catalog.items["IT1"] = SalesItem(code="IT1", designation="Article")
    e.service._pricing.get_unit_price = lambda code, currency, on_date: SimpleNamespace(
        unit_price=Money(Decimal("100"), Currency.CDF)
    )

    with pytest.raises(SalesValidationError):
        e.service.create_quote(
            e.actor,
            customer_id="C1",
            valid_until=date(2026, 12, 1),
            lines=[LineInput(item_code="IT1")],
        )

def test_create_quote_rejects_manual_price_currency_mismatch():
    e = make_env()
    add_customer(e)
    invalid_line = LineInput(
        designation="Prestation",
        unit_price_ht=Money(Decimal("100"), Currency.CDF),
        tax_group="VAT16",
    )

    with pytest.raises(SalesValidationError):
        e.service.create_quote(
            e.actor,
            customer_id="C1",
            valid_until=date(2026, 12, 1),
            lines=[invalid_line],
        )

def test_create_quote_rejects_manual_out_of_scope_tax_group():
    e = make_env()
    add_customer(e)
    e.deps.settings.get_tax_group = lambda code: SimpleNamespace(
        code=code,
        vat_rate=Decimal("0"),
        is_out_of_scope=True,
    )

    with pytest.raises(SalesValidationError):
        e.service.create_quote(
            e.actor,
            customer_id="C1",
            valid_until=date(2026, 12, 1),
            lines=[line()],
        )
