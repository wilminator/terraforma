"""A name that failed the name screen (``name_ok`` False) is saved and its owner always sees it, but every other player sees a neutral
label in its place: in chat, follows, directories, profile pages, relationships and alliances. Nothing is rewritten and nothing is refused.
A name nobody checked (``name_ok`` empty) is shown as before. The same on every database."""

import pytest
from sqlalchemy import update

from terraforma.alliances.models import Alliance
from terraforma.heroes.models import Team
from terraforma.models import Account
from terraforma.names.display import ALLIANCE, HERO, PLAYER, TEAM, public_name, seen_by
from terraforma.testing import in_app_db

from .helpers import expect
from .test_chat import call, game, hear, players, say  # noqa: F401 (the game and players fixtures)

pytestmark = pytest.mark.social

ZED = "Zedthe" + "Bold"  # (Zed's handle: the players fixture gives everybody "<name>theBold")
MIKE = "Mikethe" + "Bold"


def flag(client, model, *where, ok=False):
    """Marks the rows' names as having failed (or, with $ok, passed) the screen, the way ``Rules.screen_name`` did when they were saved."""
    async def work(db):
        await db.execute(update(model).where(*where).values(name_ok=ok))

    in_app_db(client, work)


def zed_handle_flagged(client):
    flag(client, Account, Account.username == "Zed")


def zeds_team_flagged(client):
    flag(client, Team, Team.name == "Strangers")


# --- the helpers --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("name_ok", "viewer", "shown"), [
    (False, 2, "Team"), (False, 1, "Rude"), (False, None, "Team"), (True, 2, "Rude"), (None, 2, "Rude"), (None, None, "Rude"),
])
def test_seen_by_hides_a_failed_name_from_everyone_but_its_owner(name_ok, viewer, shown):
    assert seen_by("Rude", name_ok, TEAM, 1, viewer) == shown


def test_seen_by_passes_a_missing_name_through_and_the_labels_differ():
    assert seen_by(None, False, TEAM, 1, 2) is None
    assert len({HERO, TEAM, ALLIANCE, PLAYER}) == 4
    assert public_name("Rude", False, ALLIANCE) == "Alliance"


# --- chat --------------------------------------------------------------------------------------------------------------------

def test_chat_names_a_flagged_sender_with_a_label_to_others_and_with_the_handle_to_the_sender(players):
    client, _ = players
    say(client, "Zed", "global", "all", "hello")
    assert [line["sender"] for line in hear(client, "Mike", "global", "all")] == [ZED]
    zed_handle_flagged(client)
    assert [(line["sender"], line["body"]) for line in hear(client, "Mike", "global", "all")] == [(PLAYER, "hello")], "the words are not hidden, only the name"
    assert [(line["sender"], line["mine"]) for line in hear(client, "Zed", "global", "all")] == [(ZED, True)]


def test_a_flagged_handle_is_a_label_in_the_mute_list_but_the_player_can_still_be_muted_by_it(players):
    client, _ = players
    zed_handle_flagged(client)
    assert call(client, "POST", "Mike", "/api/chat/mute", {"handle": ZED})["muted"] == [PLAYER]
    assert call(client, "GET", "Mike", "/api/chat/mutes")["muted"] == [PLAYER]


def test_a_whisper_channel_is_labelled_for_a_flagged_partner_and_not_for_the_partner_themselves(players):
    client, _ = players
    say(client, "Mike", "whisper", ZED, "psst")
    labels = lambda who: [each["label"] for each in call(client, "GET", who, "/api/chat/channels")["channels"] if each["kind"] == "whisper"]  # noqa: E731
    assert labels("Mike") == [ZED] and labels("Zed") == [MIKE]
    zed_handle_flagged(client)
    assert labels("Mike") == [PLAYER] and labels("Zed") == [MIKE]


# --- follows, bans and the directories ---------------------------------------------------------------------------------------

