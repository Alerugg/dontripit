# Yu-Gi-Oh! Catalog V2 — FINAL CERTIFIED

**Certification date:** 2026-08-08  
**Branch:** `catalog-v2`  
**Status:** **FINAL CERTIFIED**  
**Primary catalog source:** YGOPRODeck API v7  
**Certified surface:** source-backed English Yu-Gi-Oh! catalog identity, physical Print evidence, Search V2, filters, HTTP/frontend behavior and responsive visual experience.

## Certification principle

This certification follows the project rule:

> **Do not advance because it works; advance when the data can be trusted.**

`FINAL CERTIFIED` does **not** mean that the product claims information that the upstream source cannot prove. It means that every field and relationship exposed as canonical inside the certified surface has a defined identity rule, passed the relevant consistency gates, and is not silently inferred beyond the available evidence.

---

## 1. Canonical identity model

Yu-Gi-Oh! V2 deliberately separates collector-number identity from commercial release provenance.

- **Game** → `yugioh`
- **Set** → collector-number family, for example `LOB`, `MRD`, `RA01`, `DB1`
- **Card** → logical card identity backed by YGOPRODeck card ID
- **Print** → exact source-backed physical printing identity using the full printing code plus the certified variant/rarity identity
- **CatalogRelease** → source-defined commercial release/product/program
- **PrintRelease** → provenance relation linking an exact Print to the release in which it appears

This avoids the legacy mistake where commercial releases and collector-number families were collapsed into the same `Set` concept.

### Legacy source-code exception

Twelve YGOPRODeck source rows inside **Dark Beginning 1** expose malformed/no-hyphen codes such as `DB49`/`DB46` instead of a normal collector code. The certification audit showed that the same release contains 250 well-formed rows and those rows unanimously use the `DB1` family.

Therefore:

- those 12 rows use the evidence-backed `DB1` family fallback;
- their raw source code is preserved;
- the system does not pretend the malformed source value is a corrected collector number;
- this behavior is explicitly marked as `same_release_unanimous_fallback`.

Certified fallback count: **12**.

---

## 2. Canonical counts

Final post-index Catalog Health gate:

| Entity | Certified count |
|---|---:|
| Sets / collector families | **646** |
| Cards | **14,479** |
| Exact Prints | **44,226** |
| Catalog Releases | **1,032** |
| Print ↔ Release relations | **44,226** |
| Card attribute records | **14,479** |
| Print attribute records | **44,226** |
| Print images | **44,226** |
| Cards without source physical Print evidence | **490** |

Every certified Print has an image and exactly one certified release relation on this source surface.

---

## 3. Identity integrity

Final post-index identity checks:

| Gate | Result |
|---|---:|
| Duplicate Set codes | **0** |
| Duplicate Card external IDs | **0** |
| Duplicate Print external IDs | **0** |
| Duplicate `print_key` identities | **0** |
| Duplicate shared physical Print tuples | **0** |
| Prints without image | **0** |
| Prints without release | **0** |
| Prints with multiple certified release assignments | **0** |
| Search Card profiles missing | **0** |
| Search Print profiles missing | **0** |
| Search profiles attached to wrong game | **0** |
| Quarantined disputed assignments admitted | **0** |

The known source-conflict assignments remain quarantined rather than being forced into the canonical catalog.

---

## 4. Source-honest limitations

The following are intentional limitations of the certified YGOPRODeck surface, not hidden failures:

### 490 Cards without physical Print evidence

YGOPRODeck exposes **490 logical Cards** for which the current source payload has no `card_sets` physical printing evidence. They remain valid Cards but the system does **not** manufacture a fake Print for them.

### 206 unknown/noisy rarity Prints

The upstream source contains non-rarity/noisy labels such as release-status text. These rows are preserved as source evidence while their canonical rarity is **Unknown** rather than being falsely normalized into a real rarity.

Certified `Unknown` rarity Prints: **206**.

### Artwork

The source exposes artwork candidate IDs, but it does not provide sufficiently reliable evidence to map every exact Print to one exact artwork variant.

