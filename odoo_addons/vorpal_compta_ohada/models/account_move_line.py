from odoo import _, models
from odoo.exceptions import UserError

from ..ohada.protection import line_write_violations


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def write(self, vals):
        locked = self.filtered(lambda line: line.move_id.vorpal_dgi_code)
        if locked:
            bad = line_write_violations(vals.keys(), True)
            if bad:
                raise UserError(_("Lignes d'une écriture certifiée DGI non modifiables (%s).",
                                  ", ".join(bad)))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda line: line.move_id.vorpal_dgi_code):
            raise UserError(_("Lignes d'une écriture certifiée DGI non supprimables."))
        return super().unlink()