def test_follows_followers_and_bans_show_a_flagged_handle_as_a_label(players):
    client, _ = players
    call(client, "POST", "Mike", "/api/follows", {"handle": ZED})
    call(client, "POST", "Mike", "/api/bans", {"handle": ZED})
    call(client, "POST", "Zed", "/api/follows", {"handle": MIKE})
    call(client, "PUT", "Zed", "/api/follows/share-back", {"handle": MIKE, "enabled": True})
    assert [each["handle"] for each in call(client, "GET", "Mike", "/api/follows")["following"]] == [ZED]
    zed_handle_flagged(client)
    assert [each["handle"] for each in call(client, "GET", "Mike", "/api/follows")["following"]] == [PLAYER]
    assert call(client, "GET", "Mike", "/api/bans") == {"banned": [PLAYER]}
    assert [each["handle"] for each in call(client, "GET", "Zed", "/api/follows")["following"]] == [MIKE], "Zed sees what they follow as always"
    flag(client, Account, Account.username == "Mike")  # now Mike's handle fails, and Mike's own list of Zed still reads as before
    assert [each["handle"] for each in call(client, "GET", "Zed", "/api/follows")["following"]] == [PLAYER]
    assert [each["handle"] for each in call(client, "GET", "Mike", "/api/followers")["sharing"]] == []


def test_the_directory_finds_a_flagged_handle_for_nobody_but_its_owner(players):
    client, _ = players
    call(client, "PUT", "Zed", "/api/profile/directory", {"enabled": True})
    assert [each["handle"] for each in call(client, "GET", "Mike", "/api/directory?prefix=zed")["players"]] == [ZED]
    zed_handle_flagged(client)
    assert call(client, "GET", "Mike", "/api/directory?prefix=zed")["players"] == [], "a hidden name is not found by typing its start either"


def test_the_team_directory_finds_a_flagged_team_for_nobody_but_its_owner(players):
    client, ids = players
    call(client, "PUT", "Zed", "/api/profile/team-visible", {"team_id": ids["Strangers"], "visible": True})
    assert [each["name"] for each in call(client, "GET", "Mike", "/api/team-directory?prefix=str")["teams"]] == ["Strangers"]
    zeds_team_flagged(client)
    assert call(client, "GET", "Mike", "/api/team-directory?prefix=str")["teams"] == []
    mine = call(client, "GET", "Zed", "/api/team-directory?prefix=str")["teams"]
    assert [(each["name"], each["mine"]) for each in mine] == [("Strangers", True)], "its owner still finds it"


# --- profile pages -----------------------------------------------------------------------------------------------------------

def public(client, url, who=None):
    """A page read by $who, or (with nobody) by no login at all."""
    if who is None:
        client.cookies.clear()
        client.chat_user = None  # (the chat tests' login helper signs in again next time)
        return expect(client.get(url), 200).json()
    return call(client, "GET", who, url)


def test_a_players_page_shows_a_flagged_handle_and_team_name_as_labels_to_others(players):
    client, ids = players
    token = call(client, "POST", "Zed", "/api/profile/token")["token"]
    call(client, "PUT", "Zed", "/api/profile/team-visible", {"team_id": ids["Strangers"], "visible": True})
    assert public(client, f"/api/p/{token}", "Mike")["handle"] == ZED
    zed_handle_flagged(client)
    zeds_team_flagged(client)
    page = public(client, f"/api/p/{token}", "Mike")
    assert (page["handle"], [team["name"] for team in page["teams"]]) == (PLAYER, [TEAM])
    page = public(client, f"/api/p/{token}", "Zed")
    assert (page["handle"], [team["name"] for team in page["teams"]]) == (ZED, ["Strangers"]), "the owner reads their own page as it is"
    page = public(client, f"/api/p/{token}")
    assert (page["handle"], [team["name"] for team in page["teams"]]) == (PLAYER, [TEAM]), "a visitor with no login is another player too"


def test_a_team_page_names_a_flagged_team_a_flagged_alliance_and_the_directory_handle_by_labels(players):
    client, ids = players
    call(client, "POST", "Zed", "/api/profile/token")
    call(client, "PUT", "Zed", "/api/profile/directory", {"enabled": True})
    call(client, "PUT", "Zed", "/api/profile/team-visible", {"team_id": ids["Strangers"], "visible": True})
    token = next(team["token"] for team in call(client, "GET", "Zed", "/api/profile")["teams"] if team["name"] == "Strangers")
    assert public(client, f"/api/p/team/{token}", "Yan") == {"name": "Strangers", "handle": ZED, "alliances": [{"name": "Iron Pact", "page": None}]}
    zeds_team_flagged(client)
    zed_handle_flagged(client)
    flag(client, Alliance, Alliance.name == "Iron Pact")
    assert public(client, f"/api/p/team/{token}", "Yan") == {"name": TEAM, "handle": PLAYER, "alliances": [{"name": ALLIANCE, "page": None}]}
    own = public(client, f"/api/p/team/{token}", "Zed")
    assert (own["name"], own["handle"], own["alliances"]) == ("Strangers", ZED, [{"name": "Iron Pact", "page": None}]), "its owner and a member of the alliance read the real names"


