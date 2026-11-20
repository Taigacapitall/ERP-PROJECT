"""Tests des composants communs des services Ventes."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from vorpal.core import (
    AuditLog,
    AuthorizationService,
    Currency,
    Money,
    User,
)
from vorpal.core.contracts import InvoiceDTO
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesValidationError,
)
from vorpal.modules.ventes.domain.payments import Payment
from vorpal.modules.ventes.settings import RoundingSettings
from vorpal.modules.ventes.services.common import (
    ApplicationService,
    LineInput,
    PendingAuditRecord,
    PostCommitBatch,
    ServiceDependencies,
)


class FakePublisher:
    """Collecteur simple des événements publiés pendant les tests."""

    def __init__(self) -> None:
        self.invoices: list[InvoiceDTO] = []
        self.payments: list[Payment] = []

    def invoice_ready_for_certification(self, invoice: InvoiceDTO) -> None:
        self.invoices.append(invoice)

    def payment_received(self, payment: Payment) -> None:
        self.payments.append(payment)


class _ProbeApplicationService(ApplicationService):
    """Expose les méthodes protégées pour tester leur comportement."""


def make_dependencies(
    *,
    permissions: frozenset[str] = frozenset(),
    clock: Any = None,
) -> tuple[ServiceDependencies, FakePublisher, AuditLog]:
    publisher = FakePublisher()
    audit = AuditLog()
    authz = AuthorizationService()
    user = User(
        id="USER-01",
        username="test-user",
        direct_permissions=permissions,
    )

    deps = ServiceDependencies(
        uow_factory=cast(Any, lambda: None),
        publisher=publisher,
        audit=audit,
        authz=authz,
        clock=clock or (lambda: datetime(2026, 11, 20, tzinfo=UTC)),
        logger=logging.getLogger("test_ventes"),
        settings=SimpleNamespace(
            rounding=RoundingSettings(USD=2, CDF=0)
        ),
        id_factory=lambda: "TEST-ID",
    )

    # Le test récupère l'utilisateur séparément pour les vérifications d'accès.
    return deps, publisher, audit


def test_line_input_accepts_valid_values() -> None:
    line = LineInput(
        item_code="ARTICLE-01",
        designation="Article de test",
        quantity=Decimal("2.5"),
        unit_price_ht=Money(Decimal("100.00"), Currency.USD),
        discount_percent=Decimal("10"),
        tax_group="VAT16",
    )

    assert line.quantity == Decimal("2.5")
    assert line.discount_percent == Decimal("10")
    assert line.unit_price_ht == Money(Decimal("100.00"), Currency.USD)


@pytest.mark.parametrize("quantity", [Decimal("0"), Decimal("-1")])
def test_line_input_rejects_non_positive_quantity(
    quantity: Decimal,
) -> None:
    with pytest.raises(SalesValidationError):
        LineInput(quantity=quantity)


@pytest.mark.parametrize("discount", [Decimal("-1"), Decimal("100.01")])
def test_line_input_rejects_discount_out_of_range(
    discount: Decimal,
) -> None:
    with pytest.raises(SalesValidationError):
        LineInput(discount_percent=discount)


def test_line_input_rejects_negative_unit_price() -> None:
    with pytest.raises(SalesValidationError):
        LineInput(
            unit_price_ht=Money(Decimal("-0.01"), Currency.USD)
        )


def test_application_service_allows_declared_user_permission() -> None:
    deps, _, _ = make_dependencies(
        permissions=frozenset({"ventes.read"})
    )
    service = _ProbeApplicationService(deps)
    user = User(
        id="USER-01",
        username="test-user",
        direct_permissions=frozenset({"ventes.read"}),
    )

    service._require(user, "ventes.read")


def test_application_service_rejects_missing_permission() -> None:
    deps, _, _ = make_dependencies()
    service = _ProbeApplicationService(deps)
    user = User(id="USER-01", username="test-user")

    with pytest.raises(SalesComplianceError):
        service._require(user, "ventes.admin")


def test_application_service_rejects_inactive_user() -> None:
    deps, _, _ = make_dependencies(
        permissions=frozenset({"ventes.admin"})
    )
    service = _ProbeApplicationService(deps)
    user = User(
        id="USER-01",
        username="test-user",
        direct_permissions=frozenset({"ventes.admin"}),
        is_active=False,
    )

    with pytest.raises(SalesComplianceError):
        service._require(user, "ventes.admin")


def test_application_service_uses_injected_clock() -> None:
    expected = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
    deps, _, _ = make_dependencies(clock=lambda: expected)
    service = _ProbeApplicationService(deps)

    assert service._today() == expected.date()


def test_application_service_uses_currency_rounding_settings() -> None:
    deps, _, _ = make_dependencies()
    service = _ProbeApplicationService(deps)

    assert service._decimals(Currency.USD) == 2
    assert service._decimals(Currency.CDF) == 0


def test_post_commit_batch_records_audit_and_publishes_events() -> None:
    timestamp = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)
    deps, publisher, audit = make_dependencies(
        clock=lambda: timestamp
    )
    batch = PostCommitBatch()

    batch.add_audit(
        user="USER-01",
        action="create",
        entity="invoice",
        entity_id="INV-001",
        before=None,
        after={"status": "draft"},
    )

    invoice = cast(InvoiceDTO, object())
    payment = cast(Payment, object())
    batch.ready_invoices.append(invoice)
    batch.received_payments.append(payment)

    batch.emit(deps)

    assert len(audit.entries) == 1
    assert audit.entries[0].user == "USER-01"
    assert audit.entries[0].action == "create"
    assert audit.entries[0].entity_id == "INV-001"
    assert audit.entries[0].timestamp == timestamp
    assert audit.verify_chain() is True

    assert publisher.invoices == [invoice]
    assert publisher.payments == [payment]
