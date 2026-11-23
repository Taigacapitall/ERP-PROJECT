# Rapport d'intégration — comptabilité OHADA (21 novembre 2026)
**Réalisé** : intégration du ZIP livré dans l'arbre du dépôt (sans écrasement), réécriture des handlers sur les contrats réels, pont `AccountingPoster`, module `BaseModule`, boîte de corrélation SQLite, mapping aligné, tests réécrits.
**Problèmes rencontrés** : (1) `invoice.certified` ne porte pas les montants → corrélation avec `ready_for_certification` ; (2) DGI refuse les avoirs via le contrat Core et `credit_note.certified` n'a pas de montants → `register_document()` ; (3) `payment.received` sans NIF ni facture → `partner_resolver`, lettrage manuel ; (4) `PayrollResultDTO` sans devise, sans producteur ; (5) Achats n'émet aucun événement comptable ; (6) Core sans taux de change → taux lu dans Odoo ; (7) environnement sans pydantic/pytest/réseau.
**Décisions** : voir HANDOFF §4. **Core v1** inchangé, pas de commit/push.
**A vérifier** : suite pytest complète, ruff/mypy, Odoo 19 réel, validation expert-comptable et DGI.
