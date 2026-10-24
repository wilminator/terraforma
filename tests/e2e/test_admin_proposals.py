"""The Quest proposals section of the admin page in a real browser: the quests the engine made for gaps in the world's progression, approved, turned
down or retired (the calls behind it are tests/test_quest_needs.py)."""

import re

from playwright.sync_api import expect

from terraforma.quests.needs_models import LIVE, PROPOSED, QuestProposal
from terraforma.quests.weekly_models import QuestTemplate

from .test_admin import admin_page, tab


def propose(live_server, *rows):
    """Proposals in the database: (band, name, status) each, for the slot rank-s."""

    async def work(db):
        for band, name, status in rows:
            template = None
            if status == LIVE:  # (a live quest is a template of its slot)
                template = QuestTemplate(
                    key=f"need-{band}",
                    name=name,
                    source="needs",
                    min_level=1,
                    max_level=999,
                    weight=1,
                    spec={"bosses": ["dragon"]},
                    active=True,
                    slot="rank-s",
                    pxp_min=100,
                    pxp_max=200,
                )
                db.add(template)
                await db.flush()
            db.add(
                QuestProposal(
                    band=band,
                    slot="rank-s",
                    pxp_min=100,
                    pxp_max=200,
                    reason="gap",
                    name=name,
                    spec={"bosses": ["dragon"]},
                    status=status,
                    open=1 if status in (PROPOSED, LIVE) else None,
                    template_id=template.id if template else None,
                )
            )

    live_server.in_db(work)


def open_section(page):
    tab(page, "Quest proposals")
    expect(page.locator("#admin-proposals")).to_be_visible()


def test_with_nothing_proposed_the_section_says_so(page, live_server):
    admin_page(page, live_server)
    open_section(page)
    expect(page.locator("#proposals-none")).to_have_text("No quests here.")


def test_a_proposal_is_approved_and_goes_live(page, live_server):
    propose(live_server, ("band-1", "Dragon of the Pass", PROPOSED))
    admin_page(page, live_server)
    open_section(page)
    row = page.locator("#proposals-list [data-proposal]")
    expect(row).to_contain_text("Dragon of the Pass")
    expect(row).to_contain_text("Slot rank-s, PXP 100 to 200")
    expect(row.locator("pre")).to_have_text('{"bosses":["dragon"]}')
    page.get_by_role("button", name="Approve Dragon of the Pass").click()
    expect(page.locator("#proposals-note")).to_have_text("Dragon of the Pass is live.")
    expect(page.locator("#proposals-none")).to_be_visible()  # it left the waiting list
    page.locator("#proposals-stage").select_option("live")
    expect(page.locator("#proposals-list [data-proposal]")).to_have_attribute(
        "data-status", "live"
    )


def test_a_proposal_is_turned_down_and_a_live_one_retired(page, live_server):
    propose(live_server, ("band-1", "Wyrm", PROPOSED), ("band-2", "Lich", LIVE))
    admin_page(page, live_server)
    open_section(page)
    expect(page.locator("#proposals-list [data-proposal]")).to_have_count(
        1
    )  # only the waiting one is shown at first
    page.get_by_role("button", name="Turn down Wyrm").click()
    expect(page.locator("#proposals-note")).to_have_text("Wyrm was turned down.")
    page.locator("#proposals-stage").select_option("live")
    page.get_by_role("button", name="Retire Lich").click()
    expect(page.locator("#proposals-note")).to_have_text("Lich is retired.")
    page.locator("#proposals-stage").select_option("all")
    expect(page.locator("#proposals-list [data-proposal]")).to_have_count(2)
    expect(page.locator("[data-proposal][data-status='rejected']")).to_contain_text(
        "Wyrm"
    )
    expect(page.locator("[data-proposal][data-status='retired']")).to_contain_text(
        "Lich"
    )
    expect(
        page.get_by_role("button", name=re.compile("Approve|Turn down|Retire"))
    ).to_have_count(0)  # nothing more to do with them


def test_the_gap_check_can_be_run_now(page, live_server):
    admin_page(page, live_server)
    open_section(page)
    page.locator("#proposals-run").click()
    expect(page.locator("#proposals-note")).to_have_text(
        re.compile(r"The check made \d+ proposals\.")
    )
