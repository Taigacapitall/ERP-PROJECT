# legacy/ : code archivé (décision A1, cahier v1.1 §8.1 et §11 point 1)

Contenu déplacé tel quel, sans modification, depuis le dépôt :

| Archive | Emplacement d'origine |
|---|---|
| `legacy/vorpal/modules/adapters/odoo/` | `vorpal/modules/adapters/odoo/` |
| `legacy/odoo_addons/` | `odoo_addons/` |
| `legacy/tests/adapters/` | `tests/adapters/` |
| `legacy/tests/odoo_addons/` | `tests/odoo_addons/` |

Ce code est hors paquet (`pyproject.toml` n'inclut que `vorpal*`), hors CI et exclu de Ruff (`extend-exclude`).
Il conserve ses erreurs connues (Ruff, Mypy) ; il n'est ni maintenu ni testé.

Pour le restaurer : `git mv` de chaque dossier vers son emplacement d'origine, puis rétablir dans
`pyproject.toml` la ligne `"vorpal.modules.adapters.odoo" = ["data/*.json"]` (section `package-data`).
