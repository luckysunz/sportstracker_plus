"""Builders for Genius hosted-widget fragments used in tests.

The markup mirrors a real capture of
https://hosted.dcd.shared.geniussports.com/embednf/WHBA/en/competition/49807/{schedule,standings}
taken on 2026-10-27 (team ids, names, logo URLs, the finished game
Spinto Münsterland 62:65 RSV Lahn-Dill and the scheduled game
Spinto Münsterland - Rhine River Rhinos are real). Pairings involving
RB Zwickau and the live/cancelled variants are synthetic.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

IMG = "https://images.statsengine.playbyplay.api.geniussports.com/"
LINK = "https://hosted.dcd.shared.geniussports.com/WHBA/en/competition/49807/team/%s?"

# id -> (name, logo file)
TEAMS: Dict[str, Tuple[str, str]] = {
    "36362": ("RSB Thuringia Bulls", "5eb2722a1622f0782cb42c94091b252fT1.png"),
    "36916": ("Hannover United", "36545eac363d4ba23dccd6e0ab3f43caT1.jpg"),
    "36369": ("Rhine River Rhinos", "6bb8ec690a1a6111c0a94f272503a38bT1.jpg"),
    "36370": ("RSV Lahn-Dill", "3fa7010b931bbbd7973a679aaac3d2c7T1.png"),
    "36911": ("Spinto Münsterland", "f2c82921dda6156e3ced950876ce3c13T1.png"),
    "36368": ("RBC Köln 99ers", "89a8ec95e70cba04e0f3a039b5b405efT1.png"),
    "36365": ("RB Zwickau e.V.", "1c51a24da959fc0c01ee131dedfdb3ceT1.png"),
    "36366": ("Doneck Dolphins Trier", "24f06bfa035be90d19e74bef68f5469aT1.jpg"),
}


def _team_block(side: str, team_id: str, score: Optional[int]) -> str:
    name, logo = TEAMS[team_id]
    score_text = "&nbsp;" if score is None else str(score)
    return (
        '<div class="%(s)s-team"><div class="%(s)s-team-logo team-logo"><a href="%(link)s">'
        '<img src="%(img)s%(logo)s" alt="%(name)s"></a></div><div class="team-name">'
        '<a href="%(link)s" class="teamnames"><span class="team-name-full">%(name)s</span>'
        '<span class="team-name-code"></span></a></div><div class="team-score %(s)sscore">'
        '<div class="fake-cell">%(score)s</div></div></div>'
    ) % {"s": side, "link": LINK % team_id, "img": IMG, "logo": logo, "name": name, "score": score_text}


def match_html(
    match_id: str,
    status_class: str,
    time_text: str,
    home: str,
    away: str,
    home_score: Optional[int] = None,
    away_score: Optional[int] = None,
    notlive_extra: str = "",
) -> str:
    if status_class == "STATUS_COMPLETE":
        notlive = '<span class="complete matchStatus"> Final </span>'
        link = "boxscore"
    elif status_class == "STATUS_SCHEDULED":
        notlive = '<span class="upcoming matchStatus"><span class="notlive-match-time">%s</span></span>' % time_text
        link = "preview"
    else:
        notlive = notlive_extra or '<span class="upcoming matchStatus"></span>'
        link = "preview"
    return (
        '<div class="match-wrap %(status)s" id="extfix_%(id)s"><div class="match-details-wrap">'
        '<div class="match-details"><div class="match-time"><h6>Date / Time: </h6><span>%(time)s</span></div>'
        '<div class="match-venue"><h6>Venue: </h6><span class="venuename"></span></div></div></div>'
        '<div class="sched-teams">%(home)s<div class="team-divide"><div class="match-result">'
        '<div class="livenow livedetails" style=""><span class="matchStatus">Live now</span>'
        '<span class="periodBreak">HALF-TIME</span><span class="liveperiodlabel">Period</span>'
        '<span class="liveperiod"></span><span class="liveclock"></span></div>'
        '<div class="notlive">%(notlive)s</div></div></div>%(away)s</div>'
        '<div class="sched-link"><div class="match-center-link"><a href="https://hosted.dcd.shared.geniussports.com/WHBA/en/competition/49807/match/%(id)s/%(link)s?" class="detail-r-link livenow">'
        '<span class="livenow matchLink"> Live now </span></a></div></div></div>'
    ) % {
        "status": status_class,
        "id": match_id,
        "time": time_text,
        "home": _team_block("home", home, home_score),
        "away": _team_block("away", away, away_score),
        "notlive": notlive,
        "link": link,
    }


def schedule_payload(matches: Iterable[str], live_ids: str = "") -> dict:
    body = "".join(matches)
    html = (
        "<style>.x{}</style>"
        '<div class="_user_663 _comp_49807 ltr hs-embed"><div id="hs-container" class="hs-page hs-container">'
        '<div class="hs-container noresize main-container"><div class="hs-row"><div class="hs-col-full">'
        '<div class="schedule-wrap template-wrap"><div class="fixture-wrap">%s</div></div></div></div></div></div></div>'
        '<input type="hidden" id="_livematches" value="%s">'
        '<input type="hidden" id="competitionId" value="49807">'
        '<input type="hidden" id="sport" value="basketball">'
    ) % (body, live_ids)
    return {"css": ["https://hosted.dcd.shared.geniussports.com/css/hs-embed-style.css"], "js": [], "html": html}


# (rank, team id, GP, W, L, Pts, GD, last5)  - first 8 rows as captured 2026-10-27
STANDINGS: List[Tuple[int, str, int, int, int, int, int, str]] = [
    (1, "36362", 2, 2, 0, 4, 89, "WW"),
    (2, "36916", 1, 1, 0, 2, 11, "W"),
    (3, "36369", 2, 1, 1, 2, -3, "LW"),
    (4, "36370", 2, 1, 1, 2, -5, "WL"),
    (5, "36911", 2, 1, 1, 2, 13, "LW"),
    (6, "36368", 0, 0, 0, 0, 0, ""),
    (7, "36365", 1, 0, 1, 0, -16, "L"),
    (8, "36366", 1, 0, 1, 0, -32, "L"),
]


def standings_payload() -> dict:
    rows = []
    for rank, team_id, gp, w, l, pts, gd, last5 in STANDINGS:
        name, logo = TEAMS[team_id]
        form = " ".join('<span class="%s">%s</span>' % (c, c) for c in last5)
        rows.append(
            '<tr class="standings_team_%(id)s"><td>%(rank)d</td><td class="team-logo"><a href="%(link)s">'
            '<img src="%(img)s%(logo)s" alt="%(name)s"></a></td><td class="team-name"><a href="%(link)s">'
            '<span class="team-name-full">%(name)s</span><span class="team-name-code"></span>'
            '<span class="liveStandingsIndicator"></span></a></td><td class="STANDINGS_played">%(gp)d</td>'
            '<td class="STANDINGS_won">%(w)d</td><td class="STANDINGS_lost">%(l)d</td>'
            '<td class="STANDINGS_standingPoints">%(pts)d</td><td class="STANDINGS_pointsDiff">%(gd)d</td>'
            '<td class="STANDINGS_last5 split"> %(form)s </td></tr>'
            % {"id": team_id, "rank": rank, "link": LINK % team_id, "img": IMG, "logo": logo, "name": name,
               "gp": gp, "w": w, "l": l, "pts": pts, "gd": gd, "form": form}
        )
    header = (
        '<thead><tr><th data-type="number">Position</th><th class="team-logo"></th>'
        '<th class="team-name">Team</th><th class="STANDINGS_played">GP</th><th class="STANDINGS_won">W</th>'
        '<th class="STANDINGS_lost">L</th><th class="STANDINGS_standingPoints">Pts</th>'
        '<th class="STANDINGS_pointsDiff">GD</th><th class="STANDINGS_last5">L5</th></tr></thead>'
    )
    html = '<style>.y{}</style><div class="standings-wrap"><table class="tableClass standings">%s<tbody>%s</tbody></table></div>' % (
        header, "".join(rows)
    )
    return {"css": [], "js": [], "html": html}
