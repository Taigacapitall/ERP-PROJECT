from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..ohada.protection import may_reset_or_delete, move_write_violations


class AccountMove(models.Model):
    _inherit = "account.move"

    vorpal_source_ref = fields.Char(
        string="Référence source Vorpal", index=True, copy=False, readonly=True,
        help="Identifiant d'événement Vorpal (idempotence). Unique par société.")
    vorpal_dgi_code = fields.Char(string="Code DGI", copy=False, readonly=True)
    vorpal_dgi_qr = fields.Text(string="QR DGI", copy=False, readonly=True)

    def init(self):
        super().init()
        # Index unique partiel : version-agnostique (évite _sql_constraints / models.Constraint).
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS account_move_vorpal_source_ref_uniq "
            "ON account_move (company_id, vorpal_source_ref) "
            "WHERE vorpal_source_ref IS NOT NULL")

    @api.constrains("vorpal_source_ref", "company_id")
    def _check_vorpal_source_ref_unique(self):
        for move in self.filtered("vorpal_source_ref"):
            dup = self.search_count([
                ("id", "!=", move.id), ("company_id", "=", move.company_id.id),
                ("vorpal_source_ref", "=", move.vorpal_source_ref)])
            if dup:
                raise ValidationError(_("Référence source Vorpal déjà utilisée : %s",
                                        move.vorpal_source_ref))

    def _vorpal_locked(self):
        return self.filtered("vorpal_dgi_code")

    def write(self, vals):
        for move in self._vorpal_locked():
            bad = move_write_violations(vals.keys(), True)
            if bad:
                raise UserError(_(
                    "Écriture %(name)s certifiée DGI : modification interdite (%(fields)s). "
                    "Corrigez par un avoir.", name=move.display_name, fields=", ".join(bad)))
        return super().write(vals)

    def unlink(self):
        if not may_reset_or_delete(bool(self._vorpal_locked())):
            raise UserError(_("Une écriture certifiée DGI ne peut pas être supprimée."))
        return super().unlink()

    def button_draft(self):
        if self._vorpal_locked():
            raise UserError(_("Une écriture certifiée DGI ne peut pas revenir en brouillon."))
        return super().button_draft()

    def button_cancel(self):
        if self._vorpal_locked():
            raise UserError(_("Une écriture certifiée DGI ne peut pas être annulée : émettez un avoir."))
        return super().button_cancel()