Therefore:

- artwork candidates may be preserved as Card-level evidence;
- **exact Print ↔ artwork mapping is not claimed**;
- artwork is not yet a certified exact-Print filter.

### Finish and edition

The current source surface does not justify reliable exact-print `finish` or `edition` claims across the whole catalog.

Therefore the certification explicitly records:

- `finish_claimed = false`
- `edition_claimed = false`
- `exact_print_art_mapping_claimed = false`

These dimensions can only be enabled later when a source-backed mapping passes its own certification gate.

### Language

The current certified physical surface is English. Certified non-English Prints in this surface: **0**.

---

## 5. Search V2 certification

Final Search V2 state:

| Search entity | Count |
|---|---:|
| Card search profiles | **14,479** |
| Print search profiles | **44,226** |
| Facet definitions | **20** |
| Active facets | **19** |
| Legacy `search_documents` for YGO | **0** |

The Search V2 projection exactly covers every canonical Card and every canonical Print.

### Strict benchmark after storage maintenance

Final read-only benchmark passed against the compacted live Neon database.

- Dark Magician normal search: **805.13 ms**
- Blue-Eyes White Dragon normal search: **846.09 ms**
- exact collector `2017-EN001`: **238.45 ms**
- Monster + DARK advanced filter: **540.99 ms**
- ATK ≥ 3000: **544.76 ms**
- Extra Secret Rare: **203.63 ms**
- exact collector advanced filter: **174.48 ms**
- release filter: **229.12 ms**
- maximum observed: **846.09 ms**

Certification ceilings:

- normal search: **1,500 ms**
- advanced search: **1,800 ms**

Unsupported `finish=holo` was correctly rejected rather than producing an unverified result.

---

## 6. HTTP, frontend and visual QA

The final HTTP/frontend certification passed after the Search V2 index compaction.

It includes:

- strict Search V2 benchmark;
- Search V2 HTTP contract smoke;
- frontend contract tests;
- production Next.js build.

The final Chromium QA also passed after compaction.

Validated visually and interactively:

- desktop hero and certified counters;
- full Advanced Search panel;
- six Yu-Gi-Oh! quick-filter controls;
- normal `Dark Magician` search;
- `Monster + DARK` returning Exact Prints;
- source-backed identity badges;
- mobile viewport without horizontal overflow;
- Search V2 BFF responses without HTTP errors;
- no browser page errors or fatal console errors.

External/resource load errors are recorded as diagnostic evidence and are not allowed to mask Search/API/browser failures.

---

## 7. Storage correction and final headroom

A later unnecessary Search V2 rebuild was triggered after query-only changes because the workflow path selector was initially too broad.

That rebuild did **not** expose a catalog correctness problem. It aborted on the internal storage safety gate when its in-transaction database peak reached **481.07 MiB**, above the project's **480 MiB precommit safety ceiling**.

Important findings:

1. The previously certified Search V2 profiles were still the current profiles; the profile generator and facet generator had not changed.
2. The workflow trigger was narrowed so query/UI changes no longer cause a full profile rebuild.
3. The stable database size was **464.34 MiB**, not the transient 481.07 MiB transaction peak.
4. The excess stable growth was concentrated in reusable/bloated trigram indexes.

### Safe index compaction

Only these three Search V2 trigram indexes were rebuilt concurrently:

- `ix_print_search_profiles_text_trgm`
- `ix_print_search_profiles_name_trgm`
- `ix_card_search_profiles_name_trgm`

No canonical Card, Print, Set, Release, attribute or search-profile rows were modified.

| Metric | Before | After |
|---|---:|---:|
| Neon database | **464.34 MiB** | **440.41 MiB** |
| Print text trigram index | 31.29 MiB | **18.30 MiB** |
| Print name trigram index | 10.78 MiB | **4.29 MiB** |
| Card name trigram index | 6.57 MiB | **2.18 MiB** |
| Space recovered |  | **23.93 MiB** |

All three indexes ended `valid=true` and `ready=true`.

