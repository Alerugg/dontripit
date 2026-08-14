from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class SetIdentifier(Base):
    """Language/source-qualified identity for a canonical or regional Set."""

    __tablename__ = "set_identifiers"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_set_identifier_source_external"),
        UniqueConstraint("set_id", "source", name="uq_set_identifier_set_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(
        ForeignKey("sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CardIdentifier(Base):
    """Language/source-qualified aliases for canonical or regional Cards.

    ``(source, external_id)`` is unique, but several external IDs from one
    source may intentionally point to the same canonical gameplay Card when the
    card has physical reprints. This is many-to-one aliasing, not a collision.
    """

    __tablename__ = "card_identifiers"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_card_identifier_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PrintLocalization(Base):
    """Localized display/content data attached to one physical Print.

    Card and Set remain canonical identities where the source genuinely shares
    identity (for example TCGdex EN/ES). Regional catalogs that use a different
    physical identity space (currently TCGdex JA) may own independent Card/Set
    rows while still using this table for language-aware display/content data.

    There is exactly one authoritative localization row per physical print and
    language. ``source`` is provenance, not an additional identity dimension.
    """

    __tablename__ = "print_localizations"
    __table_args__ = (
        UniqueConstraint(
            "print_id",
            "language",
            name="uq_print_localizations_print_language",
        ),
        Index("ix_print_localizations_language_card_name", "language", "card_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(
        ForeignKey("prints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    card_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    set_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )