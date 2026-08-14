from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PrintLocalization(Base):
    """Localized display/content data attached to one physical Print.

    Card and Set remain canonical identities. A localized source may describe the
    same canonical card/set while owning a distinct physical Print language.
    """

    __tablename__ = "print_localizations"
    __table_args__ = (
        UniqueConstraint(
            "print_id",
            "language",
            "source",
            name="uq_print_localizations_print_language_source",
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
