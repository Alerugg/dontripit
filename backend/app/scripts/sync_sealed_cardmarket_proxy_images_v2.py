from __future__ import annotations

"""V2 certification policy for exact sealed Cardmarket proxy images.

Cardmarket legitimately reuses identical image bytes for some distinct source
products (for example generic case / jumbo-box artwork). Pixel-hash reuse is
therefore evidence to report, not an identity collision. Exact identity remains
bound to the source-owned ``category_id + idProduct`` URL and the reviewed 1:1
canonical mapping.
"""

from collections import defaultdict

from app.scripts import sync_sealed_cardmarket_proxy_images_v1 as v1


_base_certify_snapshot = v1.certify_snapshot


def certify_snapshot(engine, *, max_workers: int, timeout: int, retries: int):
    report = _base_certify_snapshot(
        engine,
        max_workers=max_workers,
        timeout=timeout,
        retries=retries,
    )

    recoverable = list(report.get("recoverable") or [])
    urls: dict[str, list[dict]] = defaultdict(list)
    for row in recoverable:
        url = str(row.get("source_image_url") or "")
        if url:
            urls[url].append(row)

    # URL reuse across different Cardmarket ids would be an identity/path bug
    # and remains fail-closed. Equal SHA-256 bytes at different exact URLs are
    # allowed because they are source-owned Cardmarket media reuse.
    source_url_collision_groups = {
        url: [
            {
                "variant_id": int(item["variant_id"]),
                "external_id": str(item["external_id"]),
                "product_name": str(item["product_name"]),
                "game": str(item["game"]),
            }
            for item in items
        ]
        for url, items in urls.items()
        if len({str(item.get("external_id") or "") for item in items}) > 1
    }

    failures = [
        failure
        for failure in list(report.get("failures") or [])
        if "duplicate_hash_groups" not in failure
    ]
    if source_url_collision_groups:
        failures.append({"source_url_collision_groups": len(source_url_collision_groups)})

    report["source_url_collision_groups"] = source_url_collision_groups
    report["source_url_collision_count"] = len(source_url_collision_groups)
    report["duplicate_hash_policy"] = (
        "informational_only_when_each_exact_cardmarket_source_url_is_unique"
    )
    report["image_byte_reuse_group_count"] = len(report.get("duplicate_hash_groups") or {})
    report["failures"] = failures

    if (
        report.get("control", {}).get("ok")
        and int(report.get("hard_failure_count") or 0) == 0
        and not source_url_collision_groups
        and (int(report.get("eligible") or 0) == 0 or int(report.get("recoverable_count") or 0) > 0)
        and not failures
    ):
        report["gate"] = "PASS"
    else:
        report["gate"] = "FAIL"

    return report


# Reuse V1's apply, two-pass idempotency, verification and CLI. Only the
# certification policy changes.
v1.certify_snapshot = certify_snapshot


if __name__ == "__main__":
    raise SystemExit(v1.main())
