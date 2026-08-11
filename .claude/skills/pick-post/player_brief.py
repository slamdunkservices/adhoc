#!/usr/bin/env python3
"""
player_brief.py — every caption-ready number for one player, in one call.

find_plays.py answers "what's the play and what's the price". This answers the
other half: "what is true about this player that a caption can lead with".
SKILL.md rule 1 says the caption is about the player, not the bet, and rule 6
says every stat traces to a file — so this reads those files once and hands back
a block that is already checkable.

    python3 player_brief.py --player "Javier Báez"
    python3 player_brief.py --player "Chelsea Gray" --json
    python3 player_brief.py --player "Kiah Stokes" --date 2026-08-04

READ-ONLY. Opens data files and writes nothing, anywhere.

The `caveats` list is the part that earns its keep. Season averages flatter
players who have been hurt, model projections lean on career profile when
recent form is thin, and a rate stat computed over a whole roster will hand you
a "team best" that a 2-for-2 bench player actually leads. Each of those has
produced a caption that had to be corrected. They are detected here so they
cannot be missed:

  * projected lineup    — the alert exists but the lineup is a projection
  * career_vs_window    — the projection is riding career profile, not form
  * part_time           — season rates are over a fraction of the team's games
  * layoff              — a gap in appearances that season totals hide
  * volume-guarded      — a superlative that a low-volume teammate beats

Data roots come from ~/.<league>_jobs.env via find_plays.data_root(). Those
files also hold webhooks and tokens — only *_DATA_ROOT is ever read out, and
nothing is echoed.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse rather than restate: same data-root resolution, same name folding, same
# odds formatting the alert feed uses. See find_plays.py.
from find_plays import ET, data_root, fold, fmt_american, prob_to_american  # noqa: E402

JERSEYS = os.path.join(HERE, "jerseys.json")

# A layoff worth mentioning. Below this it's ordinary rest or a short IL stint
# that the season line already represents fairly.
LAYOFF_DAYS = 10

# Recent-window power below this fraction of career means the projection is
# leaning on who the player has been, not who they are right now.
WINDOW_DIVERGENCE = 0.70

# Below this share of the team's games, season rates describe a part-time role.
PART_TIME_SHARE = 0.60

# Minimum attempts before a rate stat may be compared across a roster. A
# team-best free-throw percentage is meaningless at 2 attempts, and quoting it
# as one is how a caption ends up false. Tuned to WNBA season volumes.
VOLUME_FLOORS = {"FG_PCT": 100, "FG3_PCT": 50, "FT_PCT": 20}


def die(msg):
    sys.exit("error: " + msg)


def r3(v):
    """Round for display, passing None through untouched."""
    return None if v is None else round(float(v), 3)


def load_jerseys():
    try:
        with open(JERSEYS) as fh:
            return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    except Exception:
        return {}


# ---------------------------------------------------------------- MLB

def mlb_brief(root, name, date):
    import pandas as pd

    players = pd.read_csv(os.path.join(root, "02_curated", "players", "current.csv"))
    hit = players[players["full_name"].map(fold).str.contains(fold(name), regex=False)]
    if hit.empty:
        return None
    if len(hit) > 1:
        exact = hit[hit["full_name"].map(fold) == fold(name)]
        if len(exact) != 1:
            die("'%s' matches %d MLB players: %s — be more specific"
                % (name, len(hit), ", ".join(hit["full_name"])))
        hit = exact
    p = hit.iloc[0]
    pid = int(p["player_id"])

    season = int(date[:4])
    pa_path = os.path.join(root, "02_curated", "core", "plate_appearances",
                           "%d.parquet" % season)
    if not os.path.exists(pa_path):
        die("no plate appearances for %d at %s" % (season, pa_path))

    cols = ["game_pk", "game_date", "batter", "bat_team", "events", "is_hr", "is_hit",
            "is_ab", "total_bases", "launch_speed", "is_barrel", "in_play", "p_throws"]
    pa = pd.read_parquet(pa_path, columns=cols)
    b = pa[pa["batter"] == pid].copy()
    if b.empty:
        return {"league": "MLB", "player": p["full_name"], "identity": _mlb_identity(p, pid),
                "season": None, "caveats": [{"code": "no_appearances",
                "detail": "no %d plate appearances on file" % season}]}
    b["game_date"] = pd.to_datetime(b["game_date"])
    b = b.sort_values("game_date")

    ab, h, tb = b["is_ab"].sum(), b["is_hit"].sum(), b["total_bases"].sum()
    hr, dbl = int(b["is_hr"].sum()), int((b["events"] == "double").sum())
    avg, slg = (h / ab if ab else None), (tb / ab if ab else None)
    season_line = {
        "G": int(b["game_pk"].nunique()), "PA": int(len(b)), "AB": int(ab),
        "H": int(h), "2B": dbl, "3B": int((b["events"] == "triple").sum()), "HR": hr,
        "BB": int((b["events"] == "walk").sum()), "K": int((b["events"] == "strikeout").sum()),
        "AVG": r3(avg), "SLG": r3(slg), "ISO": r3(slg - avg if avg is not None else None),
    }

    # Balls in play, not "every tracked swing". Fouls carry a launch_speed too,
    # and they are weak contact by construction — counting them pulls average
    # exit velocity down by more than a mph and inflates the denominator under
    # barrel rate. `in_play` is the column that means what the caption means.
    bip = b[b["in_play"] == True] if "in_play" in b.columns else b  # noqa: E712
    ev = bip[bip["launch_speed"].notna()]
    batted = {
        "BIP": int(len(bip)),
        "avg_exit_velo": r3(ev["launch_speed"].mean()) if len(ev) else None,
        "max_exit_velo": r3(ev["launch_speed"].max()) if len(ev) else None,
        "barrels": int(bip["is_barrel"].sum()) if len(bip) else 0,
        "barrel_rate": r3(bip["is_barrel"].mean()) if len(bip) else None,
    }

    per_game = b.groupby(b["game_date"].dt.date).agg(
        PA=("game_pk", "size"), AB=("is_ab", "sum"), H=("is_hit", "sum"),
        HR=("is_hr", "sum"), TB=("total_bases", "sum"))
    last10 = [dict(date=str(d), **{k: int(v) for k, v in row.items()})
              for d, row in per_game.tail(10).iterrows()]

    # A gap in appearances that the season line silently averages over.
    dates = sorted(per_game.index)
    layoff = None
    for prev, nxt in zip(dates, dates[1:]):
        gap = (nxt - prev).days
        if gap >= LAYOFF_DAYS and (layoff is None or gap >= layoff["days"]):
            layoff = {"days": gap, "last_before": str(prev), "returned": str(nxt)}
    if layoff:
        since = b[b["game_date"] >= pd.Timestamp(layoff["returned"])]
        s_ab, s_h = since["is_ab"].sum(), since["is_hit"].sum()
        layoff["since"] = {
            "G": int(since["game_pk"].nunique()), "AB": int(s_ab), "H": int(s_h),
            "HR": int(since["is_hr"].sum()),
            "AVG": r3(s_h / s_ab) if s_ab else None,
            "SLG": r3(since["total_bases"].sum() / s_ab) if s_ab else None,
        }

    profile = _mlb_profile(root, pid)
    tonight = _mlb_tonight(root, pid, date, players)

    # `bat_team` is an abbreviation ('DET') and players/current.csv carries the
    # full club name, so they never join. Take the abbreviation off the player's
    # own rows instead — that is the club they actually batted for, which is
    # also the one that survives a mid-season trade.
    abbr = b["bat_team"].mode()
    team_games = int(pa[pa["bat_team"] == abbr.iloc[0]]["game_pk"].nunique()) if len(abbr) else 0

    out = {
        "league": "MLB", "player": p["full_name"], "date": date,
        "identity": _mlb_identity(p, pid),
        "season": season_line, "batted_ball": batted,
        "recent": {"last10": last10, "layoff": layoff},
        "profile": profile, "tonight": tonight,
        "team_games_played": int(team_games),
    }
    out["caveats"] = _mlb_caveats(out)
    return out


def _mlb_identity(p, pid):
    jersey = load_jerseys().get(str(pid))
    return {
        "player_id": pid, "team": p.get("team_name", ""), "position": p.get("position", ""),
        "bats": p.get("bats", ""), "jersey": jersey,
        "jersey_note": None if jersey else
        "no jersey on file — MLB curated data has no jersey column. Look it up, "
        "then add it to jerseys.json so it is never looked up again.",
    }


def _mlb_profile(root, pid):
    """Career vs recent-window power, and the platoon split, for one batter."""
    import pandas as pd
    path = os.path.join(root, "02_curated", "profiles", "batter_profiles.parquet")
    if not os.path.exists(path):
        return None
    # 460k+ rows; push the batter filter into the parquet reader rather than
    # loading the league and subsetting after.
    try:
        df = pd.read_parquet(path, filters=[("batter", "==", pid)])
    except Exception:
        df = pd.read_parquet(path)
        df = df[df["batter"] == pid]
    if df.empty:
        return None
    r = df.sort_values("game_date").iloc[-1]

    def g(k):
        return r3(r[k]) if k in r.index and pd.notna(r[k]) else None

    return {
        "as_of": str(r.get("game_date", "")),
        "career": {"hr_per_pa": g("hr_per_pa_career"), "barrel_rate": g("barrel_rate_career"),
                   "hard_hit_rate": g("hard_hit_rate_career"), "iso": g("iso_career"),
                   "k_rate": g("k_rate_career"), "exit_velo": g("exit_velo_career")},
        "window": {"hr_per_pa": g("hr_per_pa_w"), "barrel_rate": g("barrel_rate_w"),
                   "hard_hit_rate": g("hard_hit_rate_w"), "iso": g("iso_w"),
                   "max_exit_velo": g("max_ev_w"), "p90_exit_velo": g("p90_ev_w")},
        "platoon": {"iso_vs_l": g("iso_vs_l"), "iso_vs_r": g("iso_vs_r"),
                    "hr_per_pa_vs_l": g("hr_per_pa_vs_l"), "hr_per_pa_vs_r": g("hr_per_pa_vs_r"),
                    "barrel_rate_vs_l": g("barrel_rate_vs_l"),
                    "barrel_rate_vs_r": g("barrel_rate_vs_r")},
    }


def _mlb_tonight(root, pid, date, players):
    """Tonight's sim row: the projection, and how it was arrived at."""
    import pandas as pd
    d = os.path.join(root, "02_curated", "inferences", "game_sim__player_hr",
                     "run_date=%s" % date)
    files = sorted(glob.glob(os.path.join(d, "*.parquet")))
    if not files:
        return None
    df = pd.read_parquet(files[-1])
    df = df[df["date"].astype(str) == date]
    mine = df[df["batter"] == pid]
    if mine.empty:
        return None
    r = mine.iloc[0]

    game = df[df["game_pk"] == r["game_pk"]].sort_values("p_hr", ascending=False)
    rank = int((game["batter"] == pid).values.argmax()) + 1
    names = dict(zip(players["player_id"], players["full_name"]))

    return {
        "game_pk": int(r["game_pk"]), "side": str(r.get("side", "")),
        "p_hr": r3(r.get("p_hr")), "p_hr_cal": r3(r.get("p_hr_cal")),
        "model_price": prob_to_american(r["p_hr_cal"]) if pd.notna(r.get("p_hr_cal")) else None,
        "exp_pa": r3(r.get("exp_pa")), "power_pctile": r3(r.get("power_pctile")),
        "lineup_source": str(r.get("lineup_source", "")),
        "rank_in_game": "%d of %d" % (rank, len(game)),
        "top_in_game": [names.get(b, str(b)) for b in game["batter"].head(3)],
    }


