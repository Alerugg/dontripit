from __future__ import annotations

from dataclasses import dataclass, field

from app.ingest.normalization import trim_or_none


@dataclass(slots=True)
class NormalizedExternalId:
    source: str
    id_type: str
    value: str


@dataclass(slots=True)
class NormalizedGame:
    slug: str
    name: str


@dataclass(slots=True)
class NormalizedSet:
    source_key: str
    code: str
    name: str
    release_date: str | None = None
    external_ids: list[NormalizedExternalId] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedCard:
    source_key: str
    canonical_name: str
    name_normalized: str
    card_key: str
    identity_hints: dict = field(default_factory=dict)
    external_ids: list[NormalizedExternalId] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedPrint:
    source_key: str
    set_source_key: str
    collector_number: str
    collector_number_norm: str
    language: str
    finish: str
    variant_key: str
    rarity: str | None = None
    print_key: str | None = None
    external_ids: list[NormalizedExternalId] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedImage:
    print_source_key: str
    url: str
    is_primary: bool = True
    source: str | None = None
    image_type: str | None = None


@dataclass(slots=True)
class NormalizedPayload:
    normalized_game: NormalizedGame
    normalized_set: NormalizedSet
    normalized_card: NormalizedCard
    normalized_prints: list[NormalizedPrint]
    normalized_sets: list[NormalizedSet] = field(default_factory=list)
    normalized_images: list[NormalizedImage] = field(default_factory=list)
    normalized_external_ids: list[NormalizedExternalId] = field(default_factory=list)
    source_metadata: dict | None = None


class NormalizedPayloadError(ValueError):
    pass


def _parse_external_ids(items: list[dict] | None) -> list[NormalizedExternalId]:
    parsed: list[NormalizedExternalId] = []
    for item in items or []:
        source = trim_or_none(item.get("source"))
        id_type = trim_or_none(item.get("id_type"))
        value = trim_or_none(item.get("value"))
        if not (source and id_type and value):
            raise NormalizedPayloadError("external_ids require source, id_type and value")
        parsed.append(NormalizedExternalId(source=source, id_type=id_type, value=value))
    return parsed


def parse_normalized_payload(payload: dict) -> NormalizedPayload:
    try:
        game_raw = payload["normalized_game"]
        set_raw = payload["normalized_set"]
        card_raw = payload["normalized_card"]
        prints_raw = payload["normalized_prints"]
    except KeyError as exc:
        raise NormalizedPayloadError(f"missing required normalized section: {exc.args[0]}") from exc

    if not isinstance(prints_raw, list) or not prints_raw:
        raise NormalizedPayloadError("normalized_prints must be a non-empty list")

    normalized_game = NormalizedGame(
        slug=trim_or_none(game_raw.get("slug")) or "",
        name=trim_or_none(game_raw.get("name")) or "",
    )
    normalized_set = NormalizedSet(
        source_key=trim_or_none(set_raw.get("source_key")) or "",
        code=trim_or_none(set_raw.get("code")) or "",
        name=trim_or_none(set_raw.get("name")) or "",
        release_date=trim_or_none(set_raw.get("release_date")),
        external_ids=_parse_external_ids(set_raw.get("external_ids")),
        raw=set_raw.get("raw") or {},
    )
    normalized_card = NormalizedCard(
        source_key=trim_or_none(card_raw.get("source_key")) or "",
        canonical_name=trim_or_none(card_raw.get("canonical_name")) or "",
        name_normalized=trim_or_none(card_raw.get("name_normalized")) or "",
        card_key=trim_or_none(card_raw.get("card_key")) or "",
        identity_hints=card_raw.get("identity_hints") or {},
        external_ids=_parse_external_ids(card_raw.get("external_ids")),
        raw=card_raw.get("raw") or {},
    )

    normalized_prints: list[NormalizedPrint] = []
    for print_item in prints_raw:
        normalized_prints.append(
            NormalizedPrint(
                source_key=trim_or_none(print_item.get("source_key")) or "",
                set_source_key=trim_or_none(print_item.get("set_source_key")) or "",
                collector_number=trim_or_none(print_item.get("collector_number")) or "",
                collector_number_norm=trim_or_none(print_item.get("collector_number_norm")) or "",
                language=trim_or_none(print_item.get("language")) or "",
                finish=trim_or_none(print_item.get("finish")) or "",
                variant_key=trim_or_none(print_item.get("variant_key")) or "",
                rarity=trim_or_none(print_item.get("rarity")),
                print_key=trim_or_none(print_item.get("print_key")),
                external_ids=_parse_external_ids(print_item.get("external_ids")),
                raw=print_item.get("raw") or {},
            )
        )

    normalized_sets = [normalized_set]
    for set_item in payload.get("normalized_sets") or []:
        normalized_sets.append(
            NormalizedSet(
                source_key=trim_or_none(set_item.get("source_key")) or "",
                code=trim_or_none(set_item.get("code")) or "",
                name=trim_or_none(set_item.get("name")) or "",
                release_date=trim_or_none(set_item.get("release_date")),
                external_ids=_parse_external_ids(set_item.get("external_ids")),
                raw=set_item.get("raw") or {},
            )
        )

    normalized_images = [
        NormalizedImage(
            print_source_key=trim_or_none(item.get("print_source_key")) or "",
            url=trim_or_none(item.get("url")) or "",
            is_primary=bool(item.get("is_primary", True)),
            source=trim_or_none(item.get("source")),
            image_type=trim_or_none(item.get("image_type")),
        )
        for item in payload.get("normalized_images") or []
    ]

    parsed = NormalizedPayload(
        normalized_game=normalized_game,
        normalized_set=normalized_set,
        normalized_card=normalized_card,
        normalized_prints=normalized_prints,
        normalized_sets=normalized_sets,
        normalized_images=normalized_images,
        normalized_external_ids=_parse_external_ids(payload.get("normalized_external_ids")),
        source_metadata=payload.get("source_metadata"),
    )
    _validate(parsed)
    return parsed


def _validate(payload: NormalizedPayload) -> None:
    if not payload.normalized_game.slug or not payload.normalized_game.name:
        raise NormalizedPayloadError("normalized_game.slug and normalized_game.name are required")
    if not payload.normalized_set.source_key or not payload.normalized_set.code:
        raise NormalizedPayloadError("normalized_set.source_key and normalized_set.code are required")
    if not payload.normalized_card.source_key or not payload.normalized_card.card_key:
        raise NormalizedPayloadError("normalized_card.source_key and normalized_card.card_key are required")

    print_source_keys: set[str] = set()
    valid_set_source_keys = {item.source_key for item in payload.normalized_sets} or {payload.normalized_set.source_key}

    for item in payload.normalized_prints:
        if not item.source_key:
            raise NormalizedPayloadError("normalized_prints[].source_key is required")
        if item.source_key in print_source_keys:
            raise NormalizedPayloadError(f"duplicate print source_key in payload: {item.source_key}")
        print_source_keys.add(item.source_key)
        if not item.print_key:
            raise NormalizedPayloadError(f"normalized_prints[{item.source_key}] missing print_key")
        if item.set_source_key not in valid_set_source_keys:
            raise NormalizedPayloadError("normalized_print.set_source_key must reference a normalized set")

    for image in payload.normalized_images:
        if image.print_source_key not in print_source_keys:
            raise NormalizedPayloadError(
                f"normalized_images references unknown print_source_key={image.print_source_key}"
            )
        if not image.url:
            raise NormalizedPayloadError("normalized_images[].url is required")
