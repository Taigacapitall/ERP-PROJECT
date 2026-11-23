{
    "name": "Vorpal - Comptabilité OHADA (RDC)",
    "version": "19.0.1.0.0",
    "category": "Accounting/Localizations",
    "summary": "Extensions Vorpal : traçabilité, verrouillage DGI, journaux et devises RDC, états OHADA",
    "description": """
Extension indépendante d'Odoo Community (héritage uniquement, aucun code Enterprise).
- account.move.vorpal_source_ref (unique par société) : idempotence des écritures Vorpal
- vorpal_dgi_code / vorpal_dgi_qr : cachet DGI, verrouillage des écritures certifiées
- journaux OHADA (ventes, achats, OD, paie, immobilisations, caisses/banques USD et CDF)
- activation USD et CDF
- états financiers OHADA déclaratifs (bilan, compte de résultat) - A VALIDER par expert-comptable
    """,
    "author": "Vorpal Business Management",
    "license": "LGPL-3",
    "depends": ["account", "l10n_syscohada", "l10n_cd"],
    "data": [],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