def _mlb_caveats(o):
    out, prof, tn = [], o.get("profile"), o.get("tonight")

    if tn and tn.get("lineup_source") and tn["lineup_source"] != "posted":
        out.append({"code": "projected_lineup", "severity": "high", "detail":
                    "tonight's lineup is %r, not 'posted' — the projection assumes a "
                    "spot that has not been confirmed. Check the lineup before posting."
                    % tn["lineup_source"]})

    if prof:
        c, w = prof["career"], prof["window"]
        soft = [k for k in ("hr_per_pa", "barrel_rate", "hard_hit_rate", "iso")
                if c.get(k) and w.get(k) and w[k] < c[k] * WINDOW_DIVERGENCE]
        if soft:
            out.append({"code": "career_vs_window", "severity": "high", "detail":
                        "recent-window %s %s well below career — the projection is "
                        "riding career profile, not current form. Do not write a "
                        "recent-power line." % (", ".join(soft),
                                                "are" if len(soft) > 1 else "is")})

    s, tg = o.get("season"), o.get("team_games_played")
    if s and tg and s["G"] < tg * PART_TIME_SHARE:
        out.append({"code": "part_time", "severity": "medium", "detail":
                    "%d games played of roughly %d team games — season rates describe "
                    "a part-time role." % (s["G"], tg)})

    lay = (o.get("recent") or {}).get("layoff")
    if lay:
        out.append({"code": "layoff", "severity": "medium", "detail":
                    "%d days between %s and %s — season totals average across the gap. "
                    "Since returning: %s." % (
                        lay["days"], lay["last_before"], lay["returned"],
                        ", ".join("%s %s" % (k, v) for k, v in lay["since"].items()))})

    bb = o.get("batted_ball") or {}
    if s and s["HR"] <= 2 and (bb.get("BIP") or 0) >= 50:
        out.append({"code": "thin_power_sample", "severity": "medium", "detail":
                    "%d HR in %d PA this season on %d balls in play — there is no "
                    "season power number to lead with."
                    % (s["HR"], s["PA"], bb["BIP"])})
    return out