def test_the_public_alliance_page_and_the_members_roster(players):
    client, ids = players
    call(client, "POST", "Mike", f"/api/alliances/{ids['alliance']}/profile/token")
    token = call(client, "GET", "Mike", f"/api/alliances/{ids['alliance']}/profile")["token"]
    zeds_team_flagged(client)
    page = public(client, f"/api/p/alliance/{token}")
    assert page["name"] == "Iron Pact" and [team["name"] for team in page["teams"]] == ["Vanguard", TEAM]
    flag(client, Alliance, Alliance.name == "Iron Pact")
    assert public(client, f"/api/p/alliance/{token}")["name"] == ALLIANCE, "a stranger on the public page sees a label for the alliance"
    roster = lambda who: [team["name"] for team in call(client, "GET", who, f"/api/alliances/{ids['alliance']}/roster")]  # noqa: E731
    assert roster("Mike") == ["Vanguard", TEAM], "its members read the roster, where another player's flagged team is a label"
    assert roster("Zed") == ["Vanguard", "Strangers"], "and the flagged team's own player reads it as it is"


# --- relationships and alliances ----------------------------------------------------------------------------------------------

def test_a_teams_relationships_name_another_players_flagged_team_and_alliance_by_labels(players):
    client, ids = players
    team = ids["Vanguard"]
    call(client, "POST", "Mike", f"/api/teams/{team}/relationships/set", {"kind": "team", "id": ids["Loner"], "score": 40})
    call(client, "POST", "Mike", f"/api/teams/{team}/relationships/set", {"kind": "team", "id": ids["Strangers"], "score": 20})
    names = lambda: sorted(each["name"] for each in call(client, "GET", "Mike", f"/api/teams/{team}/relationships")["relationships"])  # noqa: E731
    assert names() == ["Loner", "Strangers"]
    flag(client, Team, Team.name == "Strangers")
    assert names() == ["Loner", TEAM]
    flag(client, Team, Team.name == "Strangers", ok=True)
    flag(client, Team, Team.name == "Strangers", ok=None)
    assert names() == ["Loner", "Strangers"], "a name that was never checked is shown"
    answer = call(client, "POST", "Mike", f"/api/teams/{team}/relationships/set", {"kind": "team", "id": ids["Loner"], "note": "hi"})
    assert answer["name"] == "Loner"


def test_an_alliance_names_the_other_players_flagged_team_by_a_label_and_the_owner_by_the_name(players):
    client, ids = players
    zeds_team_flagged(client)
    view = lambda who: call(client, "GET", who, f"/api/alliances/{ids['alliance']}")  # noqa: E731
    assert [member["team"] for member in view("Mike")["members"]] == ["Vanguard", TEAM]
    assert [member["team"] for member in view("Zed")["members"]] == ["Vanguard", "Strangers"]
    mine = call(client, "GET", "Zed", "/api/alliances")
    assert [row["team"] for row in mine["alliances"]] == ["Strangers"]


def test_an_alliance_that_failed_the_screen_keeps_its_name_for_its_members_and_labels_it_for_an_invited_team(players):
    client, ids = players
    flag(client, Alliance, Alliance.name == "Iron Pact")
    assert view_name(client, "Mike", ids) == "Iron Pact" and view_name(client, "Zed", ids) == "Iron Pact", "members share it"
    call(client, "POST", "Mike", f"/api/alliances/{ids['alliance']}/invite", {"team_id": ids["Vanguard"], "target_team_id": ids["Loner"]})
    invitation = call(client, "GET", "Yan", "/api/alliances")["invitations"]
    assert [row["alliance"] for row in invitation] == [ALLIANCE], "the invited player is not a member yet"
    call(client, "POST", "Yan", f"/api/teams/{ids['Loner']}/invitations/accept", {"alliance_id": ids["alliance"]})
    assert view_name(client, "Yan", ids) == "Iron Pact", "they see the name once they are in"


def view_name(client, who, ids):
    return call(client, "GET", who, f"/api/alliances/{ids['alliance']}")["name"]
