from decimal import Decimal

from odoo import _, api, models
from odoo.exceptions import AccessError

from ..ohada.engine import evaluate, load_definition


class VorpalOhadaReport(models.AbstractModel):
    """Calcul des états OHADA depuis la balance. Rendu via RPC (Community n'a pas de moteur)."""

    _name = "vorpal.ohada.report"
    _description = "États financiers OHADA Vorpal"

    @api.model
    def balances(self, date_from, date_to, company_id=None):
        """Solde (débit - crédit) par code de compte, écritures validées uniquement."""
        if not self.env.user.has_group("account.group_account_readonly"):
            raise AccessError(_("Droit comptable requis."))
        company = company_id or self.env.company.id
        groups = self.env["account.move.line"]._read_group(
            [("parent_state", "=", "posted"), ("company_id", "=", company),
             ("date", ">=", date_from), ("date", "<=", date_to)],
            ["account_id"], ["balance:sum"])
        return {account.code: str(total) for account, total in groups}

    @api.model
    def compute(self, statement, date_from, date_to, company_id=None):
        raw = self.balances(date_from, date_to, company_id)
        lines = evaluate(load_definition(statement), {k: Decimal(v) for k, v in raw.items()})
        return [{"code": ln.code, "label": ln.label, "level": ln.level, "value": str(ln.value)}
                for ln in lines]