# ---------------------------------------------------------------- WNBA

def wnba_brief(root, name, date):
    import pandas as pd

    logs = pd.read_csv(os.path.join(root, "02_curated", "player_game_logs",
                                    "current.csv.gz"))
    logs = logs[(logs["SEASON_YEAR"].astype(str).str.contains(date[:4])) &
                (logs["SEASON_TYPE"] == "Regular Season")]
    mine = logs[logs["PLAYER_NAME"].map(fold).str.contains(fold(name), regex=False)]
    if mine.empty:
        return None
    who = sorted(mine["PLAYER_NAME"].unique())
    if len(who) > 1:
        exact = [w for w in who if fold(w) == fold(name)]
        if len(exact) != 1:
            die("'%s' matches %d WNBA players: %s — be more specific"
                % (name, len(who), ", ".join(who)))
        who = exact
    full = who[0]
    mine = mine[mine["PLAYER_NAME"] == full].copy()
    mine["GAME_DATE"] = pd.to_datetime(mine["GAME_DATE"])
    mine = mine.sort_values("GAME_DATE")
    team_abbr = mine["TEAM_ABBREVIATION"].iloc[-1]

    season = _wnba_line(mine)
    last10 = [{"date": str(r["GAME_DATE"].date()), "matchup": r["MATCHUP"], "WL": r["WL"],
               "MIN": r3(r["MIN"]), "PTS": int(r["PTS"]), "FGM": int(r["FGM"]),
               "FGA": int(r["FGA"]), "FG3M": int(r["FG3M"]), "REB": int(r["REB"]),
               "AST": int(r["AST"])}
              for _, r in mine.tail(10).iterrows()]

    team = logs[logs["TEAM_ABBREVIATION"] == team_abbr]
    rotation, ranks = _wnba_team_context(team, full)

    ident = _wnba_identity(root, full)
    out = {
        "league": "WNBA", "player": full, "date": date,
        "identity": dict(ident, team_abbreviation=team_abbr),
        "season": season, "recent": {"last10": last10},
        "team": {"games": int(team["GAME_ID"].nunique()), "rotation": rotation,
                 "ranks": ranks},
    }
    out["caveats"] = _wnba_caveats(out)
    return out


