"""The fight's stage layout, in a real browser: the formation of DragonStar. Each group stands in a row with its number, the other side
stands up the stage facing the viewer, and the viewer's own side stands at the bottom, seen from behind."""

from playwright.sync_api import expect

from .test_fight import into_the_marsh


def test_the_viewers_side_stands_below_the_foe_and_each_group_is_a_numbered_row(page, live_server):
    into_the_marsh(page, live_server)
    own = page.locator(".fighter[data-side='left']").first
    foe = page.locator(".fighter[data-side='right']").first
    expect(own).to_be_visible()
    expect(foe).to_be_visible()
    assert own.bounding_box()["y"] > foe.bounding_box()["y"], "the viewer's own side is at the bottom of the stage"
    assert own.get_attribute("data-view") == "up" and foe.get_attribute("data-view") == "down"
    group = page.locator(".fight-side.left .fight-group").first
    expect(group.locator(".fight-group-tag")).to_have_text("1")
    expect(group).to_have_attribute("data-group", "0.0")
    expect(page.locator(".fight-side.left")).to_have_attribute("data-groups", "1")
    # (the stage is 480 by 270 scaled whole: no fighter is drawn outside it)
    stage = page.locator("#fight-layer").bounding_box()
    for card in page.locator(".fighter").all():
        box = card.bounding_box()
        assert stage["x"] <= box["x"] and box["x"] + box["width"] <= stage["x"] + stage["width"]
        assert stage["y"] <= box["y"] and box["y"] + box["height"] <= stage["y"] + stage["height"]


def test_the_fight_is_drawn_at_twice_the_stages_resolution_and_pictures_at_a_whole_multiple_of_their_frame(page, live_server):
    into_the_marsh(page, live_server)
    layer = page.locator("#fight-layer")
    expect(page.locator(".fighter-sprite").first).to_be_visible()
    size = layer.evaluate("(el) => [el.offsetWidth, el.offsetHeight]")
    assert size == [960, 540], "the layer has its own design resolution, twice the stage's"
    shown = layer.bounding_box()
    stage = page.locator("#stage").bounding_box()
    assert abs(shown["width"] - stage["width"]) < 1 and abs(shown["height"] - stage["height"]) < 1, "and is scaled back to fill the stage"
    # (the example's fighters are drawn on 32 pixel frames; one row of fighters on each side, so three times)
    sprite = page.locator(".fighter-sprite").first.evaluate("(el) => [el.width, el.height]")
    assert sprite == [96, 96]
