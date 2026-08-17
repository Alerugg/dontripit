from app.search_v2.onepiece_source import parse_onepiece_search_attributes


def _modal(print_id: str, category: str, cost_label: str, cost_value: str, block: str) -> str:
    return f'''
    <dl class="modalCol" id="{print_id}">
      <dt>
        <div class="infoCol"><span>{print_id}</span> | <span>SR</span> | <span>{category}</span></div>
        <div class="cardName">Monkey.D.Luffy</div>
      </dt>
      <dd>
        <div class="frontCol"><img data-src="/images/{print_id}.png" /></div>
        <div class="backCol">
          <div class="col2">
            <div class="cost"><h3>{cost_label}</h3>{cost_value}</div>
            <div class="attribute"><h3>Attribute</h3><img src="slash.png" alt="Slash" /></div>
            <div class="power"><h3>Power</h3>7000</div>
            <div class="counter"><h3>Counter</h3>1000</div>
            <div class="color"><h3>Color</h3>Red/Black</div>
            <div class="block"><h3>Block</h3><img alt="icon" />{block}</div>
          </div>
          <div class="feature"><h3>Type</h3>Straw Hat Crew/Supernovas</div>
          <div class="text"><h3>Effect</h3>[On Play] Draw 1 card.</div>
          <div class="trigger"><h3>Trigger</h3>[Trigger] Play this card.</div>
        </div>
      </dd>
    </dl>
    '''


def test_character_attributes_are_extracted_for_advanced_filters():
    rows = parse_onepiece_search_attributes(_modal("OP05-119_p1", "CHARACTER", "Cost", "10", "2"))
    attrs = rows["OP05-119_P1"]

    assert attrs["card_type"] == "Character"
    assert attrs["cost"] == 10
    assert attrs["life"] is None
    assert attrs["power"] == 7000
    assert attrs["counter"] == 1000
    assert attrs["colors"] == ["Red", "Black"]
    assert attrs["attributes"] == ["Slash"]
    assert attrs["block"] == "2"
    assert attrs["traits"] == ["Straw Hat Crew", "Supernovas"]
    assert "Draw 1 card" in attrs["effect"]
    assert "Play this card" in attrs["trigger"]


def test_leader_cost_slot_is_normalized_to_life():
    rows = parse_onepiece_search_attributes(_modal("OP05-001", "LEADER", "Life", "4", "X"))
    attrs = rows["OP05-001"]
    assert attrs["card_type"] == "Leader"
    assert attrs["life"] == 4
    assert attrs["cost"] is None
    assert attrs["block"] == "X"