def _wnba_line(g):
    fgm, fga = g["FGM"].sum(), g["FGA"].sum()
    m, a = g["FG3M"].sum(), g["FG3A"].sum()
    ftm, fta = g["FTM"].sum(), g["FTA"].sum()
    pct = lambda n, d: r3(n / d * 100) if d else None  # noqa: E731
    return {
        "G": int(g["GAME_ID"].nunique()), "MIN": r3(g["MIN"].mean()),
        "PTS": r3(g["PTS"].mean()), "REB": r3(g["REB"].mean()), "AST": r3(g["AST"].mean()),
        "STL": r3(g["STL"].mean()), "TOV": r3(g["TOV"].mean()),
        "FGM": int(fgm), "FGA": int(fga), "FG_PCT": pct(fgm, fga),
        "FG3M": int(m), "FG3A": int(a), "FG3_PCT": pct(m, a),
        "FTM": int(ftm), "FTA": int(fta), "FT_PCT": pct(ftm, fta),
        "PTS_total": int(g["PTS"].sum()), "AST_total": int(g["AST"].sum()),
    }


def _wnba_team_context(team, full):
    """Rotation table, plus rankings that refuse to be fooled by tiny samples."""
    agg = team.groupby("PLAYER_NAME").agg(
        G=("GAME_ID", "nunique"), MIN=("MIN", "mean"), PTS=("PTS", "mean"),
        REB=("REB", "mean"), AST=("AST", "mean"),
        FGM=("FGM", "sum"), FGA=("FGA", "sum"), FG3M=("FG3M", "sum"),
        FG3A=("FG3A", "sum"), FTM=("FTM", "sum"), FTA=("FTA", "sum"))
    agg = agg[agg["G"] >= 10]
    for pc, (n, d) in {"FG_PCT": ("FGM", "FGA"), "FG3_PCT": ("FG3M", "FG3A"),
                       "FT_PCT": ("FTM", "FTA")}.items():
        agg[pc] = (agg[n] / agg[d] * 100).where(agg[d] > 0)

    rotation = [dict(player=p, **{k: r3(v) for k, v in row.items()})
                for p, row in agg.sort_values("MIN", ascending=False).iterrows()]

    ranks = {}
    for stat in ("MIN", "PTS", "REB", "AST", "FG_PCT", "FG3_PCT", "FT_PCT"):
        if stat not in agg or full not in agg.index or agg.at[full, stat] != agg.at[full, stat]:
            continue
        mine_v = float(agg.at[full, stat])
        floor_col = {"FG_PCT": "FGA", "FG3_PCT": "FG3A", "FT_PCT": "FTA"}.get(stat)
        floor = VOLUME_FLOORS.get(stat)

        pool = agg[agg[stat].notna()]
        # Anyone with a better raw rate who only clears it on trivial volume. A
        # caption that says "team best" is false unless these are named.
        beaten_by_thin = []
        if floor_col:
            thin = pool[(pool[floor_col] < floor) & (pool[stat] > mine_v)]
            beaten_by_thin = ["%s %.1f%% on %d %s" % (p, r[stat], r[floor_col], floor_col)
                              for p, r in thin.iterrows()]
            pool = pool[pool[floor_col] >= floor]

        ordered = pool[stat].sort_values(ascending=False)
        if full not in ordered.index:
            continue
        rank = int(list(ordered.index).index(full)) + 1
        ranks[stat] = {
            "value": r3(mine_v), "rank": rank, "of": int(len(ordered)),
            "leader": ordered.index[0], "qualifier":
                ("%s >= %d" % (floor_col, floor)) if floor_col else "G >= 10",
        }
        if beaten_by_thin:
            ranks[stat]["beaten_below_qualifier"] = beaten_by_thin
    return rotation, ranks


