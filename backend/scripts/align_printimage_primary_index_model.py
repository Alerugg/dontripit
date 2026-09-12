from pathlib import Path

path = Path(__file__).resolve().parents[1] / "app" / "models.py"
text = path.read_text(encoding="utf-8")
old = '''class PrintImage(Base):
    __tablename__ = "print_images"

    id: Mapped[int] = mapped_column(primary_key=True)
'''
new = '''class PrintImage(Base):
    __tablename__ = "print_images"
    __table_args__ = (
        Index(
            "uq_print_images_one_primary_per_print",
            "print_id",
            unique=True,
            postgresql_where=text("is_primary IS TRUE"),
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"PrintImage model anchor count={count}; refusing patch")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("PrintImage ORM aligned with uq_print_images_one_primary_per_print")
