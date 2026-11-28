"""Plan de contrôle initial (brique B1).

Revision ID: 0001
Revises:
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BIG_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TS = sa.DateTime(timezone=True)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def _now(name: str) -> "sa.Column[datetime]":
    return sa.Column(name, TS, server_default=sa.func.now(), nullable=False)


def _fk(table: str, column: str, target: str, ondelete: str | None = None) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{target}.id"], name=f"fk_{table}_{column}", ondelete=ondelete
    )


def upgrade() -> None:
    op.create_table(
        "entreprises",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("nom", sa.String(200), nullable=False),
        sa.Column("statut", sa.String(30), nullable=False, server_default="en_attente_validation"),
        sa.Column("nom_base", sa.String(63), nullable=True),
        _now("cree_le"),
        _now("modifie_le"),
        sa.UniqueConstraint("slug", name="uq_entreprises_slug"),
        sa.UniqueConstraint("nom_base", name="uq_entreprises_nom_base"),
        sa.CheckConstraint(
            "statut IN (" + _quoted(("en_attente_validation", "active", "suspendue", "archivee")) + ")",
            name="ck_entreprises_statut_valide",
        ),
    )
    op.create_table(
        "utilisateurs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("nom", sa.String(200), nullable=False),
        sa.Column("mot_de_passe_hash", sa.String(255), nullable=True),
        sa.Column("mot_de_passe_temporaire", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("actif", sa.Boolean, nullable=False, server_default=sa.true()),
        _now("cree_le"),
        _now("modifie_le"),
        sa.UniqueConstraint("email", name="uq_utilisateurs_email"),
        sa.CheckConstraint("email = lower(email)", name="ck_utilisateurs_email_minuscules"),
    )
    op.create_table(
        "roles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entreprise_id", sa.String(36), nullable=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("nom", sa.String(100), nullable=False),
        sa.Column("permissions", sa.JSON, nullable=False),
        _fk("roles", "entreprise_id", "entreprises", "CASCADE"),
        sa.UniqueConstraint("entreprise_id", "code", name="uq_roles_entreprise_code"),
    )
    op.create_table(
        "appartenances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("utilisateur_id", sa.String(36), nullable=False),
        sa.Column("entreprise_id", sa.String(36), nullable=False),
        sa.Column("role_id", sa.String(36), nullable=False),
        sa.Column("actif", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("site_id", sa.String(36), nullable=True),
        _now("cree_le"),
        _fk("appartenances", "utilisateur_id", "utilisateurs", "CASCADE"),
        _fk("appartenances", "entreprise_id", "entreprises", "CASCADE"),
        _fk("appartenances", "role_id", "roles"),
        sa.UniqueConstraint("utilisateur_id", "entreprise_id", name="uq_appartenances_utilisateur_entreprise"),
    )
    op.create_index("ix_appartenances_entreprise_id", "appartenances", ["entreprise_id"])
    op.create_table(
        "appareils",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("utilisateur_id", sa.String(36), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("libelle", sa.String(100), nullable=True),
        sa.Column("de_confiance", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("site_id", sa.String(36), nullable=True),
        _now("cree_le"),
        sa.Column("dernier_vu_le", TS, nullable=True),
        sa.Column("revoque_le", TS, nullable=True),
        _fk("appareils", "utilisateur_id", "utilisateurs", "CASCADE"),
        sa.UniqueConstraint("utilisateur_id", "device_id", name="uq_appareils_utilisateur_device"),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("utilisateur_id", sa.String(36), nullable=False),
        sa.Column("entreprise_id", sa.String(36), nullable=True),
        sa.Column("appareil_id", sa.String(36), nullable=True),
        _now("cree_le"),
        _now("derniere_activite_le"),
        sa.Column("expire_le", TS, nullable=False),
        sa.Column("revoque_le", TS, nullable=True),
        sa.Column("adresse_ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        _fk("sessions", "utilisateur_id", "utilisateurs", "CASCADE"),
        _fk("sessions", "entreprise_id", "entreprises", "CASCADE"),
        _fk("sessions", "appareil_id", "appareils", "SET NULL"),
    )
    op.create_index("ix_sessions_utilisateur_id", "sessions", ["utilisateur_id"])
    op.create_table(
        "tentatives_connexion",
        sa.Column("id", BIG_ID, primary_key=True, autoincrement=True),
        sa.Column("identifiant", sa.String(254), nullable=False),
        sa.Column("adresse_ip", sa.String(45), nullable=True),
        sa.Column("reussie", sa.Boolean, nullable=False),
        _now("cree_le"),
    )
    op.create_index("ix_tentatives_connexion_identifiant_cree_le", "tentatives_connexion", ["identifiant", "cree_le"])
    op.create_index("ix_tentatives_connexion_adresse_ip_cree_le", "tentatives_connexion", ["adresse_ip", "cree_le"])
    op.create_table(
        "octrois_support",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entreprise_id", sa.String(36), nullable=False),
        sa.Column("accorde_par", sa.String(36), nullable=False),
        sa.Column("beneficiaire_id", sa.String(36), nullable=False),
        sa.Column("motif", sa.Text, nullable=True),
        sa.Column("debut_le", TS, nullable=False),
        sa.Column("fin_le", TS, nullable=False),
        sa.Column("revoque_le", TS, nullable=True),
        _fk("octrois_support", "entreprise_id", "entreprises", "CASCADE"),
        _fk("octrois_support", "accorde_par", "utilisateurs"),
        _fk("octrois_support", "beneficiaire_id", "utilisateurs"),
        sa.CheckConstraint("fin_le > debut_le", name="ck_octrois_support_periode_valide"),
    )
    op.create_table(
        "taches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("type", sa.String(100), nullable=False),
        sa.Column("entreprise_id", sa.String(36), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("statut", sa.String(20), nullable=False, server_default="en_attente"),
        sa.Column("tentatives", sa.Integer, nullable=False, server_default="0"),
        sa.Column("erreur", sa.Text, nullable=True),
        _now("planifiee_le"),
        _now("cree_le"),
        _now("modifie_le"),
        _fk("taches", "entreprise_id", "entreprises", "CASCADE"),
        sa.CheckConstraint(
            "statut IN (" + _quoted(("en_attente", "en_cours", "terminee", "echouee")) + ")",
            name="ck_taches_statut_valide",
        ),
    )
    op.create_index("ix_taches_statut_planifiee_le", "taches", ["statut", "planifiee_le"])
    op.create_table(
        "sauvegardes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entreprise_id", sa.String(36), nullable=False),
        sa.Column("chemin", sa.String(500), nullable=False),
        sa.Column("taille_octets", sa.BigInteger, nullable=True),
        sa.Column("empreinte_sha256", sa.String(64), nullable=True),
        sa.Column("chiffree", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("statut", sa.String(20), nullable=False, server_default="en_cours"),
        _now("cree_le"),
        sa.Column("verifiee_le", TS, nullable=True),
        _fk("sauvegardes", "entreprise_id", "entreprises", "CASCADE"),
        sa.CheckConstraint(
            "statut IN (" + _quoted(("en_cours", "terminee", "echouee", "verifiee")) + ")",
            name="ck_sauvegardes_statut_valide",
        ),
    )
    op.create_index("ix_sauvegardes_entreprise_id_cree_le", "sauvegardes", ["entreprise_id", "cree_le"])
    op.create_table(
        "journal_controle",
        sa.Column("id", BIG_ID, primary_key=True, autoincrement=True),
        sa.Column("acteur_id", sa.String(36), nullable=True),
        sa.Column("entreprise_id", sa.String(36), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        _now("cree_le"),
        _fk("journal_controle", "acteur_id", "utilisateurs", "SET NULL"),
        _fk("journal_controle", "entreprise_id", "entreprises", "SET NULL"),
    )
    op.create_index("ix_journal_controle_entreprise_id_cree_le", "journal_controle", ["entreprise_id", "cree_le"])
    op.create_table(
        "cles_idempotence",
        sa.Column("entreprise_id", sa.String(36), primary_key=True),
        sa.Column("cle", sa.String(100), primary_key=True),
        sa.Column("empreinte_requete", sa.String(64), nullable=False),
        sa.Column("statut_http", sa.Integer, nullable=True),
        sa.Column("reponse", sa.JSON, nullable=True),
        _now("cree_le"),
        sa.Column("expire_le", TS, nullable=False),
        _fk("cles_idempotence", "entreprise_id", "entreprises", "CASCADE"),
    )
    op.create_index("ix_cles_idempotence_expire_le", "cles_idempotence", ["expire_le"])
    op.create_table(
        "abonnements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("entreprise_id", sa.String(36), nullable=False),
        sa.Column("statut", sa.String(20), nullable=False, server_default="essai"),
        sa.Column("echeance_le", sa.Date, nullable=True),
        sa.Column("delai_grace_jours", sa.Integer, nullable=False, server_default="7"),
        _now("modifie_le"),
        _fk("abonnements", "entreprise_id", "entreprises", "CASCADE"),
        sa.UniqueConstraint("entreprise_id", name="uq_abonnements_entreprise_id"),
        sa.CheckConstraint(
            "statut IN (" + _quoted(("essai", "actif", "grace", "suspendu", "resilie")) + ")",
            name="ck_abonnements_statut_valide",
        ),
        sa.CheckConstraint("delai_grace_jours >= 0", name="ck_abonnements_delai_grace_positif"),
    )
    op.create_table(
        "journal_licences",
        sa.Column("id", BIG_ID, primary_key=True, autoincrement=True),
        sa.Column("entreprise_id", sa.String(36), nullable=False),
        sa.Column("evenement", sa.String(50), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("acteur_id", sa.String(36), nullable=True),
        _now("cree_le"),
        _fk("journal_licences", "entreprise_id", "entreprises", "CASCADE"),
        _fk("journal_licences", "acteur_id", "utilisateurs", "SET NULL"),
    )
    op.create_index("ix_journal_licences_entreprise_id_cree_le", "journal_licences", ["entreprise_id", "cree_le"])


def downgrade() -> None:
    for name in (
        "journal_licences", "abonnements", "cles_idempotence", "journal_controle", "sauvegardes",
        "taches", "octrois_support", "tentatives_connexion", "sessions", "appareils",
        "appartenances", "roles", "utilisateurs", "entreprises",
    ):
        op.drop_table(name)
