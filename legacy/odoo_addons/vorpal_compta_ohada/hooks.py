"""Installation : devises USD/CDF et journaux OHADA. Idempotent, n'écrase rien."""
import logging

_logger = logging.getLogger(__name__)

# code, nom, type, devise ('' = devise société)
VORPAL_JOURNALS = [
    ("VTE", "Ventes", "sale", ""),
    ("ACH", "Achats", "purchase", ""),
    ("OD", "Opérations diverses", "general", ""),
    ("PAIE", "Paie", "general", ""),
    ("IMMO", "Immobilisations et amortissements", "general", ""),
    ("CAU", "Caisse USD", "cash", "USD"),
    ("CAF", "Caisse CDF", "cash", "CDF"),
    ("BQU", "Banque USD", "bank", "USD"),
    ("BQF", "Banque CDF", "bank", "CDF"),
]
OHADA_CHARTS = {"cd", "syscohada", "cd_syscebnl", "syscebnl"}


def post_init_hook(env):
    currencies = env["res.currency"].with_context(active_test=False).search(
        [("name", "in", ["USD", "CDF"])])
    currencies.filtered(lambda c: not c.active).write({"active": True})
    by_name = {c.name: c for c in currencies}

    for company in env["res.company"].search([]):
        if getattr(company, "chart_template", None) not in OHADA_CHARTS:
            continue
        for code, name, jtype, cur in VORPAL_JOURNALS:
            if env["account.journal"].search_count(
                    [("code", "=", code), ("company_id", "=", company.id)]):
                continue
            vals = {"name": name, "code": code, "type": jtype, "company_id": company.id}
            if cur and cur != company.currency_id.name and cur in by_name:
                vals["currency_id"] = by_name[cur].id
            env["account.journal"].create(vals)
            _logger.info("Vorpal : journal %s créé pour %s", code, company.name)