Final post-index Health measurement: **440.43 MiB / 512 MiB**, leaving **71.57 MiB** of physical database headroom.

Row counts before and after compaction were identical:

- YGO Card profiles: **14,479 → 14,479**
- YGO Print profiles: **44,226 → 44,226**
- YGO facets: **20 → 20**
- Pokémon Card profiles: **21,065 → 21,065**
- Pokémon Print profiles: **33,757 → 33,757**

---

## 8. Audit lifecycle correction

The original post-canonical Catalog Health script was intentionally written for the **pre-index** stage and asserted that YGO Search V2 was still empty.

Re-running that historical gate after Search V2 had already been certified correctly failed on this obsolete lifecycle assumption:

`card_search_profiles=14479`, `print_search_profiles=44226`, `facet_definitions=20`.

This was not treated as a catalog regression. A separate final-state gate was added:

`audit_yugioh_catalog_health_postindex_v2.py`

It requires the post-index state to be **exactly** certified rather than merely non-empty. Partial indexing, missing profiles, wrong-game profiles, duplicate identities or broken Print linkage all fail the gate.

This preserves both useful contracts:

- pre-index health gate → canonical data is ready before indexing;
- post-index health gate → canonical data + Search V2 are both complete after indexing.

---

## 9. Final evidence

### Canonical apply

- Workflow: `apply-yugioh-v2-canonical.yml`
- Run: **31266269641**
- Commit: `d2d8e336c1bd8a28b9d944af37ece06bc969b248`
- Result: **PASS**

### Pre-index canonical Catalog Health

- Workflow: `audit-yugioh-catalog-health-v2.yml`
- Original successful run: **31266477262**, attempt 1
- Commit: `b893568332e356b13eb67c61da1f0e75c716282c`
- Result: **PASS**

### Search V2 build + original benchmark

- Workflow: `rebuild-yugioh-search-v2.yml`
- Run: **31267927315**
- Commit: `90778875bd4d4f95a04f9ddddf41c23cfa18d276`
- Result: **PASS**

### Storage compaction

- Workflow: `compact-search-v2-trgm-indexes.yml`
- Run: **31269467106**
- Commit: `b1fc3efa638f216b9e9e65e985c9dad3c08ec936`
- Result: **PASS**

### Final benchmark + HTTP + frontend

- Workflow: `certify-yugioh-frontend-v2.yml`
- Run: **31269609154**
- Commit: `d5b02b92ef6f5686850c05403762b9812b65d07a`
- Result: **PASS**

### Final Chromium visual QA

- Workflow: `visual-qa-yugioh-search-v2.yml`
- Run: **31268647609**, attempt 2
- Result: **PASS**

### Final post-index Catalog Health

- Workflow: `audit-yugioh-catalog-health-postindex-v2.yml`
- Run: **31269776339**
- Commit: `73e20ad7c5384fdc300a3911f3e537bc3b8e1c16`
- Result: **PASS**

---

## 10. Certification decision

**Yu-Gi-Oh! Catalog V2 is FINAL CERTIFIED for the currently defined YGOPRODeck-backed English catalog surface.**

The certification covers canonical identity, source-backed physical Prints, release provenance, images, attributes, Search V2, game-specific filters, HTTP/frontend contracts, responsive Chromium QA and operational database headroom.

It explicitly does **not** certify unsupported exact artwork mapping, finish, edition or non-English physical-print completeness.

## 11. Conditions that invalidate certification

Yu-Gi-Oh! must return to certification review if any of the following changes:

- canonical identity algorithm;
- Set-family derivation;
- Print identity tuple;
- source reconciliation/quarantine policy;
- YGOPRODeck source shape or materially changed counts;
- exact artwork/finish/edition model;
- canonical Card/Print counts;
- Search V2 profile generator;
- facet contract;
- database schema affecting canonical identity;
- a new source is allowed to override current canonical fields.

Until one of those changes, routine query/UI improvements should use read-only benchmark/HTTP/visual gates and should **not** trigger a full Search V2 rebuild unnecessarily.