def _wnba_identity(root, full):
    path = os.path.join(root, "02_curated", "rosters", "current.csv")
    if not os.path.exists(path):
        return {}
    import pandas as pd
    r = pd.read_csv(path)
    hit = r[r["PLAYER_NAME"].map(fold) == fold(full)]
    if hit.empty:
        return {}
    p = hit.iloc[0]
    return {"player_id": int(p["PLAYER_ID"]), "jersey": str(p.get("JERSEY_NUMBER", "")),
            "team_city": p.get("TEAM_CITY", ""), "team_name": p.get("TEAM_NAME", ""),
            "position": p.get("POSITION", ""), "experience": p.get("EXPERIENCE", "")}


def _wnba_caveats(o):
    out = []
    s, t = o["season"], o["team"]
    if t["games"] and s["G"] < t["games"] * PART_TIME_SHARE:
        out.append({"code": "part_time", "severity": "medium", "detail":
                    "%d games of the team's %d — season rates describe a part-time role."
                    % (s["G"], t["games"])})
    for stat, r in (t.get("ranks") or {}).items():
        # Only a rank-1 stat tempts a "team best" line, and only then does
        # someone beating it below the volume floor make that line false. Firing
        # this at rank 5 is noise that buries the one case that matters.
        if r["rank"] == 1 and r.get("beaten_below_qualifier"):
            out.append({"code": "thin_volume_superlative", "severity": "high", "detail":
                        "%s leads the team at %s among %s — but %s beat it below that "
                        "floor, so an unqualified 'team best' is false. Quote the "
                        "number, or qualify the claim."
                        % (stat, r["value"], r["qualifier"],
                           " and ".join(r["beaten_below_qualifier"]))})
    return out


