# Installation et lancement

> **Archivé (A1, cahier v1.1 §8.1).** Le code décrit ici est dans `legacy/` (hors paquet, hors CI). Pour le réutiliser : replacer `legacy/vorpal/modules/adapters/odoo` dans `vorpal/modules/adapters/odoo`, `legacy/odoo_addons` à la racine, `legacy/tests/*` dans `tests/`. Voir `legacy/README.md`.

```bash
# 1. Copier sans écraser (essai à blanc d'abord)
bash scripts/install_vorpal_compta.sh /chemin/depot_vorpal --dry-run
bash scripts/install_vorpal_compta.sh /chemin/depot_vorpal

# 2. Tests (depuis le dépôt ; fonctionne avec unittest ou pytest)
export VORPAL_SYSCOHADA_CSV=/chemin/l10n_syscohada/data/template/account.account-syscohada.csv   # optionnel
python -m unittest discover -s tests -t . -v        # ou : python -m pytest tests/adapters tests/odoo_addons

# 3. Odoo : copier odoo_addons/vorpal_compta_ohada dans le addons_path, puis
odoo-bin -d <base> -i vorpal_compta_ohada --stop-after-init
odoo-bin -d <base_test> -i vorpal_compta_ohada --test-tags /vorpal_compta_ohada --stop-after-init

# 4. Configuration de l'adaptateur (jamais en dur)
export VORPAL_ODOO_URL=https://odoo.exemple VORPAL_ODOO_DB=... VORPAL_ODOO_USER=... VORPAL_ODOO_API_KEY=...
```
Câblage : `OdooAccountingModule(AccountingEventHandlers(OdooAccountingPoster(XmlRpcOdooClient(OdooConfig.from_env())), SqlitePendingQueue("odoo_queue.db"), AccountMapping.load(), publish=...)).start(bus)`, puis appeler `process_pending()` périodiquement.
