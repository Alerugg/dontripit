from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.ingest.base import IngestStats, SourceConnector
from app.ingest.provenance import upsert_field_provenance
from app.models import (
    Card,
    Game,
    Print,
    PrintIdentifier,
    PrintImage,
    Product,
    ProductIdentifier,
    ProductImage,
    ProductVariant,
    Set,
)


class FixtureLocalConnector(SourceConnector):
    name = "fixture_local"

    def load(self, path: str | Path | None, **kwargs) -> list[tuple[Path, dict, str]]:
        if path is None:
            raise ValueError("fixture_local requires --path")
        root = Path(path)
        if not root.exists():
            repo_root = Path(__file__).resolve().parents[3]
            for candidate in (repo_root / str(path), repo_root / "backend" / str(path)):
                if candidate.exists():
                    root = candidate
                    break
        files = sorted(root.glob("*.json")) if root.is_dir() else [root]
        payloads = []
        for item in files:
            raw = item.read_bytes()
            payloads.append((item, json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()))
        return payloads

    def normalize(self, payload: dict, **kwargs) -> dict:
        return payload

    def upsert(self, session, payload: dict, stats: IngestStats, **kwargs) -> dict:
        game_payload = payload.get("game") or {}
        game_slug = game_payload.get("slug")
        game_name = game_payload.get("name")
        game = session.execute(select(Game).where(Game.slug == game_slug)).scalar_one_or_none() if game_slug else None
        if game is None and game_slug and game_name:
            game = Game(slug=game_slug, name=game_name)
            session.add(game)
            session.flush()
            stats.records_inserted += 1
        elif game and game_name and game.name != game_name:
            game.name = game_name
            stats.records_updated += 1
        if game is None:
            return {}

        sets_by_code: dict[str, Set] = {}
        for item in payload.get("sets") or []:
            code = item.get("code")
            if not code:
                continue
            set_row = session.execute(select(Set).where(Set.game_id == game.id, Set.code == code)).scalar_one_or_none()
            release_date = date.fromisoformat(item["release_date"]) if item.get("release_date") else None
            if set_row is None:
                set_row = Set(game_id=game.id, code=code, name=item.get("name") or code, release_date=release_date)
                session.add(set_row)
                session.flush()
                stats.records_inserted += 1
            else:
                set_row.name = item.get("name") or set_row.name
                set_row.release_date = release_date or set_row.release_date
                stats.records_updated += 1
            sets_by_code[code] = set_row

        cards_by_name: dict[str, Card] = {}
        for item in payload.get("cards") or []:
            name = item.get("name")
            if not name:
                continue
            card_row = session.execute(select(Card).where(Card.game_id == game.id, Card.name == name)).scalar_one_or_none()
            if card_row is None:
                card_row = Card(game_id=game.id, name=name)
                session.add(card_row)
                session.flush()
                stats.records_inserted += 1
            cards_by_name[name] = card_row

        for item in payload.get("prints") or []:
            set_row = sets_by_code.get(item.get("set_code"))
            card_row = cards_by_name.get(item.get("card_name"))
            if not set_row or not card_row:
                continue
            collector_number = item.get("collector_number")
            print_row = session.execute(
                select(Print).where(
                    Print.set_id == set_row.id,
                    Print.card_id == card_row.id,
                    Print.collector_number == collector_number,
                )
            ).scalar_one_or_none()
            if print_row is None:
                print_row = Print(
                    set_id=set_row.id,
                    card_id=card_row.id,
                    collector_number=collector_number,
                    language=item.get("language"),
                    rarity=item.get("rarity"),
                    is_foil=bool(item.get("is_foil", False)),
                )
                session.add(print_row)
                session.flush()
                stats.records_inserted += 1
            else:
                print_row.language = item.get("language") or print_row.language
                print_row.rarity = item.get("rarity") or print_row.rarity
                print_row.is_foil = bool(item.get("is_foil", print_row.is_foil))
                stats.records_updated += 1

            for image in item.get("images") or []:
                url = image.get("url")
                if not url:
                    continue
                existing = session.execute(select(PrintImage).where(PrintImage.print_id == print_row.id, PrintImage.url == url)).scalar_one_or_none()
                if existing is None:
                    session.add(PrintImage(print_id=print_row.id, url=url, is_primary=bool(image.get("is_primary", False)), source=self.name))
                    stats.records_inserted += 1

            for identifier in item.get("identifiers") or []:
                source = identifier.get("source")
                external_id = identifier.get("external_id")
                if not source or not external_id:
                    continue
                existing = session.execute(
                    select(PrintIdentifier).where(PrintIdentifier.print_id == print_row.id, PrintIdentifier.source == source)
                ).scalar_one_or_none()
                if existing is None:
                    session.add(PrintIdentifier(print_id=print_row.id, source=source, external_id=external_id))
                    stats.records_inserted += 1

            upsert_field_provenance(
                session,
                "print",
                print_row.id,
                kwargs.get("source_name", self.name),
                {
                    "rarity": print_row.rarity,
                    "language": print_row.language,
                    "collector_number": print_row.collector_number,
                },
            )

        for product in payload.get("products") or []:
            set_row = sets_by_code.get(product.get("set_code"))
            release_date = date.fromisoformat(product["release_date"]) if product.get("release_date") else None
            product_row = session.execute(
                select(Product).where(
                    Product.game_id == game.id,
                    Product.set_id == (set_row.id if set_row else None),
                    Product.product_type == product.get("product_type"),
                    Product.name == product.get("name"),
                )
            ).scalar_one_or_none()
            if product_row is None:
                product_row = Product(
                    game_id=game.id,
                    set_id=set_row.id if set_row else None,
                    product_type=product.get("product_type") or "unknown",
                    name=product.get("name") or "Unnamed product",
                    release_date=release_date,
                )
                session.add(product_row)
                session.flush()
                stats.records_inserted += 1

            for variant in product.get("variants") or []:
                variant_row = session.execute(
                    select(ProductVariant).where(
                        ProductVariant.product_id == product_row.id,
                        ProductVariant.language == variant.get("language"),
                        ProductVariant.region == variant.get("region"),
                        ProductVariant.packaging == variant.get("packaging"),
                    )
                ).scalar_one_or_none()
                if variant_row is None:
                    variant_row = ProductVariant(
                        product_id=product_row.id,
                        language=variant.get("language") or "EN",
                        region=variant.get("region") or "US",
                        packaging=variant.get("packaging"),
                        sku=variant.get("sku"),
                    )
                    session.add(variant_row)
                    session.flush()
                    stats.records_inserted += 1

                for image in variant.get("images") or []:
                    url = image.get("url")
                    if not url:
                        continue
                    existing = session.execute(
                        select(ProductImage).where(ProductImage.product_variant_id == variant_row.id, ProductImage.url == url)
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(ProductImage(product_variant_id=variant_row.id, url=url, is_primary=bool(image.get("is_primary", False)), source=image.get("source", self.name)))
                        stats.records_inserted += 1

                for identifier in variant.get("identifiers") or []:
                    source = identifier.get("source")
                    external_id = identifier.get("external_id")
                    if not source or not external_id:
                        continue
                    existing = session.execute(
                        select(ProductIdentifier).where(ProductIdentifier.source == source, ProductIdentifier.external_id == external_id)
                    ).scalar_one_or_none()
                    if existing is None:
                        session.add(ProductIdentifier(product_variant_id=variant_row.id, source=source, external_id=external_id))
                        stats.records_inserted += 1

        return {}
