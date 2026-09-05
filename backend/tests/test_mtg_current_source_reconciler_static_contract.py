from pathlib import Path


SOURCE = Path('app/scripts/reconcile_mtg_current_source_v1.py').read_text(encoding='utf-8')


def test_reconciler_has_no_image_or_price_mutation_paths():
    assert 'PrintImage(' not in SOURCE
    assert 'Price(' not in SOURCE
    assert 'PriceSnapshot(' not in SOURCE
    assert 'session.delete(' not in SOURCE


def test_reconciler_keeps_generic_writer_quarantined_by_design():
    assert 'ScryfallMtgV2Connector().run' not in SOURCE
    assert 'get_connector("scryfall_mtg")' not in SOURCE
    assert 'generic_scryfall_writer_quarantine_relaxed": False' in SOURCE
