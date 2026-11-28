"""Modèle de la base de contrôle (brique B1, cahier §5.1).

Une seule base de contrôle (entreprises, utilisateurs, appartenances, rôles, sessions, appareils,
abonnements, tâches, sauvegardes, journaux) ; les tables des modules métier vivent dans la base de
chaque entreprise. Toutes les contraintes et tous les index portent un nom explicite, repris à
l'identique par la migration Alembic ``0001``.

``site_id`` est une colonne réservée (multi-sites, reporté) : toujours NULL en V1.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ENTREPRISE_STATUTS = ("en_attente_validation", "active", "suspendue", "archivee")
ABONNEMENT_STATUTS = ("essai", "actif", "grace", "suspendu", "resilie")
TACHE_STATUTS = ("en_attente", "en_cours", "terminee", "echouee")
SAUVEGARDE_STATUTS = ("en_cours", "terminee", "echouee", "verifiee")

# BIGINT en PostgreSQL ; INTEGER en SQLite (seul type qui s'auto-incrémente comme clé primaire).
BigId = BigInteger().with_variant(Integer(), "sqlite")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid() -> str:
    return str(uuid.uuid4())


class ControlBase(DeclarativeBase):
    """Base déclarative propre à la base de contrôle (distincte de celle des modules)."""


def _created() -> Mapped[datetime]:
    return mapped_column("cree_le", DateTime(timezone=True), server_default=func.now(), nullable=False)


class Entreprise(ControlBase):
    __tablename__ = "entreprises"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_entreprises_slug"),
        UniqueConstraint("nom_base", name="uq_entreprises_nom_base"),
        CheckConstraint(_in("statut", ENTREPRISE_STATUTS), name="ck_entreprises_statut_valide"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    nom: Mapped[str] = mapped_column(String(200), nullable=False)
    statut: Mapped[str] = mapped_column(
        String(30), nullable=False, default="en_attente_validation",
        server_default="en_attente_validation",
    )
    # Base PostgreSQL de l'entreprise : renseignée seulement après validation (cahier §5.1).
    nom_base: Mapped[str | None] = mapped_column(String(63), nullable=True)
    cree_le: Mapped[datetime] = _created()
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Utilisateur(ControlBase):
    __tablename__ = "utilisateurs"
    __table_args__ = (
        UniqueConstraint("email", name="uq_utilisateurs_email"),
        CheckConstraint("email = lower(email)", name="ck_utilisateurs_email_minuscules"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    nom: Mapped[str] = mapped_column(String(200), nullable=False)
    # Empreinte Argon2id (brique B3) ; jamais de mot de passe en clair.
    mot_de_passe_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mot_de_passe_temporaire: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    actif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    cree_le: Mapped[datetime] = _created()
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Role(ControlBase):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("entreprise_id", "code", name="uq_roles_entreprise_code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # NULL = rôle par défaut de la Plateforme.
    entreprise_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_roles_entreprise_id", ondelete="CASCADE"),
        nullable=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)


class Appartenance(ControlBase):
    __tablename__ = "appartenances"
    __table_args__ = (
        UniqueConstraint("utilisateur_id", "entreprise_id", name="uq_appartenances_utilisateur_entreprise"),
        Index("ix_appartenances_entreprise_id", "entreprise_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    utilisateur_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("utilisateurs.id", name="fk_appartenances_utilisateur_id", ondelete="CASCADE"),
        nullable=False,
    )
    entreprise_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("entreprises.id", name="fk_appartenances_entreprise_id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("roles.id", name="fk_appartenances_role_id"), nullable=False
    )
    actif: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # réservé
    cree_le: Mapped[datetime] = _created()


class Appareil(ControlBase):
    __tablename__ = "appareils"
    __table_args__ = (UniqueConstraint("utilisateur_id", "device_id", name="uq_appareils_utilisateur_device"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    utilisateur_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("utilisateurs.id", name="fk_appareils_utilisateur_id", ondelete="CASCADE"),
        nullable=False,
    )
    device_id: Mapped[str] = mapped_column(String(64), nullable=False)
    libelle: Mapped[str | None] = mapped_column(String(100), nullable=True)
    de_confiance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    site_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # réservé
    cree_le: Mapped[datetime] = _created()
    dernier_vu_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoque_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Session(ControlBase):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_utilisateur_id", "utilisateur_id"),)

    # Empreinte SHA-256 (hex) de l'identifiant de session ; l'identifiant lui-même n'est jamais stocké.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    utilisateur_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("utilisateurs.id", name="fk_sessions_utilisateur_id", ondelete="CASCADE"),
        nullable=False,
    )
    entreprise_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_sessions_entreprise_id", ondelete="CASCADE"),
        nullable=True,
    )
    appareil_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("appareils.id", name="fk_sessions_appareil_id", ondelete="SET NULL"),
        nullable=True,
    )
    cree_le: Mapped[datetime] = _created()
    derniere_activite_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoque_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    adresse_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TentativeConnexion(ControlBase):
    __tablename__ = "tentatives_connexion"
    __table_args__ = (
        Index("ix_tentatives_connexion_identifiant_cree_le", "identifiant", "cree_le"),
        Index("ix_tentatives_connexion_adresse_ip_cree_le", "adresse_ip", "cree_le"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    identifiant: Mapped[str] = mapped_column(String(254), nullable=False)
    adresse_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    reussie: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cree_le: Mapped[datetime] = _created()


class OctroiSupport(ControlBase):
    __tablename__ = "octrois_support"
    __table_args__ = (CheckConstraint("fin_le > debut_le", name="ck_octrois_support_periode_valide"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entreprise_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_octrois_support_entreprise_id", ondelete="CASCADE"),
        nullable=False,
    )
    accorde_par: Mapped[str] = mapped_column(
        String(36), ForeignKey("utilisateurs.id", name="fk_octrois_support_accorde_par"), nullable=False
    )
    beneficiaire_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("utilisateurs.id", name="fk_octrois_support_beneficiaire_id"), nullable=False
    )
    motif: Mapped[str | None] = mapped_column(Text, nullable=True)
    debut_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoque_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Tache(ControlBase):
    __tablename__ = "taches"
    __table_args__ = (
        CheckConstraint(_in("statut", TACHE_STATUTS), name="ck_taches_statut_valide"),
        Index("ix_taches_statut_planifiee_le", "statut", "planifiee_le"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(100), nullable=False)
    entreprise_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_taches_entreprise_id", ondelete="CASCADE"),
        nullable=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    statut: Mapped[str] = mapped_column(
        String(20), nullable=False, default="en_attente", server_default="en_attente"
    )
    tentatives: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    erreur: Mapped[str | None] = mapped_column(Text, nullable=True)
    planifiee_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    cree_le: Mapped[datetime] = _created()
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Sauvegarde(ControlBase):
    __tablename__ = "sauvegardes"
    __table_args__ = (
        CheckConstraint(_in("statut", SAUVEGARDE_STATUTS), name="ck_sauvegardes_statut_valide"),
        Index("ix_sauvegardes_entreprise_id_cree_le", "entreprise_id", "cree_le"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entreprise_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_sauvegardes_entreprise_id", ondelete="CASCADE"),
        nullable=False,
    )
    chemin: Mapped[str] = mapped_column(String(500), nullable=False)
    taille_octets: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    empreinte_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chiffree: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    statut: Mapped[str] = mapped_column(
        String(20), nullable=False, default="en_cours", server_default="en_cours"
    )
    cree_le: Mapped[datetime] = _created()
    verifiee_le: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JournalControle(ControlBase):
    __tablename__ = "journal_controle"
    __table_args__ = (Index("ix_journal_controle_entreprise_id_cree_le", "entreprise_id", "cree_le"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    acteur_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("utilisateurs.id", name="fk_journal_controle_acteur_id", ondelete="SET NULL"),
        nullable=True,
    )
    entreprise_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_journal_controle_entreprise_id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    cree_le: Mapped[datetime] = _created()


class CleIdempotence(ControlBase):
    __tablename__ = "cles_idempotence"
    __table_args__ = (Index("ix_cles_idempotence_expire_le", "expire_le"),)

    entreprise_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("entreprises.id", name="fk_cles_idempotence_entreprise_id", ondelete="CASCADE"),
        primary_key=True,
    )
    cle: Mapped[str] = mapped_column(String(100), primary_key=True)
    empreinte_requete: Mapped[str] = mapped_column(String(64), nullable=False)
    statut_http: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reponse: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    cree_le: Mapped[datetime] = _created()
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Abonnement(ControlBase):
    __tablename__ = "abonnements"
    __table_args__ = (
        UniqueConstraint("entreprise_id", name="uq_abonnements_entreprise_id"),
        CheckConstraint(_in("statut", ABONNEMENT_STATUTS), name="ck_abonnements_statut_valide"),
        CheckConstraint("delai_grace_jours >= 0", name="ck_abonnements_delai_grace_positif"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    entreprise_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_abonnements_entreprise_id", ondelete="CASCADE"),
        nullable=False,
    )
    statut: Mapped[str] = mapped_column(String(20), nullable=False, default="essai", server_default="essai")
    echeance_le: Mapped[date | None] = mapped_column(Date, nullable=True)
    delai_grace_jours: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    modifie_le: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JournalLicence(ControlBase):
    __tablename__ = "journal_licences"
    __table_args__ = (Index("ix_journal_licences_entreprise_id_cree_le", "entreprise_id", "cree_le"),)

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    entreprise_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("entreprises.id", name="fk_journal_licences_entreprise_id", ondelete="CASCADE"),
        nullable=False,
    )
    evenement: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    acteur_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("utilisateurs.id", name="fk_journal_licences_acteur_id", ondelete="SET NULL"),
        nullable=True,
    )
    cree_le: Mapped[datetime] = _created()
