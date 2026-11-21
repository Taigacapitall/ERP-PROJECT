"""Tests des services Achats, evenements, permissions, audit, persistance SQL et ModuleRegistry."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from vorpal.core.errors import ComplianceError
from vorpal.core.events import Event
from vorpal.core.money import Currency, Money
from vorpal.core.registry import ModuleRegistry, ModuleState
from vorpal.core.security import User
from vorpal.modules.achats.domain.errors import (
    PurchaseConflictError,
    PurchaseNotFoundError,
    PurchaseValidationError,
)
from vorpal.modules.achats.domain.landed_costs import LandedCostComponent
from vorpal.modules.achats.manifest import ACHATS_EVENTS_EMITTED
from vorpal.modules.achats.module import AchatsModule
from vorpal.modules.achats.ports.extensions import (
    ImportedSupplierInvoiceTemplate,
    StockReceiptNotifier,
    SupplierInvoiceTemplateImporter,
)
from vorpal.modules.achats.services.landed_costs import LandedCostService
from vorpal.modules.achats.services.purchase_orders import PurchaseOrderService
from vorpal.modules.achats.services.receipts import GoodsReceiptService
from vorpal.modules.achats.services.supplier_invoices import SupplierInvoiceImportService
from vorpal.modules.achats.services.suppliers import SupplierService


class _CsvTemplateImporter(SupplierInvoiceTemplateImporter):
    def import_template(
        self,
        raw_content: bytes,
        filename: str,
        linked_purchase_order_number: str | None = None,
    ) -> ImportedSupplierInvoiceTemplate:
        lines = raw_content.decode("utf-8").strip().splitlines()
        headers = lines[0].split(";") if lines else []
        rows = [dict(zip(headers, row.split(";"), strict=True)) for row in lines[1:]]
        return ImportedSupplierInvoiceTemplate(
            source_filename=filename,
            file_format="csv",
            headers=headers,
            rows=rows,
            linked_purchase_order_number=linked_purchase_order_number,
        )


class _FakeStockNotifier(StockReceiptNotifier):
    def __init__(self) -> None:
        self.receipts: list[dict[str, Any]] = []
        self.landed_costs: list[dict[str, Any]] = []

    def notify_receipt_validated(self, payload: dict[str, Any]) -> None:
        self.receipts.append(payload)

    def notify_landed_cost_allocated(self, payload: dict[str, Any]) -> None:
        self.landed_costs.append(payload)


def test_module_registry_lifecycle_and_end_to_end_sql_workflow(
    app_context,
    buyer_user: User,
    readonly_user: User,
) -> None:
    published_events: list[Event] = []
    app_context.event_bus.subscribe("*", lambda ev: published_events.append(ev))

    stock_notifier = _FakeStockNotifier()
    app_context.services.register(StockReceiptNotifier, stock_notifier)
    app_context.services.register(SupplierInvoiceTemplateImporter, _CsvTemplateImporter())

    module = AchatsModule()
    registry = ModuleRegistry()
    registry.register(module)
    registry.start_all(app_context)
    assert registry.get_state("achats") == ModuleState.RUNNING
    assert module.healthcheck() is True

    assert module.supplier_service is not None
    assert module.purchase_order_service is not None
    assert module.goods_receipt_service is not None
    assert module.landed_cost_service is not None
    assert module.supplier_invoice_import_service is not None

    with pytest.raises(ComplianceError):
        module.supplier_service.create_supplier(
            actor=readonly_user,
            supplier_id="SUP-FORBIDDEN",
            name="Interdit",
            nif="NIF-000",
        )

    supplier = module.supplier_service.create_supplier(
        actor=buyer_user,
        supplier_id="SUP-10",
        name="Fournisseur RDC",
        nif="NIF-998877",
        rccm="CD/LSH/RCCM/25-A-010",
        address="Lubumbashi",
    )
    assert supplier.id == "SUP-10"

    po = module.purchase_order_service.create_order(
        actor=buyer_user,
        order_id="PO-10",
        supplier_id=supplier.id,
        order_date=date(2026, 11, 20),
    )
    module.purchase_order_service.add_line(
        actor=buyer_user,
        order_id=po.id,
        line_id="POL-1",
        item_code="CABLE-CU",
        designation="Cable cuivre 16mm",
        quantity_ordered=Decimal("10"),
        unit_price_ht=Money(Decimal("25.00"), Currency.USD),
    )
    confirmed_po = module.purchase_order_service.confirm_order(actor=buyer_user, order_id=po.id)
    assert confirmed_po.number == "BC-2026-00001"

    receipt = module.goods_receipt_service.create_receipt(
        actor=buyer_user,
        receipt_id="REC-10",
        purchase_order_id=confirmed_po.id,
        receipt_date=date(2026, 11, 20),
        warehouse_code="DEPOT-LSH",
    )
    module.goods_receipt_service.add_line(
        actor=buyer_user,
        receipt_id=receipt.id,
        line_id="GRL-1",
        order_line_id="POL-1",
        item_code="CABLE-CU",
        designation="Cable cuivre 16mm",
        quantity_received=Decimal("10"),
        unit_cost_ht=Money(Decimal("25.00"), Currency.USD),
    )
    validated_receipt = module.goods_receipt_service.validate_receipt(
        actor=buyer_user, receipt_id=receipt.id
    )
    assert validated_receipt.number == "REC-2026-00001"
    assert len(stock_notifier.receipts) == 1

    lc = module.landed_cost_service.allocate_for_receipt(
        actor=buyer_user,
        landed_cost_id="LC-10",
        goods_receipt_id=validated_receipt.id,
        allocation_date=date(2026, 11, 20),
        components=[
            LandedCostComponent(
                category="FREIGHT",
                label="Fret routier",
                amount=Money(Decimal("50.00"), Currency.USD),
            ),
        ],
    )
    assert lc.total_cost() == Money(Decimal("50.00"), Currency.USD)
    assert len(stock_notifier.landed_costs) == 1

    imported = module.supplier_invoice_import_service.import_template(
        actor=buyer_user,
        raw_content=b"facture_num;montant_ht;devise\nFAC-F-001;250.00;USD\n",
        filename="modele_fournisseur.csv",
        linked_purchase_order_number=confirmed_po.number,
    )
    assert imported.headers == ["facture_num", "montant_ht", "devise"]
    assert len(imported.rows) == 1

    event_types = [ev.type for ev in published_events]
    assert ACHATS_EVENTS_EMITTED[0] in event_types
    assert ACHATS_EVENTS_EMITTED[1] in event_types
    assert ACHATS_EVENTS_EMITTED[2] in event_types
    assert ACHATS_EVENTS_EMITTED[3] in event_types
    assert app_context.audit.verify_chain() is True

    registry.stop_all()
    assert registry.get_state("achats") == ModuleState.STOPPED


def test_memory_uow_rollback_and_sequence_gapless(
    memory_uow_factory,
    settings,
    audit_log,
    authz_service,
    buyer_user: User,
    clock_fn,
) -> None:
    supplier_svc = SupplierService(
        uow_factory=memory_uow_factory,
        audit=audit_log,
        authz=authz_service,
        clock=clock_fn,
    )
    po_svc = PurchaseOrderService(
        uow_factory=memory_uow_factory,
        settings=settings,
        audit=audit_log,
        authz=authz_service,
        clock=clock_fn,
    )
    receipt_svc = GoodsReceiptService(
        uow_factory=memory_uow_factory,
        settings=settings,
        audit=audit_log,
        authz=authz_service,
        clock=clock_fn,
    )
    lc_svc = LandedCostService(
        uow_factory=memory_uow_factory,
        settings=settings,
        audit=audit_log,
        authz=authz_service,
        clock=clock_fn,
    )
    inv_svc = SupplierInvoiceImportService(
        audit=audit_log,
        authz=authz_service,
        clock=clock_fn,
    )

    sup = supplier_svc.create_supplier(buyer_user, "S-M1", "Fournisseur Memoire", nif="NIF-M1")
    po = po_svc.create_order(buyer_user, "PO-M1", sup.id, date(2026, 11, 20))
    po_svc.add_line(
        buyer_user,
        po.id,
        "L1",
        "ART1",
        "Article 1",
        Decimal("4"),
        Money(Decimal("10.00"), Currency.USD),
    )
    confirmed = po_svc.confirm_order(buyer_user, po.id)
    assert confirmed.number == "BC-2026-00001"

    # Rollback si une erreur survient en cours de transaction
    with pytest.raises(RuntimeError):
        with memory_uow_factory() as uow:
            uow.sequences.next_number("purchase_order", 2026)
            raise RuntimeError("erreur transactionnelle")

    with memory_uow_factory() as uow:
        assert uow.sequences.next_number("purchase_order", 2026) == "2"

    rec = receipt_svc.create_receipt(buyer_user, "REC-M1", confirmed.id, date(2026, 11, 20))
    receipt_svc.add_line(
        buyer_user,
        rec.id,
        "RL1",
        "L1",
        "ART1",
        "Article 1",
        Decimal("2"),
        Money(Decimal("10.00"), Currency.USD),
    )
    validated = receipt_svc.validate_receipt(buyer_user, rec.id)
    assert validated.number == "REC-2026-00001"

    lc_doc = lc_svc.allocate_for_receipt(
        buyer_user,
        "LC-M1",
        validated.id,
        date(2026, 11, 20),
        [
            LandedCostComponent(
                category="TAX", label="Taxe locale", amount=Money(Decimal("20.00"), Currency.USD)
            )
        ],
    )
    assert lc_doc.total_cost() == Money(Decimal("20.00"), Currency.USD)

    with pytest.raises(PurchaseValidationError):
        inv_svc.import_template(buyer_user, b"a;b\n1;2\n", "test.csv")


def test_supplier_service_crud_and_status_transitions(
    memory_uow_factory,
    audit_log,
    authz_service,
    buyer_user: User,
    readonly_user: User,
) -> None:
    service = SupplierService(
        uow_factory=memory_uow_factory,
        audit=audit_log,
        authz=authz_service,
    )

    created = service.create_supplier(
        actor=buyer_user,
        supplier_id="SUP-COVERAGE",
        name="Fournisseur initial",
        nif="NIF-001",
        rccm="RCCM-001",
        address="Lubumbashi",
        payment_terms_days=30,
        preferred_currency=Currency.USD,
    )

    assert created.is_active is True
    assert created.name == "Fournisseur initial"

    retrieved = service.get_supplier(readonly_user, "SUP-COVERAGE")
    assert retrieved.id == created.id

    suppliers = service.list_suppliers(readonly_user)
    assert [supplier.id for supplier in suppliers] == ["SUP-COVERAGE"]

    updated = service.update_supplier(
        actor=buyer_user,
        supplier_id="SUP-COVERAGE",
        name="Fournisseur modifié",
        nif="NIF-002",
        rccm="RCCM-002",
        address="Kolwezi",
        payment_terms_days=45,
    )

    assert updated.name == "Fournisseur modifié"
    assert updated.nif == "NIF-002"
    assert updated.partner.rccm == "RCCM-002"
    assert updated.partner.address == "Kolwezi"
    assert updated.payment_terms_days == 45
    assert updated.preferred_currency == Currency.USD
    assert updated.is_active is True

    deactivated = service.set_active_status(
        actor=buyer_user,
        supplier_id="SUP-COVERAGE",
        is_active=False,
    )
    assert deactivated.is_active is False

    reactivated = service.set_active_status(
        actor=buyer_user,
        supplier_id="SUP-COVERAGE",
        is_active=True,
    )
    assert reactivated.is_active is True

    assert audit_log.verify_chain() is True


def test_supplier_service_duplicate_and_missing_suppliers(
    memory_uow_factory,
    audit_log,
    authz_service,
    buyer_user: User,
) -> None:
    service = SupplierService(
        uow_factory=memory_uow_factory,
        audit=audit_log,
        authz=authz_service,
    )

    service.create_supplier(
        actor=buyer_user,
        supplier_id="SUP-ERROR",
        name="Fournisseur existant",
    )

    with pytest.raises(PurchaseConflictError):
        service.create_supplier(
            actor=buyer_user,
            supplier_id="SUP-ERROR",
            name="Doublon",
        )

    with pytest.raises(PurchaseNotFoundError):
        service.get_supplier(buyer_user, "SUP-ABSENT")

    with pytest.raises(PurchaseNotFoundError):
        service.update_supplier(
            actor=buyer_user,
            supplier_id="SUP-ABSENT",
            name="Inexistant",
        )

    with pytest.raises(PurchaseNotFoundError):
        service.set_active_status(
            actor=buyer_user,
            supplier_id="SUP-ABSENT",
            is_active=False,
        )


def test_supplier_service_rejects_readonly_user(
    memory_uow_factory,
    audit_log,
    authz_service,
    readonly_user: User,
) -> None:
    service = SupplierService(
        uow_factory=memory_uow_factory,
        audit=audit_log,
        authz=authz_service,
    )

    with pytest.raises(ComplianceError):
        service.create_supplier(
            actor=readonly_user,
            supplier_id="SUP-FORBIDDEN",
            name="Accès interdit",
        )

    with pytest.raises(ComplianceError):
        service.update_supplier(
            actor=readonly_user,
            supplier_id="SUP-FORBIDDEN",
            name="Modification interdite",
        )

    with pytest.raises(ComplianceError):
        service.set_active_status(
            actor=readonly_user,
            supplier_id="SUP-FORBIDDEN",
            is_active=False,
        )