# ---------------------------------------------------------------- output

def emit_text(o):
    print("%s — %s, %s" % (o["player"], o["league"], o["date"]))
    i = o.get("identity") or {}
    club = i.get("team") or ("%s %s" % (i.get("team_city", ""),
                                        i.get("team_name", ""))).strip()
    bits = [x for x in (club,
                        "#%s" % i["jersey"] if i.get("jersey") else "",
                        i.get("position", "")) if x]
    print("  " + " · ".join(bits))
    if i.get("jersey_note"):
        print("  jersey: %s" % i["jersey_note"])

    s = o.get("season")
    if s:
        print("\n## Season")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in s.items() if v is not None))
    bb = o.get("batted_ball")
    if bb:
        print("\n## Batted ball")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in bb.items() if v is not None))

    prof = o.get("profile")
    if prof:
        print("\n## Profile (as of %s)" % prof["as_of"])
        for label in ("career", "window", "platoon"):
            vals = {k: v for k, v in prof[label].items() if v is not None}
            if vals:
                print("  %-8s %s" % (label, " · ".join("%s %s" % kv for kv in vals.items())))

    tn = o.get("tonight")
    if tn:
        print("\n## Tonight")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in tn.items() if v is not None))

    rec = o.get("recent") or {}
    if rec.get("last10"):
        print("\n## Last %d" % len(rec["last10"]))
        for g in rec["last10"]:
            print("  " + " · ".join("%s %s" % (k, v) for k, v in g.items()))
    if rec.get("layoff"):
        print("\n## Layoff\n  %s" % json.dumps(rec["layoff"]))

    t = o.get("team")
    if t:
        print("\n## Team (%d games)" % t["games"])
        for r in t["rotation"]:
            print("  " + " · ".join("%s %s" % (k, v) for k, v in r.items() if v is not None))
        if t.get("ranks"):
            print("\n## Rankings (volume-guarded)")
            for stat, r in t["ranks"].items():
                line = "  %-8s %s — rank %d of %d among %s (leader %s)" % (
                    stat, r["value"], r["rank"], r["of"], r["qualifier"], r["leader"])
                print(line)
                for x in r.get("beaten_below_qualifier", []):
                    print("           ⚠ beaten below the floor by %s" % x)

    cav = o.get("caveats") or []
    print("\n## Caveats")
    if not cav:
        print("  (none)")
    for c in cav:
        print("  [%s] %s: %s" % (c.get("severity", "?"), c["code"], c["detail"]))
    print("\nEvery number above came from a file. See SKILL.md rules 1 and 6.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--player", required=True, help="name; accents optional")
    ap.add_argument("--league", choices=("mlb", "wnba"), help="default: try both")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today ET)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    leagues = [args.league] if args.league else ["mlb", "wnba"]

    for lg in leagues:
        root = data_root(lg)
        if not root or not os.path.isdir(root):
            continue
        brief = (mlb_brief if lg == "mlb" else wnba_brief)(root, args.player, date)
        if brief:
            print(json.dumps(brief, indent=2, default=str)) if args.json else emit_text(brief)
            return
    die("no %s player matching %r (checked %s)"
        % ("/".join(l.upper() for l in leagues), args.player, date))


if __name__ == "__main__":
    main()
