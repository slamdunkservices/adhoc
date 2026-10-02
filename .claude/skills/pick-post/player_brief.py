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
    python3 player_brief.py --player "Tank Bigsby" --league nfl

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
  * postseason          — WNBA playoff games exist; the season line excludes them

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

# A steal card is a bet on tonight. Past this many days since the last steal,
# the season total is history and the caption may not imply current form.
STEAL_DROUGHT_DAYS = 7

# Attempts per time on a stealable base, below which "he runs constantly" is
# not a claim the data supports. League-wide the rate sits near 5%.
STEAL_QUIET_RATE = 0.06

# Minimum attempts before a rate stat may be compared across a roster. A
# team-best free-throw percentage is meaningless at 2 attempts, and quoting it
# as one is how a caption ends up false. Tuned to WNBA season volumes.
VOLUME_FLOORS = {"FG_PCT": 100, "FG3_PCT": 50, "FT_PCT": 20}

# WNBA SEASON_TYPE values that are real games, and the tag each carries in
# recent form. Pre Season is left out. The Cup final is a real game but counts
# toward neither the standings nor the season line.
WNBA_GAME_TYPES = {"Regular Season": "REG", "Commissioner's Cup": "CUP",
                   "PlayIn": "PLAY-IN", "Playoffs": "PO"}
WNBA_POSTSEASON = ("PlayIn", "Playoffs")


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
    baserunning = _mlb_baserunning(root, pid, season)
    production = _mlb_production(root, pid, season, b)
    lineup = _mlb_lineup(root, p["full_name"], date)

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
        "baserunning": baserunning, "production": production,
        "recent": {"last10": last10, "layoff": layoff},
        "profile": profile, "tonight": tonight, "lineup": lineup,
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


# Credited steals and the outs that end them, out of the steal-event taxonomy in
# 02_curated/baserunning/steal_events. `kind` is the classification to read:
# `advance` = a credited stolen base, `out` = caught stealing or picked off in
# the act. The `uncredited` kind looks like a steal and is NOT one (defensive
# indifference, fielder's choice advances), and pickoff_throw/stepoff are the
# 12k-row pitcher-disengagement rows — counting either inflates a steal line.
SB_KIND_CREDITED = "advance"
SB_KIND_OUT = "out"


def _mlb_baserunning(root, pid, season):
    """Season steal line + speed — the caption's material for a steal card.

    The HR blocks above (batted ball, power profile) say nothing about a steal
    play, and a steal caption built from them is a caption about the wrong
    skill. Rule 1 wants a real season number for the thing being bet.
    """
    import pandas as pd
    base = os.path.join(root, "02_curated", "baserunning")
    ev = os.path.join(base, "steal_events", "%d.parquet" % season)
    op = os.path.join(base, "steal_opportunities", "%d.parquet" % season)
    if not os.path.exists(ev):
        return None

    e = pd.read_parquet(ev)
    mine = e[e["runner_id"] == pid].copy()
    sb = mine[(mine["kind"] == SB_KIND_CREDITED)
              & mine["event_type"].astype(str).str.startswith("stolen_base")]
    cs = mine[mine["kind"] == SB_KIND_OUT]
    att = len(sb) + len(cs)

    # steal_events carries game_pk but no date; opportunities carries both, and
    # is the same slate, so it is the crosswalk. It also gives the denominator
    # that matters for a per-game market: how often he was ON a stealable base.
    opps, dates, last = None, {}, []
    if os.path.exists(op):
        o = pd.read_parquet(op, columns=["game_pk", "game_date", "runner_id"])
        dates = dict(zip(o["game_pk"], o["game_date"].astype(str)))
        opps = int((o["runner_id"] == pid).sum())
        if len(sb):
            d = sorted(str(dates.get(g, "")) for g in sb["game_pk"])
            last = [x for x in d if x][-8:]

    # League rank on credited steals — a superlative that is checkable, unlike
    # "one of the best baserunners in the league".
    lg = (e[(e["kind"] == SB_KIND_CREDITED)
            & e["event_type"].astype(str).str.startswith("stolen_base")]
          ["runner_id"].value_counts())
    rank = (list(lg.index).index(pid) + 1) if pid in set(lg.index) else None

    out = {
        "SB": len(sb), "CS": len(cs), "attempts": att,
        "success_rate": r3(len(sb) / att) if att else None,
        "SB_2B": int((sb["event_type"] == "stolen_base_2b").sum()),
        "SB_3B": int((sb["event_type"] == "stolen_base_3b").sum()),
        "opportunities": opps,
        "attempt_rate": r3(att / opps) if opps else None,
        "league_rank_SB": rank, "league_runners_with_SB": int(len(lg)),
        "steal_dates": last,
    }
    out.update(_mlb_sprint(root, pid, season))
    return out


