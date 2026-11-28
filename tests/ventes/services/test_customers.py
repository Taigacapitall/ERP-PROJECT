import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from vorpal.core import AuditLog, AuthorizationService, Currency, Partner, User
from vorpal.modules.ventes.domain.customers import ContactPerson, Customer
from vorpal.modules.ventes.domain.errors import (
    SalesComplianceError,
    SalesConflictError,
    SalesNotFoundError,
    SalesValidationError,
)
from vorpal.modules.ventes.services.customers import CustomerService
from vorpal.modules.ventes.settings import RoundingSettings


class Repo:
    def __init__(self): self.data = {}
    def get(self, key): return self.data.get(key)
    def save(self, obj): self.data[obj.id] = obj
    def list_all(self, active_only=False):
        values = list(self.data.values())
        return [x for x in values if x.is_active] if active_only else values

class Uow:
    def __init__(self, repo): self.customers = repo
    def __enter__(self): return self
    def __exit__(self, *args): return None

def make_env(perms=frozenset({"ventes.create", "ventes.admin", "ventes.view"})):
    repo = Repo()
    audit = AuditLog()
    deps = SimpleNamespace(
        uow_factory=lambda: Uow(repo),
        publisher=SimpleNamespace(invoice_ready_for_certification=lambda x: None, payment_received=lambda x: None),
        audit=audit, authz=AuthorizationService(),
        clock=lambda: datetime(2026, 11, 20, tzinfo=UTC),
        logger=logging.getLogger("test_customers"),
        settings=SimpleNamespace(default_payment_terms_days=30, default_currency=Currency.USD,
                                 rounding=RoundingSettings(USD=2, CDF=0)),
        id_factory=lambda: "GENERATED",
    )
    actor = User(id="U1", username="tester", direct_permissions=perms)
    return SimpleNamespace(repo=repo, audit=audit, service=CustomerService(deps), actor=actor)

def customer(cid, name="Client"):
    return Customer(partner=Partner(id=cid, name=name, partner_type="client"))

def test_create_customer_uses_defaults_and_records_audit():
    e = make_env()
    c = e.service.create_customer(e.actor, name="Acme")
    assert c.id == "GENERATED"
    assert c.payment_terms.days == 30
    assert c.payment_terms.label == "Net 30 jours"
    assert c.preferred_currency == Currency.USD
    assert e.repo.get(c.id) == c
    assert e.audit.entries[-1].action == "CREATE_CUSTOMER"

def test_create_customer_accepts_explicit_values_and_contacts():
    e = make_env()
    contact = ContactPerson(name="Jean", email="jean@example.com")
    c = e.service.create_customer(e.actor, name="Acme", customer_id="C1",
        nif="NIF1", rccm="RCCM1", address="Lubumbashi",
        contacts=[contact], payment_terms_days=45,
        payment_terms_label="45 jours", preferred_currency=Currency.CDF)
    assert c.contacts == (contact,)
    assert c.payment_terms.days == 45
    assert c.preferred_currency == Currency.CDF
    assert c.nif == "NIF1"

def test_create_customer_rejects_duplicate_id():
    e = make_env()
    e.repo.save(customer("C1"))
    import pytest
    with pytest.raises(SalesConflictError):
        e.service.create_customer(e.actor, name="Autre", customer_id="C1")

def test_update_customer_changes_fields_and_terms():
    e = make_env()
    e.repo.save(customer("C1"))
    contact = ContactPerson(name="Paul")
    updated = e.service.update_customer(e.actor, "C1", name="Nouveau",
        nif="NIF2", contacts=[contact], payment_terms_days=60,
        preferred_currency=Currency.CDF)
    assert updated.name == "Nouveau"
    assert updated.nif == "NIF2"
    assert updated.contacts == (contact,)
    assert updated.payment_terms.label == "Net 60 jours"
    assert updated.preferred_currency == Currency.CDF
    assert e.audit.entries[-1].action == "UPDATE_CUSTOMER"

def test_update_customer_preserves_unchanged_fields():
    e = make_env()
    original = customer("C1", "Original")
    e.repo.save(original)
    updated = e.service.update_customer(e.actor, "C1", address="Nouvelle adresse")
    assert updated.name == "Original"
    assert updated.address == "Nouvelle adresse"
    assert updated.payment_terms == original.payment_terms

def test_update_customer_rejects_blank_name():
    import pytest
    e = make_env()
    e.repo.save(customer("C1"))
    with pytest.raises(SalesValidationError):
        e.service.update_customer(e.actor, "C1", name="   ")

def test_update_customer_rejects_unknown_customer():
    import pytest
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.update_customer(e.actor, "absent", name="X")

def test_deactivate_and_reactivate_customer():
    e = make_env()
    e.repo.save(customer("C1"))
    inactive = e.service.deactivate_customer(e.actor, "C1")
    assert inactive.is_active is False
    active = e.service.reactivate_customer(e.actor, "C1")
    assert active.is_active is True
    assert e.audit.entries[-2].action == "DEACTIVATE_CUSTOMER"
    assert e.audit.entries[-1].action == "REACTIVATE_CUSTOMER"

def test_admin_operations_reject_missing_permission():
    import pytest
    e = make_env(frozenset({"ventes.view"}))
    e.repo.save(customer("C1"))
    with pytest.raises(SalesComplianceError):
        e.service.deactivate_customer(e.actor, "C1")

def test_get_and_list_customers_are_deterministic():
    e = make_env()
    e.repo.save(customer("Z"))
    e.repo.save(customer("A"))
    assert e.service.get_customer(e.actor, "A").id == "A"
    assert [x.id for x in e.service.list_customers(e.actor)] == ["A", "Z"]

def test_list_customers_can_filter_inactive():
    e = make_env()
    e.repo.save(customer("A"))
    e.repo.save(customer("B").deactivate())
    assert [x.id for x in e.service.list_customers(e.actor, active_only=True)] == ["A"]

def test_get_customer_rejects_unknown_id():
    import pytest
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.get_customer(e.actor, "missing")

def test_view_permission_is_required():
    import pytest
    e = make_env(frozenset({"ventes.create"}))
    with pytest.raises(SalesComplianceError):
        e.service.list_customers(e.actor)


def test_deactivate_customer_rejects_unknown_id():
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.deactivate_customer(e.actor, "MISSING")

def test_reactivate_customer_rejects_unknown_id():
    e = make_env()
    with pytest.raises(SalesNotFoundError):
        e.service.reactivate_customer(e.actor, "MISSING")
