from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vorpal.core import Currency, Money, Partner
from vorpal.modules.ventes.adapters.persistence.models import Base
from vorpal.modules.ventes.adapters.persistence.sql_repositories import (
    SqlCatalogRepository,
    SqlCustomerRepository,
    SqlInvoiceRepository,
    SqlOrderRepository,
    SqlPaymentRepository,
    SqlQuoteRepository,
    SqlReminderRepository,
)
from vorpal.modules.ventes.domain.catalog import PriceList, SalesItem
from vorpal.modules.ventes.domain.customers import (
    ContactPerson,
    Customer,
    PaymentTerms,
)
from vorpal.modules.ventes.domain.invoices import (
    InvoiceLine,
    InvoiceStatus,
    SalesInvoice,
)
from vorpal.modules.ventes.domain.orders import (
    OrderLine,
    OrderStatus,
    SalesOrder,
)
from vorpal.modules.ventes.domain.payments import (
    Allocation,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from vorpal.modules.ventes.domain.quotes import (
    Quote,
    QuoteLine,
    QuoteStatus,
)
from vorpal.modules.ventes.domain.reminders import Reminder


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()

    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def make_customer(
    customer_id: str = "C-001",
    active: bool = True,
) -> Customer:
    partner = Partner(
        id=customer_id,
        name=f"Client {customer_id}",
        partner_type="client",
        nif="NIF-001",
        rccm="RCCM-001",
        address="Lubumbashi",
    )
    return Customer(
        partner=partner,
        contacts=(
            ContactPerson(
                name="Contact principal",
                email="contact@example.com",
                phone="+243000000000",
                role="Responsable",
            ),
        ),
        payment_terms=PaymentTerms(days=30, label="Net 30"),
        preferred_currency=Currency.USD,
        is_active=active,
    )


def make_item(
    code: str = "ART-001",
    active: bool = True,
) -> SalesItem:
    return SalesItem(
        code=code,
        designation=f"Article {code}",
        tax_group="A",
        vat_rate=Decimal("0.16"),
        is_out_of_scope=False,
        is_active=active,
    )


def make_quote(quote_id: str = "DEV-001") -> Quote:
    return Quote(
        id=quote_id,
        number=f"Q-{quote_id}",
        customer_id="C-001",
        quote_date=date(2026, 1, 10),
        valid_until=date(2026, 2, 10),
        currency=Currency.USD,
        lines=(
            QuoteLine(
                item_code="ART-001",
                designation="Article principal",
                quantity=Decimal("2"),
                unit_price_ht=Money(Decimal("100"), Currency.USD),
            ),
            QuoteLine(
                item_code="ART-002",
                designation="Article secondaire",
                quantity=Decimal("3"),
                unit_price_ht=Money(Decimal("50"), Currency.USD),
            ),
        ),
        status=QuoteStatus.SENT,
    )


def make_order(order_id: str = "CMD-001") -> SalesOrder:
    return SalesOrder(
        id=order_id,
        number=f"O-{order_id}",
        customer_id="C-001",
        order_date=date(2026, 1, 11),
        currency=Currency.USD,
        lines=(
            OrderLine(
                line_id="L-001",
                item_code="ART-001",
                designation="Article principal",
                quantity=Decimal("2"),
                unit_price_ht=Money(Decimal("100"), Currency.USD),
            ),
            OrderLine(
                line_id="L-002",
                item_code="ART-002",
                designation="Article secondaire",
                quantity=Decimal("1"),
                unit_price_ht=Money(Decimal("50"), Currency.USD),
                invoiced_quantity=Decimal("0"),
            ),
        ),
        status=OrderStatus.CONFIRMED,
    )


def make_invoice(
    invoice_id: str = "FAC-001",
    number: str | None = "INV-001",
    invoice_type: str = "vente",
    original_invoice_id: str | None = None,
) -> SalesInvoice:
    return SalesInvoice(
        id=invoice_id,
        customer_id="C-001",
        invoice_date=date(2026, 1, 12),
        due_date=date(2026, 2, 12),
        currency=Currency.USD,
        lines=(
            InvoiceLine(
                item_code="ART-001",
                designation="Article facturé",
                quantity=Decimal("2"),
                unit_price_ht=Money(Decimal("100"), Currency.USD),
            ),
        ),
        invoice_type=invoice_type,
        number=number,
        status=InvoiceStatus.VALIDATED,
        original_invoice_id=original_invoice_id,
        total_ht=Money(Decimal("200"), Currency.USD),
        total_vat=Money(Decimal("32"), Currency.USD),
        total_ttc=Money(Decimal("232"), Currency.USD),
        paid_amount=Money(Decimal("0"), Currency.USD),
    )


def make_payment(
    payment_id: str = "PAY-001",
    allocations: tuple[Allocation, ...] = (),
) -> Payment:
    return Payment(
        id=payment_id,
        customer_id="C-001",
        amount=Money(Decimal("100"), Currency.USD),
        payment_date=date(2026, 1, 15),
        method=PaymentMethod.BANK_TRANSFER,
        reference=f"REF-{payment_id}",
        status=PaymentStatus.RECORDED,
        allocations=allocations,
    )


def test_customer_repository_save_get_and_list(session: Session) -> None:
    repository = SqlCustomerRepository(session)
    customer = make_customer()

    repository.save(customer)
    session.commit()

    loaded = repository.get("C-001")

    assert loaded is not None
    assert loaded.partner.id == "C-001"
    assert loaded.partner.name == "Client C-001"
    assert loaded.is_active is True
    assert len(loaded.contacts) == 1
    assert loaded.contacts[0].email == "contact@example.com"
    assert loaded.payment_terms.days == 30
    assert repository.list_all() == [loaded]
    assert repository.list_all(active_only=True) == [loaded]


def test_customer_repository_filters_inactive_customers(
    session: Session,
) -> None:
    repository = SqlCustomerRepository(session)
    repository.save(make_customer("C-ACTIVE", active=True))
    repository.save(make_customer("C-INACTIVE", active=False))
    session.commit()

    all_customers = repository.list_all()
    active_customers = repository.list_all(active_only=True)

    assert len(all_customers) == 2
    assert len(active_customers) == 1
    assert active_customers[0].partner.id == "C-ACTIVE"
    assert repository.get("UNKNOWN") is None


def test_catalog_repository_item_save_get_and_filter(
    session: Session,
) -> None:
    repository = SqlCatalogRepository(session)
    repository.save_item(make_item("A-ACTIVE", active=True))
    repository.save_item(make_item("B-INACTIVE", active=False))
    session.commit()

    loaded = repository.get_item("A-ACTIVE")

    assert loaded is not None
    assert loaded.code == "A-ACTIVE"
    assert loaded.vat_rate == Decimal("0.16")
    assert len(repository.list_items()) == 2
    assert len(repository.list_items(active_only=True)) == 1
    assert repository.get_item("UNKNOWN") is None


def test_catalog_repository_price_list_round_trip(
    session: Session,
) -> None:
    repository = SqlCatalogRepository(session)
    repository.save_item(make_item())
    repository.save_price_list(
        PriceList(
            id="PL-001",
            name="Tarif standard",
            currency=Currency.USD,
            prices={
                "ART-001": Money(Decimal("125.50"), Currency.USD),
            },
        )
    )
    session.commit()

    loaded = repository.get_price_list("PL-001")

    assert loaded is not None
    assert loaded.id == "PL-001"
    assert loaded.name == "Tarif standard"
    assert loaded.prices["ART-001"].amount == Decimal("125.50")
    assert repository.list_price_lists() == [loaded]
    assert repository.list_price_lists(currency=Currency.USD) == [loaded]
    assert repository.get_price_list("UNKNOWN") is None


def test_catalog_repository_price_list_filters(
    session: Session,
) -> None:
    repository = SqlCatalogRepository(session)

    repository.save_price_list(
        PriceList(
            id="PL-USD",
            name="Tarif USD",
            currency=Currency.USD,
            prices={},
            is_active=True,
        )
    )
    repository.save_price_list(
        PriceList(
            id="PL-CDF",
            name="Tarif CDF",
            currency=Currency.CDF,
            prices={},
            is_active=True,
        )
    )
    repository.save_price_list(
        PriceList(
            id="PL-OLD",
            name="Ancien tarif",
            currency=Currency.USD,
            prices={},
            is_active=False,
        )
    )
    session.commit()

    assert len(repository.list_price_lists()) == 2
    assert len(repository.list_price_lists(currency=Currency.USD)) == 1
    assert len(repository.list_price_lists(active_only=False)) == 3


def test_quote_repository_save_get_and_list(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlQuoteRepository(session)
    quote = make_quote()

    repository.save(quote)
    session.commit()

    loaded = repository.get("DEV-001")

    assert loaded is not None
    assert loaded.number == "Q-DEV-001"
    assert loaded.status == QuoteStatus.SENT
    assert len(loaded.lines) == 2
    assert loaded.lines[0].designation == "Article principal"
    assert loaded.lines[1].designation == "Article secondaire"
    assert repository.list_all() == [loaded]
    assert repository.get("UNKNOWN") is None


def test_quote_repository_replaces_existing_lines(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlQuoteRepository(session)
    repository.save(make_quote())
    session.commit()

    replacement = Quote(
        id="DEV-001",
        number="Q-DEV-001",
        customer_id="C-001",
        quote_date=date(2026, 1, 10),
        valid_until=date(2026, 2, 10),
        currency=Currency.USD,
        lines=(
            QuoteLine(
                item_code="ART-NEW",
                designation="Ligne remplacée",
                quantity=Decimal("5"),
                unit_price_ht=Money(Decimal("20"), Currency.USD),
            ),
        ),
        status=QuoteStatus.ACCEPTED,
    )

    repository.save(replacement)
    session.commit()

    loaded = repository.get("DEV-001")

    assert loaded is not None
    assert loaded.status == QuoteStatus.ACCEPTED
    assert len(loaded.lines) == 1
    assert loaded.lines[0].designation == "Ligne remplacée"


def test_order_repository_save_get_and_list(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlOrderRepository(session)
    order = make_order()

    repository.save(order)
    session.commit()

    loaded = repository.get("CMD-001")

    assert loaded is not None
    assert loaded.number == "O-CMD-001"
    assert loaded.status == OrderStatus.CONFIRMED
    assert len(loaded.lines) == 2
    assert loaded.lines[0].line_id == "L-001"
    assert loaded.lines[1].line_id == "L-002"
    assert repository.list_all() == [loaded]
    assert repository.get("UNKNOWN") is None


def test_order_repository_replaces_existing_lines(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlOrderRepository(session)
    repository.save(make_order())
    session.commit()

    replacement = SalesOrder(
        id="CMD-001",
        number="O-CMD-001",
        customer_id="C-001",
        order_date=date(2026, 1, 11),
        currency=Currency.USD,
        lines=(
            OrderLine(
                line_id="L-NEW",
                item_code="ART-NEW",
                designation="Nouvelle ligne",
                quantity=Decimal("4"),
                unit_price_ht=Money(Decimal("25"), Currency.USD),
            ),
        ),
        status=OrderStatus.INVOICED,
    )

    repository.save(replacement)
    session.commit()

    loaded = repository.get("CMD-001")

    assert loaded is not None
    assert loaded.status == OrderStatus.INVOICED
    assert len(loaded.lines) == 1
    assert loaded.lines[0].line_id == "L-NEW"


def test_invoice_repository_save_get_number_and_list(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlInvoiceRepository(session)
    invoice = make_invoice()

    repository.save(invoice)
    session.commit()

    loaded = repository.get("FAC-001")

    assert loaded is not None
    assert loaded.number == "INV-001"
    assert loaded.total_ttc is not None
    assert loaded.total_ttc.amount == Decimal("232")
    assert len(loaded.lines) == 1
    assert repository.get_by_number("INV-001") is not None
    assert repository.get_by_number("UNKNOWN") is None
    assert repository.list_all() == [loaded]
    assert repository.get("UNKNOWN") is None


def test_invoice_repository_lists_credit_notes_for_original(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlInvoiceRepository(session)

    repository.save(make_invoice("FAC-001", "INV-001"))
    repository.save(
        make_invoice(
            invoice_id="AVOIR-001",
            number="CR-001",
            invoice_type="avoir",
            original_invoice_id="FAC-001",
        )
    )
    repository.save(
        make_invoice(
            invoice_id="AVOIR-002",
            number="CR-002",
            invoice_type="avoir",
            original_invoice_id="OTHER-INVOICE",
        )
    )
    session.commit()

    credit_notes = repository.list_credit_notes_for_invoice("FAC-001")

    assert len(credit_notes) == 1
    assert credit_notes[0].id == "AVOIR-001"
    assert credit_notes[0].invoice_type == "avoir"


def test_invoice_repository_replaces_existing_lines(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlInvoiceRepository(session)
    repository.save(make_invoice())
    session.commit()

    replacement = SalesInvoice(
        id="FAC-001",
        customer_id="C-001",
        invoice_date=date(2026, 1, 12),
        due_date=date(2026, 2, 12),
        currency=Currency.USD,
        lines=(
            InvoiceLine(
                item_code="ART-NEW",
                designation="Nouvelle ligne facture",
                quantity=Decimal("3"),
                unit_price_ht=Money(Decimal("40"), Currency.USD),
            ),
        ),
        number="INV-001",
        status=InvoiceStatus.VALIDATED,
    )

    repository.save(replacement)
    session.commit()

    loaded = repository.get("FAC-001")

    assert loaded is not None
    assert len(loaded.lines) == 1
    assert loaded.lines[0].designation == "Nouvelle ligne facture"


def test_payment_repository_save_get_and_search(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    repository = SqlPaymentRepository(session)
    payment = make_payment()

    repository.save(payment)
    session.commit()

    loaded = repository.get("PAY-001")

    assert loaded is not None
    assert loaded.amount.amount == Decimal("100")
    assert loaded.reference == "REF-PAY-001"
    assert repository.get_by_method_and_reference(
        PaymentMethod.BANK_TRANSFER,
        "REF-PAY-001",
    ) is not None
    assert repository.get_by_method_and_reference(
        PaymentMethod.CASH,
        "REF-PAY-001",
    ) is None
    assert repository.get("UNKNOWN") is None
    assert len(repository.list_by_customer("C-001")) == 1
    assert repository.list_by_customer("UNKNOWN") == []


def test_payment_repository_allocations_and_invoice_filter(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    SqlInvoiceRepository(session).save(make_invoice())
    repository = SqlPaymentRepository(session)

    allocation = Allocation(
        id="ALLOC-001",
        payment_id="PAY-001",
        invoice_id="FAC-001",
        amount=Money(Decimal("75"), Currency.USD),
        allocated_at=date(2026, 1, 16),
    )
    repository.save(make_payment(allocations=(allocation,)))
    session.commit()

    loaded = repository.get("PAY-001")

    assert loaded is not None
    assert len(loaded.allocations) == 1
    assert loaded.allocations[0].invoice_id == "FAC-001"
    assert loaded.allocations[0].amount.amount == Decimal("75")

    by_invoice = repository.list_by_invoice("FAC-001")

    assert len(by_invoice) == 1
    assert by_invoice[0].id == "PAY-001"
    assert repository.list_by_invoice("UNKNOWN") == []


def test_payment_repository_replaces_allocations(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    SqlInvoiceRepository(session).save(make_invoice())
    repository = SqlPaymentRepository(session)

    first_allocation = Allocation(
        id="ALLOC-001",
        payment_id="PAY-001",
        invoice_id="FAC-001",
        amount=Money(Decimal("50"), Currency.USD),
        allocated_at=date(2026, 1, 16),
    )
    repository.save(make_payment(allocations=(first_allocation,)))
    session.commit()

    second_allocation = Allocation(
        id="ALLOC-002",
        payment_id="PAY-001",
        invoice_id="FAC-001",
        amount=Money(Decimal("100"), Currency.USD),
        allocated_at=date(2026, 1, 17),
    )
    repository.save(make_payment(allocations=(second_allocation,)))
    session.commit()

    loaded = repository.get("PAY-001")

    assert loaded is not None
    assert len(loaded.allocations) == 1
    assert loaded.allocations[0].id == "ALLOC-002"
    assert loaded.allocations[0].amount.amount == Decimal("100")


def test_reminder_repository_save_get_and_list(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    SqlInvoiceRepository(session).save(make_invoice())
    repository = SqlReminderRepository(session)

    reminder = Reminder(
        id="REL-001",
        invoice_id="FAC-001",
        invoice_number="INV-001",
        customer_id="C-001",
        level_code="LEVEL-1",
        channel="email",
        days_overdue=10,
        balance_due=Money(Decimal("232"), Currency.USD),
        message="Rappel de paiement",
        generated_at=date(2026, 2, 22),
        sent=False,
    )

    repository.save(reminder)
    session.commit()

    loaded = repository.get_by_invoice_and_level("FAC-001", "LEVEL-1")

    assert loaded is not None
    assert loaded.id == "REL-001"
    assert loaded.balance_due.amount == Decimal("232")
    assert loaded.sent is False
    assert len(repository.list_by_invoice("FAC-001")) == 1
    assert repository.get_by_invoice_and_level(
        "UNKNOWN",
        "LEVEL-1",
    ) is None
    assert repository.list_by_invoice("UNKNOWN") == []


def test_reminder_repository_updates_existing_reminder(
    session: Session,
) -> None:
    SqlCustomerRepository(session).save(make_customer())
    SqlInvoiceRepository(session).save(make_invoice())
    repository = SqlReminderRepository(session)

    reminder = Reminder(
        id="REL-001",
        invoice_id="FAC-001",
        invoice_number="INV-001",
        customer_id="C-001",
        level_code="LEVEL-1",
        channel="email",
        days_overdue=10,
        balance_due=Money(Decimal("232"), Currency.USD),
        message="Premier rappel",
        generated_at=date(2026, 2, 22),
        sent=False,
    )
    repository.save(reminder)
    session.commit()

    updated = Reminder(
        id="REL-001",
        invoice_id="FAC-001",
        invoice_number="INV-001",
        customer_id="C-001",
        level_code="LEVEL-1",
        channel="email",
        days_overdue=15,
        balance_due=Money(Decimal("200"), Currency.USD),
        message="Rappel mis à jour",
        generated_at=date(2026, 2, 27),
        sent=True,
    )
    repository.save(updated)
    session.commit()

    loaded = repository.get_by_invoice_and_level("FAC-001", "LEVEL-1")

    assert loaded is not None
    assert loaded.days_overdue == 15
    assert loaded.message == "Rappel mis à jour"
    assert loaded.sent is True