def _mlb_sprint(root, pid, season):
    """Savant sprint speed, with its league percentile. Raw feed, not curated."""
    import pandas as pd
    path = os.path.join(root, "01_raw", "savant_sprint_speed", "%d.csv" % season)
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path)
    r = df[df["player_id"] == pid]
    if r.empty or pd.isna(r.iloc[0].get("sprint_speed")):
        return {}
    v = float(r.iloc[0]["sprint_speed"])
    return {
        "sprint_speed": v,
        "sprint_speed_pctile": r3((df["sprint_speed"] < v).mean()),
        "competitive_runs": int(r.iloc[0].get("competitive_runs") or 0),
        "hp_to_1b": r3(r.iloc[0].get("hp_to_1b")) if pd.notna(r.iloc[0].get("hp_to_1b")) else None,
    }


def _mlb_production(root, pid, season, b):
    """Total bases, runs and RBI per game — the caption's material for a total
    bases 2+, rbi 1+ or run scored 1+ card.

    These markets settle per game, so the checkable numbers are game counts
    ("2+ total bases in 23 of 77 games"), not season rates. TB comes off the
    plate appearances; runs, RBI and the batting-order slot are not in that file
    and come from the raw boxscores. Runs and RBI follow the lineup slot more
    than the hitter, so the slots he has started in are part of the block.
    """
    import pandas as pd
    games = b.groupby("game_pk").agg(date=("game_date", "first"), PA=("game_pk", "size"),
                                     TB=("total_bases", "sum"))
    box_path = os.path.join(root, "01_raw", "boxscores", "%d.csv" % season)
    if os.path.exists(box_path):
        box = pd.read_csv(box_path, usecols=["game_pk", "player_id", "batting_order",
                                             "runs", "rbi"])
        box = box[box["player_id"] == pid].drop_duplicates("game_pk").set_index("game_pk")
        games = games.join(box[["batting_order", "runs", "rbi"]], how="left")
    games = games.sort_values("date")

    def counts(g):
        out = {"G": int(len(g)), "TB": int(g["TB"].sum()),
               "games_2plus_TB": int((g["TB"] >= 2).sum())}
        if "runs" in g.columns:
            out.update(R=int(g["runs"].sum()), RBI=int(g["rbi"].sum()),
                       games_with_R=int((g["runs"] >= 1).sum()),
                       games_with_RBI=int((g["rbi"] >= 1).sum()))
        return out

    out = {"season": counts(games), "last15": counts(games.tail(15))}

    # boxscores encode the slot as slot*100, plus a sequence for substitutes:
    # 500 started fifth, 701 came in for the seventh hitter. Only a starter's
    # slot says where he bats.
    if "batting_order" in games.columns:
        starts = games[games["batting_order"].notna() & (games["batting_order"] % 100 == 0)]
        out["starts"] = counts(starts)
        slots = (starts["batting_order"] // 100).astype(int).value_counts()
        out["start_slots"] = {str(k): int(v) for k, v in slots.sort_index().items()}
        out["usual_slot"] = int(slots.idxmax()) if len(slots) else None

    def line(x):
        ab = x["is_ab"].sum()
        return {"PA": int(len(x)), "AB": int(ab),
                "AVG": r3(x["is_hit"].sum() / ab) if ab else None,
                "SLG": r3(x["total_bases"].sum() / ab) if ab else None,
                "XBH": int(x["events"].isin(["double", "triple", "home_run"]).sum()),
                "HR": int(x["is_hr"].sum())}

    out["splits"] = {"vs_%s" % h: line(x) for h, x in b.groupby("p_throws")}

    # A mid-season trade splits the season line across two clubs; a caption
    # about "since joining" needs the club it is about.
    if b["bat_team"].nunique() > 1:
        out["by_team"] = {t: dict(line(x), G=int(x["game_pk"].nunique()),
                                  first=str(x["game_date"].min().date()),
                                  last=str(x["game_date"].max().date()))
                          for t, x in b.groupby("bat_team")}
    return out


def _mlb_lineup(root, full_name, date):
    """Tonight's batting slot and opposing starter, off the RotoWire scrape."""
    import pandas as pd
    path = os.path.join(root, "01_raw", "rotowire_lineups", "%s.csv" % date)
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    if "CREATE_DTTM" in df.columns:
        df = df.sort_values("CREATE_DTTM")
    me = df[(df["ROLE"] == "B") & (df["PLAYER_NAME"].map(fold) == fold(full_name))]
    if me.empty:
        return None
    # A doubleheader posts two blocks for the club; the first is the earlier game.
    r = me.iloc[0]
    out = {"batting_order": int(r["BATTING_ORDER"]) if pd.notna(r["BATTING_ORDER"]) else None,
           "position": r.get("POSITION"), "lineup_status": r.get("LINEUP_STATUS"),
           "game_time": r.get("GAME_TIME")}
    sp = df[(df["ROLE"] == "SP") & (df["MATCHUP"] == r["MATCHUP"])
            & (df["TEAM_ABBREVIATION"] != r["TEAM_ABBREVIATION"])]
    if len(sp):
        s = sp.iloc[-1]
        out.update(opp_sp=s["PLAYER_NAME"], opp_sp_throws=s.get("THROWS"),
                   opp_sp_line=s.get("SP_STATS"), opp_sp_status=s.get("LINEUP_STATUS"))
    return out


def _price(row, col):
    import pandas as pd
    v = row.get(col)
    return prob_to_american(v) if v is not None and pd.notna(v) else None


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
        # The steal model rides the same sim snapshot: p_sb is P(>=1 SB) and
        # p_sb_cal is the calibrated basis the SB edge table prices off when it
        # is present. Quote the one the edge row's probability_source names —
        # SKILL.md rule 3, the MLB version of it.
        "p_sb": r3(r.get("p_sb")), "p_sb_cal": r3(r.get("p_sb_cal")),
        "exp_sb": r3(r.get("exp_sb")),
        "model_price_sb": (prob_to_american(r["p_sb_cal"]) if pd.notna(r.get("p_sb_cal"))
                           else prob_to_american(r["p_sb"]) if pd.notna(r.get("p_sb")) else None),
        # Batter props price off their own (uncalibrated) sim columns: p_2tb for
        # total bases 2+, p_rbi for rbi 1+, p_run for run scored 1+ — the
        # `probability_source` each of those edge rows names.
        "p_2tb": r3(r.get("p_2tb")), "exp_tb": r3(r.get("exp_tb")),
        "model_price_tb": _price(r, "p_2tb"),
        "p_rbi": r3(r.get("p_rbi")), "exp_rbi": r3(r.get("exp_rbi")),
        "model_price_rbi": _price(r, "p_rbi"),
        "p_run": r3(r.get("p_run")), "exp_runs": r3(r.get("exp_runs")),
        "model_price_run": _price(r, "p_run"),
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

    # Steal-card guards. A season SB total says nothing about whether he is
    # running NOW, and a caption that reads as current form off a total that
    # stopped growing two weeks ago is the rule-6 failure in slow motion.
    br = o.get("baserunning") or {}
    if br.get("steal_dates"):
        last = br["steal_dates"][-1]
        try:
            gap = (datetime.strptime(o["date"], "%Y-%m-%d")
                   - datetime.strptime(last, "%Y-%m-%d")).days
        except ValueError:
            gap = 0
        if gap >= STEAL_DROUGHT_DAYS:
            out.append({"code": "steal_drought", "severity": "high", "detail":
                        "last stolen base was %s, %d days ago — the season total is "
                        "real but he is not running right now. Lead with the season "
                        "line or the speed, not with recent form." % (last, gap)})
    if br.get("attempt_rate") is not None and br["attempt_rate"] < STEAL_QUIET_RATE:
        out.append({"code": "low_attempt_rate", "severity": "medium", "detail":
                    "%d attempts in %d chances on base (%.0f%%) — he is on base far "
                    "more often than he goes, so don't write him as a constant threat."
                    % (br["attempts"], br["opportunities"], br["attempt_rate"] * 100)})

    # Runs and RBI chances follow the lineup slot. When tonight's slot isn't the
    # one he usually starts in, his season R/RBI counts were built somewhere else
    # in the order and don't describe tonight's role.
    prod, lu = o.get("production") or {}, o.get("lineup") or {}
    if lu.get("batting_order") and prod.get("usual_slot") \
            and lu["batting_order"] != prod["usual_slot"]:
        slots = prod.get("start_slots") or {}
        out.append({"code": "lineup_slot_change", "severity": "medium", "detail":
                    "batting #%d tonight, but #%d is his usual spot (%s of %d starts; "
                    "#%d in %s) — season runs/RBI were built mostly elsewhere in the "
                    "order. Don't write them as tonight's role."
                    % (lu["batting_order"], prod["usual_slot"],
                       slots.get(str(prod["usual_slot"]), 0), sum(slots.values()),
                       lu["batting_order"], slots.get(str(lu["batting_order"]), 0))})

    bb = o.get("batted_ball") or {}
    if s and s["HR"] <= 2 and (bb.get("BIP") or 0) >= 50:
        out.append({"code": "thin_power_sample", "severity": "medium", "detail":
                    "%d HR in %d PA this season on %d balls in play — there is no "
                    "season power number to lead with."
                    % (s["HR"], s["PA"], bb["BIP"])})
    return out


# ---------------------------------------------------------------- WNBA

def wnba_brief(root, name, date):
    """Season and Team are regular season only — that is what "this season"
    means in a caption. Recent form and the postseason block include playoff
    and play-in games, tagged, because in October "her last game" is a playoff
    game and a brief that hides it puts a stale date in the copy."""
    import pandas as pd

    logs = pd.read_csv(os.path.join(root, "02_curated", "player_game_logs",
                                    "current.csv.gz"))
    logs["GAME_DATE"] = pd.to_datetime(logs["GAME_DATE"])
    # Games before --date only: a brief for a past pick describes the player as
    # of that morning, not with that night's box score already in it.
    logs = logs[(logs["SEASON_YEAR"].astype(str).str.contains(date[:4])) &
                (logs["SEASON_TYPE"].isin(WNBA_GAME_TYPES)) &
                (logs["GAME_DATE"] < pd.Timestamp(date))]
    reg = logs[logs["SEASON_TYPE"] == "Regular Season"]
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
    mine = mine[mine["PLAYER_NAME"] == full].sort_values("GAME_DATE")
    team_abbr = mine["TEAM_ABBREVIATION"].iloc[-1]

    season = _wnba_line(mine[mine["SEASON_TYPE"] == "Regular Season"])
    last10 = [{"date": str(r["GAME_DATE"].date()),
               "type": WNBA_GAME_TYPES[r["SEASON_TYPE"]], "matchup": r["MATCHUP"],
               "WL": r["WL"], "MIN": r3(r["MIN"]), "PTS": int(r["PTS"]),
               "FGM": int(r["FGM"]), "FGA": int(r["FGA"]), "FG3M": int(r["FG3M"]),
               "REB": int(r["REB"]), "AST": int(r["AST"])}
              for _, r in mine.tail(10).iterrows()]

    team = reg[reg["TEAM_ABBREVIATION"] == team_abbr]
    rotation, ranks = _wnba_team_context(team, full)

    ident = _wnba_identity(root, full)
    out = {
        "league": "WNBA", "player": full, "date": date,
        "identity": dict(ident, team_abbreviation=team_abbr),
        "season_scope": "regular season",
        "season": season,
        "postseason": _wnba_postseason(logs, mine, team_abbr),
        "recent": {"last10": last10},
        "team": {"scope": "regular season", "games": int(team["GAME_ID"].nunique()),
                 "rotation": rotation, "ranks": ranks},
    }
    out["caveats"] = _wnba_caveats(out)
    return out


def _wnba_postseason(logs, mine, team_abbr):
    """Series-by-series record for the player's team, read off the team's rows
    so a game the player sat out still counts toward the series."""
    po = logs[(logs["TEAM_ABBREVIATION"] == team_abbr) &
              (logs["SEASON_TYPE"].isin(WNBA_POSTSEASON))]
    if po.empty:
        return None
    games = po.drop_duplicates("GAME_ID").sort_values("GAME_DATE").copy()
    games["OPP"] = games["MATCHUP"].str.split().str[-1]
    hers = mine[mine["SEASON_TYPE"].isin(WNBA_POSTSEASON)]
    played = set(hers["GAME_ID"])

    # A team meets an opponent at most once per round, so (type, opponent) is a
    # series. sort=False keeps them in the order they were played.
    series = []
    for (stype, opp), g in games.groupby(["SEASON_TYPE", "OPP"], sort=False):
        w, l = int((g["WL"] == "W").sum()), int((g["WL"] == "L").sum())
        series.append({
            "round": WNBA_GAME_TYPES[stype], "opponent": opp, "record": "%d-%d" % (w, l),
            "games": [{"date": str(r["GAME_DATE"].date()), "matchup": r["MATCHUP"],
                       "WL": r["WL"], "played": r["GAME_ID"] in played}
                      for _, r in g.iterrows()],
        })
    last = games.iloc[-1]
    return {
        "team": team_abbr, "series": series,
        "team_games": int(len(games)), "games_played": int(len(played)),
        "last_game": {"date": str(last["GAME_DATE"].date()), "matchup": last["MATCHUP"],
                      "WL": last["WL"]},
        "line": _wnba_line(hers) if not hers.empty else None,
    }


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
    ps = o.get("postseason")
    if ps:
        lg = ps["last_game"]
        out.append({"code": "postseason", "severity": "high", "detail":
                    "%s has played %d postseason game(s) (%s); the last was %s %s %s. "
                    "%s played %d of them. Season and Team are regular season only — "
                    "a caption's 'last game' or 'recent form' must count the playoffs, "
                    "and a season number must be called a regular-season number."
                    % (ps["team"], ps["team_games"],
                       "; ".join("%s vs %s %s" % (x["round"], x["opponent"], x["record"])
                                 for x in ps["series"]),
                       lg["date"], lg["matchup"], lg["WL"],
                       o["player"], ps["games_played"])})
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


# ---------------------------------------------------------------- NFL

# Below this many games into a season, a per-game rate is a couple of games,
# not a trend. Say "in two games", not "averaging".
NFL_THIN_GAMES = 3
# Same usage gate the TD alert feed applies (jobs/nfl/intraday.sh).
NFL_MIN_EXPECTED_OPPS = 3.5
# Injury designations that make a player a game-time question.
NFL_DOUBT = ("QUESTIONABLE", "DOUBTFUL", "OUT", "IR", "PUP", "SUSPENDED")
NFL_TD_MARKETS = {"1+": "model_price_td", "2+": "model_price_td2",
                  "FTD": "model_price_ftd"}


def nfl_brief(root, name, date):
    """Anytime / first-TD scorer brief. Regular season only, everywhere: the
    game-log and pbp files carry playoff games too, and a caption that says
    "last season" means the regular season."""
    import pandas as pd

    season = int(date[:4]) if date[5:7] >= "08" else int(date[:4]) - 1
    raw = os.path.join(root, "01_raw")
    ros = pd.read_csv(os.path.join(raw, "rosters", "%d.csv" % season), low_memory=False)
    hit = ros[ros["full_name"].map(fold).str.contains(fold(name), regex=False)]
    if hit.empty:
        return None
    who = sorted(hit["full_name"].unique())
    if len(who) > 1:
        exact = [w for w in who if fold(w) == fold(name)]
        if len(exact) != 1:
            die("'%s' matches %d NFL players: %s — be more specific"
                % (name, len(who), ", ".join(who)))
        who = exact
    me = hit[hit["full_name"] == who[0]].sort_values("week").iloc[-1]
    pid, team, pos = me["gsis_id"], me["team"], me["position"]
    jersey = None if pd.isna(me["jersey_number"]) else str(int(me["jersey_number"]))

    prev_team = None
    prev_path = os.path.join(raw, "rosters", "%d.csv" % (season - 1))
    if os.path.exists(prev_path):
        pr = pd.read_csv(prev_path, usecols=["gsis_id", "team", "week"], low_memory=False)
        pr = pr[pr["gsis_id"] == pid].sort_values("week")
        prev_team = pr["team"].iloc[-1] if len(pr) else None

    ident = {"team": team, "jersey": jersey, "position": pos,
             "years_exp": int(me["years_exp"]) if pd.notna(me["years_exp"]) else None,
             "college": me.get("college"), "draft_club": me.get("draft_club"),
             "last_season_team": prev_team, "gsis_id": pid,
             "source": "01_raw/rosters/%d.csv" % season}

    out = {"league": "NFL", "player": who[0], "date": date, "identity": ident,
           "season": _nfl_line(raw, pid, season),
           "last_season": _nfl_line(raw, pid, season - 1)}
    out["recent"] = {"games": _nfl_games(root, pid)}
    out["first_tds"] = _nfl_first_tds(root, pid)
    out["depth"] = _nfl_depth(root, pid, team, pos)
    out["tonight"] = _nfl_tonight(root, pid, date)
    opp = (out["tonight"] or {}).get("opponent")
    if opp:
        out["opponent"] = _nfl_defense(raw, opp, season, pos)
    out["caveats"] = _nfl_caveats(out, season)
    return out


_PBP_COLS = ("game_id", "season_type", "week", "posteam", "defteam", "yardline_100",
             "rusher_player_id", "receiver_player_id", "rush_attempt", "pass_attempt",
             "rushing_yards", "receiving_yards", "complete_pass", "rush_touchdown",
             "pass_touchdown", "td_player_id", "return_touchdown")


def _nfl_pbp(raw, season):
    import pandas as pd
    path = os.path.join(raw, "pbp", "%d.csv" % season)
    if not os.path.exists(path):
        return None
    p = pd.read_csv(path, usecols=lambda c: c in _PBP_COLS, low_memory=False)
    return p[p["season_type"] == "REG"]


def _nfl_line(raw, pid, season):
    """Regular-season rushing/receiving/TD line off play-by-play."""
    p = _nfl_pbp(raw, season)
    if p is None:
        return None
    ru = p[(p["rusher_player_id"] == pid) & (p["rush_attempt"] == 1)]
    re_ = p[p["receiver_player_id"] == pid]
    games = set(ru["game_id"]) | set(re_["game_id"])
    if not games:
        return None
    tds = p[p["td_player_id"] == pid]
    car = len(ru)
    return {
        "season": season, "source": "01_raw/pbp/%d.csv (REG)" % season,
        "G_with_touch": len(games), "carries": car,
        "rush_yds": int(ru["rushing_yards"].sum()),
        "ypc": r3(ru["rushing_yards"].sum() / car) if car else None,
        "targets": len(re_), "rec": int(re_["complete_pass"].sum()),
        "rec_yds": int(re_["receiving_yards"].sum()),
        "TD": int(len(tds)),
        "rush_TD": int(ru["rush_touchdown"].sum()),
        "rec_TD": int(re_["pass_touchdown"].sum()),
        "games_with_TD": int(tds["game_id"].nunique()),
        "rz_touches": int((ru["yardline_100"] <= 20).sum() + (re_["yardline_100"] <= 20).sum()),
        "inside10_carries": int((ru["yardline_100"] <= 10).sum()),
        "inside5_carries": int((ru["yardline_100"] <= 5).sum()),
    }


def _nfl_games(root, pid, n=6):
    import pandas as pd
    g = pd.read_parquet(os.path.join(root, "02_curated", "touchdowns", "player_game_logs",
                                     "player_game_segments.parquet"))
    g = g[(g["player_id"] == pid) & (g["segment"] == "full")].sort_values(["season", "week"])
    # REG only — weeks past 18 are the playoffs.
    g = g[g["week"] <= 18]
    return [{"season": int(r["season"]), "wk": int(r["week"]), "team": r["team"],
             "opp": r["opponent"], "snap%": r3(r["snap_share_off"]),
             "car": int(r["rush_attempts"]), "tgt": int(r["targets"]),
             "rz_opp": int(r["red_zone_opportunities"]),
             "GL_car": int(r["goal_line_carries"]), "TD": int(r["tds"])}
            for _, r in g.tail(n).iterrows()]


def _nfl_first_tds(root, pid):
    import pandas as pd
    path = os.path.join(root, "02_curated", "touchdowns", "first_touchdowns",
                        "first_touchdowns.parquet")
    if not os.path.exists(path):
        return None
    f = pd.read_parquet(path)
    f = f[f["season_type"] == "REG"]
    game = f[f["game_first_td_player_id"] == pid].drop_duplicates("game_id")
    team = f[f["team_first_td_player_id"] == pid].drop_duplicates("game_id")
    return {"game_first_TD_career": int(len(game)),
            "team_first_TD_career": int(len(team)),
            "game_first_TD_games": [g for g in game["game_id"].tail(5)],
            "source": "02_curated/touchdowns/first_touchdowns/first_touchdowns.parquet (REG)"}


def _nfl_depth(root, pid, team, pos):
    import pandas as pd
    base = os.path.join(root, "02_curated", "depth_charts")
    out = {}
    d = pd.read_parquet(os.path.join(base, "consensus_current.parquet"))
    mine = d[d["gsis_id"] == pid]
    # One row per scope the player is listed in — his position, and also KR/PR
    # when he returns. The position scope is the one that says starter/backup.
    if (mine["scope"] == pos).any():
        mine = mine[mine["scope"] == pos]
    if len(mine):
        r = mine.iloc[0]
        room = d[(d["team"] == team) & (d["scope"] == r["scope"])
                 ].sort_values("consensus_depth_rank")
        out = {"scope": r["scope"], "depth_rank": int(r["consensus_depth_rank"]),
               "starter": bool(r["is_consensus_starter"]),
               "sources_listing": "%d/%d" % (r["n_sources_listing"], r["n_sources_covering"]),
               "room": ["%d. %s" % (x["consensus_depth_rank"], x["player_name"])
                        for _, x in room.head(4).iterrows()],
               "as_of": str(r["as_of"])}
    inj = pd.read_parquet(os.path.join(base, "injury_status_current.parquet"))
    me = inj[inj["gsis_id"] == pid]
    out["injury"] = (None if me.empty or pd.isna(me.iloc[0]["worst_status"])
                     else "%s (%s)" % (me.iloc[0]["worst_status"], me.iloc[0]["injury_detail"]))
    hurt = inj[(inj["team"] == team) & (inj["position"] == pos) & (inj["gsis_id"] != pid)
               & inj["worst_status"].isin(NFL_DOUBT)]
    out["same_position_injuries"] = ["%s %s" % (x["player_name"], x["worst_status"])
                                     for _, x in hurt.iterrows()]
    return out


def _nfl_tonight(root, pid, date):
    import pandas as pd
    path = os.path.join(root, "02_curated", "edges", "td_edges_current.csv")
    if not os.path.exists(path):
        return None
    e = pd.read_csv(path, dtype={"player_id": str})
    e = e[e["player_id"] == pid]
    if e.empty:
        return None
    r = e.iloc[0]
    out = {"game": r["event"], "game_id": r["game_id"], "date": r["date"],
           "opponent": r["opponent"],
           "exp_opportunities": r3(r.get("expected_opportunities")),
           "exp_return_opps": r3(r.get("expected_return_opportunities")),
           "exp_total_opps": r3(r.get("expected_total_opportunities"))}
    for mkt, key in NFL_TD_MARKETS.items():
        m = e[e["market"] == mkt]
        if len(m):
            out["p_" + key.split("_")[-1]] = r3(m["projection"].iloc[0])
            out[key] = prob_to_american(m["projection"].iloc[0])
    sched = os.path.join(root, "01_raw", "schedules", "%s.csv" % r["game_id"][:4])
    if os.path.exists(sched):
        s = pd.read_csv(sched)
        s = s[s["game_id"] == r["game_id"]]
        if len(s):
            s = s.iloc[0]
            out.update({"kickoff_ET": "%s %s %s" % (s["weekday"], s["gameday"], s["gametime"]),
                        "stadium": s.get("stadium"), "roof": s.get("roof"),
                        "spread_line": s.get("spread_line"), "total_line": s.get("total_line")})
    out["source"] = "02_curated/edges/td_edges_current.csv + 01_raw/schedules"
    return out


def _nfl_defense(raw, opp, season, pos):
    """What tonight's opponent has allowed this regular season."""
    p = _nfl_pbp(raw, season)
    if p is None:
        return None
    d = p[p["defteam"] == opp]
    ru = d[d["rush_attempt"] == 1]
    games = d["game_id"].nunique()
    return {"team": opp, "games": games,
            "rush_yds_per_carry_allowed": r3(ru["rushing_yards"].mean()) if len(ru) else None,
            "rush_TD_allowed": int(ru["rush_touchdown"].sum()),
            "pass_TD_allowed": int(d["pass_touchdown"].sum()),
            "source": "01_raw/pbp/%d.csv (REG, defteam)" % season}


def _nfl_caveats(o, season):
    out = []
    i, s, dep, tn = o["identity"], o.get("season") or {}, o.get("depth") or {}, o.get("tonight") or {}
    if s.get("G_with_touch", 0) <= NFL_THIN_GAMES:
        out.append({"code": "thin_sample", "severity": "high", "detail":
                    "%d game(s) with a touch this season — quote counts ('a TD in week 2'), "
                    "never per-game rates or 'averaging'." % s.get("G_with_touch", 0)})
    if dep.get("depth_rank", 1) > 1 and not dep.get("starter"):
        out.append({"code": "backup_role", "severity": "high", "detail":
                    "Consensus #%d on the depth chart (%s). Don't call him the starter or "
                    "'the lead back'; write the role he actually has."
                    % (dep["depth_rank"], ", ".join(dep.get("room", [])))})
    if dep.get("injury"):
        out.append({"code": "injury_status", "severity": "high", "detail":
                    "Listed %s — flag to the user before posting." % dep["injury"]})
    if dep.get("same_position_injuries"):
        out.append({"code": "depth_injury", "severity": "medium", "detail":
                    "Same-position teammates on the report: %s. Usage tonight may not "
                    "look like the season line." % ", ".join(dep["same_position_injuries"])})
    if i.get("last_season_team") and i["last_season_team"] != i["team"]:
        out.append({"code": "team_change", "severity": "medium", "detail":
                    "Finished last season with %s — last-season numbers were not in this "
                    "uniform. Photo must be %s (rule 2)." % (i["last_season_team"], i["team"])})
    last = o.get("last_season") or {}
    if last and len({g["team"] for g in o["recent"]["games"] if g["season"] == season - 1}) > 1:
        out.append({"code": "split_season", "severity": "medium", "detail":
                    "Last season spans two clubs; the pbp line totals both."})
    eo = tn.get("exp_total_opps")
    if eo is not None and eo < NFL_MIN_EXPECTED_OPPS:
        out.append({"code": "below_usage_gate", "severity": "high", "detail":
                    "Model expects %.1f opportunities — under the feed's %.1f gate, so this "
                    "was never alerted. Don't post it." % (eo, NFL_MIN_EXPECTED_OPPS)})
    if not tn:
        out.append({"code": "not_priced", "severity": "high", "detail":
                    "No row in td_edges_current.csv — no model price to quote."})
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
        print("\n## Season" + (" (%s)" % o["season_scope"] if o.get("season_scope") else ""))
        print("  " + " · ".join("%s %s" % (k, v) for k, v in s.items() if v is not None))
    ps = o.get("postseason")
    if ps:
        print("\n## Postseason (%s: %d games; %s played %d)"
              % (ps["team"], ps["team_games"], o["player"], ps["games_played"]))
        for x in ps["series"]:
            print("  %s vs %s: %s %s" % (x["round"], x["opponent"], ps["team"], x["record"]))
            for g in x["games"]:
                print("    %s · %s · %s%s" % (g["date"], g["matchup"], g["WL"],
                                            "" if g["played"] else " · DNP"))
        if ps.get("line"):
            print("  line  " + " · ".join("%s %s" % (k, v) for k, v in ps["line"].items()
                                          if v is not None))
    bb = o.get("batted_ball")
    if bb:
        print("\n## Batted ball")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in bb.items() if v is not None))
    br = o.get("baserunning")
    if br:
        print("\n## Baserunning")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in br.items()
                                if v is not None and k != "steal_dates"))
        if br.get("steal_dates"):
            print("  recent steals: %s" % ", ".join(br["steal_dates"]))

    pr = o.get("production")
    if pr:
        print("\n## Production (games)")
        for label in ("season", "starts", "last15"):
            if pr.get(label):
                print("  %-8s %s" % (label, " · ".join("%s %s" % kv for kv in pr[label].items())))
        if pr.get("start_slots"):
            print("  slots    %s (usual #%s)" % (
                " · ".join("#%s x%s" % kv for kv in pr["start_slots"].items()), pr["usual_slot"]))
        for label, v in list((pr.get("splits") or {}).items()) + list((pr.get("by_team") or {}).items()):
            print("  %-8s %s" % (label, " · ".join("%s %s" % kv for kv in v.items() if kv[1] is not None)))

    lu = o.get("lineup")
    if lu:
        print("\n## Lineup")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in lu.items() if v is not None))

    prof = o.get("profile")
    if prof:
        print("\n## Profile (as of %s)" % prof["as_of"])
        for label in ("career", "window", "platoon"):
            vals = {k: v for k, v in prof[label].items() if v is not None}
            if vals:
                print("  %-8s %s" % (label, " · ".join("%s %s" % kv for kv in vals.items())))

    for key, title in (("last_season", "Last season"), ("first_tds", "First TDs"),
                       ("depth", "Depth chart + injuries"), ("opponent", "Opponent defense")):
        v = o.get(key)
        if v:
            print("\n## %s" % title)
            print("  " + " · ".join("%s %s" % (k, x) for k, x in v.items()
                                    if x not in (None, [], "")))

    tn = o.get("tonight")
    if tn:
        print("\n## Tonight")
        print("  " + " · ".join("%s %s" % (k, v) for k, v in tn.items() if v is not None))

    rec = o.get("recent") or {}
    if rec.get("last10"):
        tagged = any(g.get("type", "REG") != "REG" for g in rec["last10"])
        print("\n## Last %d%s" % (len(rec["last10"]), " (incl. postseason)" if tagged else ""))
        for g in rec["last10"]:
            print("  " + " · ".join("%s %s" % (k, v) for k, v in g.items()))
    if rec.get("games"):
        print("\n## Last %d games (REG)" % len(rec["games"]))
        for g in rec["games"]:
            print("  " + " · ".join("%s %s" % (k, v) for k, v in g.items()))
    if rec.get("layoff"):
        print("\n## Layoff\n  %s" % json.dumps(rec["layoff"]))

    t = o.get("team")
    if t:
        print("\n## Team (%d %sgames)" % (t["games"],
                                          "%s " % t["scope"] if t.get("scope") else ""))
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
    ap.add_argument("--league", choices=("mlb", "wnba", "nfl"), help="default: try all")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today ET)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    leagues = [args.league] if args.league else ["mlb", "wnba", "nfl"]

    for lg in leagues:
        root = data_root(lg)
        if not root or not os.path.isdir(root):
            continue
        brief = {"mlb": mlb_brief, "wnba": wnba_brief, "nfl": nfl_brief}[lg](
            root, args.player, date)
        if brief:
            print(json.dumps(brief, indent=2, default=str)) if args.json else emit_text(brief)
            return
    die("no %s player matching %r (checked %s)"
        % ("/".join(l.upper() for l in leagues), args.player, date))


if __name__ == "__main__":
    main()
