# How Do We Predict Home Runs? Part 1 of ...

### Pitch Data

Home runs are the most exciting part of baseball, imho, and they don't happen very often — right around 3% of plate appearances. They're a pretty rare event, so figuring out whether a player is going to hit one is a little bit like finding that one lego brick you need in your bin of unsorted bricks.

We know home runs are at least a teensy bit predictable. Some players are going to hit a lot more home runs than others, year in and year out, and some players are hardly going to hit any. Same story on the mound: some pitchers give up way more homers than others. We know homers are more common at higher elevations (thanks Colorado!), on warmer days, in parks with shorter fences, and when the wind blows out from home plate.

But...how do you actually turn any of that into a prediction?

(That question is this whole series — the data, the models, the edges, the alerts, and the app. It will get long and nerdy. That's the fun part.)

It starts with data! Statcast publishes 119 columns about every single MLB pitch — velocity, spin, release point, exit velocity, launch angle, all the way down to the tilt of the batter's swing path. We mirror all of it, roughly 750,000 pitches per season going back to 2017, and then build our own layers on top: batter profiles (career numbers AND the trailing 40 games, with platoon splits and performance against different pitch types), pitcher profiles (arsenal, velocity, workload, recent form), park factors, fence distances in every direction (including the batter's pull side), elevation, roofs, temperature, wind. Plus team tendencies, like how quickly each club goes to its bench. Plus game-state stuff — score, outs, count, runners — which we'll get into in Part 2.

Rookies get seeded with minor-league data, and the translation is not gentle: MiLB power gets marked DOWN on the way up (a AAA dinger is worth about 80% of an MLB one in our priors), strikeout rates get marked up, and lower levels count for less than AAA. Sorry, rooks. And every rate in every profile gets empirical-Bayes shrinkage, which is a fancy way of saying small samples get dragged toward league average until they earn their distance. One hot week does not make you Aaron Judge. Yet.

All told, our models use around 145 features per pitch (145 to 154, depending on the model), which is way more information than I can juggle in my brain at any point in time, let alone for every pitch of every game on a full slate. Fortunately, that's where the ML models come in.

We use a chain of models — boosted decision trees, trained on about six million pitches — to predict what happens on each pitch, one question at a time:

1. **What's he throwing?** Statcast tags pitches with 18 different type codes; we collapse them into six classes (fastball, sinker, cutter, breaking, offspeed, other) because for our purposes the coarse distinctions are the ones that matter. And they DO matter: in 2024, balls in play against four-seamers became homers about 5.3% of the time, versus 3.2% against sinkers.
2. **Does he swing?** Given the pitch class, the count, and everything else about the matchup.
3. **What happens on the swing?** Whiff, foul, or fair contact — with a sibling model handling the takes: ball, called strike, or the occasional plunking.
4. **What happens on contact?** Single, double, triple, home run, or out, priced off the batter's power, the pitcher, the park, and the weather.

(Fun aside: none of these models ever sees pitch *location*, on purpose. Our simulator only decides WHAT gets thrown, not where, and if you hand location-hungry models a filled-in average location, every simulated pitch becomes a down-the-middle meatball. Ask us how we know.)

Sharp-eyed readers might notice there's no ball-physics step in that list. There used to be! We had models that predicted exit velocity and launch angle off the bat, then turned the physics into outcomes, and they were beautiful. Then we tested them head-to-head against the boring direct approach over a full season, and the boring approach was a hair more accurate and literally twice as fast. So the physics models got benched — they still hang around as diagnostics, but they don't touch the predictions anymore. The number of models drifts between five and seven as we experiment (RIP to the physics arm), and the current starting five is the list above.

Accuracy is what everybody wants to talk about with models, and ours do pretty well for predicting the outcomes of hundreds of interactions between groups of human beings. On the question that matters — does this batter homer in this game — our AUC runs in the .6 to .7 range across backtests and live seasons (this season: .61 through late July, across about 7,000 player-games), with Brier scores around .10 and calibration error around a single point of probability. Translated from nerd: the model is meaningfully better than naive guessing at separating homer games from no-homer games, and when it says 12%, homers happen about 12% of the time. In this business, that second part is the superpower.

Chains of models can be REALLY useful (it's the same trick behind our first-basket models [TK: link to first-baskets series]), but they undeniably add complexity, and they add the risk of compounding errors: mess something up in the first model and it flows downstream through everything else, because each model takes the previous outputs as inputs. Are they worth it?

Our research says yes. You can absolutely estimate a batter's probability of homering with the simplest possible method: count his games, count his homer games, divide. If Jim played 100 games and homered in 10 of them, call it 10% (+900 in American odds). Totally intuitive, easy to update, and genuinely a lot better than flipping a coin. But the chained approach beats that baseline by roughly +.02 to +.04 of AUC, season after season, in leak-free backtests. That sounds modest, so here's the honest version: home runs are HARD, nobody's model sees the future, and a couple points of AUC is the difference between finding real edges and donating vig to the books. The complexity earns its keep. (One of our favorite findings along the way: home runs are overwhelmingly a *batter* skill. A pitcher's homer-allowed history carries almost no predictive weight — that's not a bug in our models, it's a fact about baseball.)

So that's cool — we can price individual pitches. But the bets are on games, and games have STATE. That's Part 2 of ..., where we get into score effects, lineups, and the simulations that hold this whole thing together. Thanks for reading!

---

# How Do We Predict Home Runs? Part 2 of ...

### Game-State Data

Everything in Part 1 [TK: link] is stuff we know before first pitch, and you could stop there: throw the pregame features into a model and get a decent baseline probability that a player goes deep. But pitches don't happen in a vacuum, they happen inside games. Pitchers change their approach when they're protecting a big lead or chasing from behind, when they're ahead or behind in the count, when there are runners in scoring position. Batters do the same! With two strikes, the pitch mix tilts hard toward breaking and offspeed stuff — and as we covered in Part 1, breaking balls become dingers a lot less often than heaters do. Our models see all of it: balls, strikes, outs, all 24 combinations of runners and outs, the inning, the score difference, times through the order, home/away, and the handedness matchup.

And then there's the obvious one: a player's chances of homering in a game rise and fall with his plate appearances. So how a team moves through its lineup — and how it handles substitutions, for defense or pinch-hitting or pinch-running — matters a LOT. By the time the lineup turns over a third time, the 9-hole hitter is about three times as likely as the leadoff man to have been lifted, and some clubs go to their bench much faster than others. Our sim tracks per-slot survival odds and per-team pinch-hit tendencies for exactly this reason.

Given all that, it should be pretty clear: the state of the game is a real predictor of whether an at-bat produces a homer, and the state changes on every pitch. So how do you account for game states before the game even starts? SIMULATIONS.

But first, a confession: we don't actually roll dice pitch by pitch. For every batter-pitcher matchup, in every context, we solve the plate appearance EXACTLY. There are only 12 possible counts and six pitch classes, so a plate appearance is really a little 72-cell grid: push probability through every path — every pitch choice, every swing and take, every foul — until all of it has been absorbed into an outcome. Foul balls with two strikes just loop the count back on itself, and the math is perfectly happy to price the at-bat that takes a dozen foul balls to resolve. Out the other side comes an exact distribution for that matchup: strikeout, walk, hit-by-pitch, single, double, triple, homer, out. No randomness, no simulation noise, just probability doing its job.

THEN we roll dice. We simulate each game 20,000 times, plate appearance by plate appearance, through a full game-state machine: lineups turning over, starters running out of gas, bullpens, pinch hitters, baserunners doing baserunner things (the sim knows a runner on second scores on a single about 60% of the time). The endings are score-aware, too: a home team that's leading skips the bottom of the ninth, walk-offs end innings mid-stream, and ties go to extras with the free runner on second (yes, the Manfred runner lives in our simulator; no, he cannot be traded). Getting the endings right isn't just cosmetic — it fixed a systematic ~5% inflation in home hitters' projections, since home teams bat in the ninth a lot less often than a lazy sim assumes.

After 20,000 sims we can answer questions like, "in what percentage of sims for the Phillies-Dodgers game did Ohtani go yard?" That proportion is our probability — the number that eventually becomes a projection, an edge, and maybe an alert. Why 20,000? Because at a 10% homer probability, that's enough sims to shrink the random noise to about ±0.2 points — and in our testing, extra sims sharpen the PRICES, not the model. Backtest accuracy is flat whether we run 2,000 or 20,000. Precision is for the odds; accuracy was decided back in Part 1. (Also: everything is seeded, so every run is perfectly reproducible. The seed is 2026. Because of course it is.)

Now, simulating every game 20,000 times, then re-simulating all day as lineups post and weather shifts, sounds computationally expensive. Three tricks keep it manageable:

1. **Solve once, sample cheap.** The expensive part is the exact plate-appearance math — about three seconds of thinking per game. After that, each additional simulated game costs six *hundredths* of a millisecond.
2. **Simulate wide.** Instead of looping through 20,000 games one at a time, we advance all 20,000 together, one plate appearance per step, as big numpy arrays. The slow part of the code scales with the length of a game, not the number of sims.
3. **Fingerprints.** Every game's projection carries a signature of its inputs: lineups, starters, weather (bucketed into 3°F and 2 mph steps, so ordinary forecast wobble doesn't count as news). New update, same signature? Skip the re-run. And once a game goes live, its pregame projection freezes for good.

Add it up and a full slate re-scores in about the time it takes to microwave a burrito, on an old i7 with 16GB of RAM. Still just a couple of guys on laptops over here. V nifty.

One last step before the numbers are ready for prime time: calibration. An assembled simulator carries small compounding biases — ours ran a touch hot on balls in play, and its generic bullpens were a touch friendlier to hitters than real ones — so a final calibration layer, fit on held-out data, trues everything up. It stretches the tiniest probabilities upward (a raw 2% becomes about 4%) and trims the biggest ones (a raw 20% becomes about 17%). The goal is simple: when we publish 12%, we mean 12%.

So now we've got a calibrated probability that any batter in baseball goes deep tonight. A probability is not a bet, though. In Part 3 of ... we turn projections into edges. Thanks for reading!

---

# How Do We Predict Home Runs? Part 3 of ...

### From Projections to Edges

Being able to put a fair price on every potential dinger in baseball is a nifty parlor trick, and it might win you an argument at the bar. But unless those prices find us spots where the books disagree with us, they're just trivia. Fortunately, they do. Here's how.

Twice an hour, we collect home run odds from 13 sportsbooks: bet365, BetMGM, BetRivers, Caesars, Circa, DraftKings, Fanatics, FanDuel, Hard Rock, Kalshi, Novig, ProphetX, and theScore. Every American price converts to an implied probability, and then the comparison is almost embarrassingly simple:

**edge = our probability − the book's implied probability**

Say our calibrated projection has a batter at 14% to go deep — a fair price of +614 — and a book is dangling +900, which implies 10%. That's a 4-point edge. Play.

Two details we're picky about. First, we compare against the book's price exactly as posted — no de-vigging, no theoretical "true" line. The number we beat is the number you can actually bet, juice included. Second, not every positive edge is playable. The bar right now: 2 points of probability for 1+ home run plays, 1.5 points for the multi-homer and first-inning markets. It used to be lower. We started this season at 1 point and raised it twice, because our own tracking data was blunt with us: thin edges on 1+ homers were about a third of our volume and returned roughly nothing. We'd rather alert fewer, better plays. The thresholds will keep evolving, and the tracking data — not vibes — decides.

(For the 2+ and 3+ homer markets, we take the sim's expected homers for each batter and run it through a little Poisson math to get the tail probabilities. Longshot city, priced with the same machinery.)

Then there's sizing. Recommendations are half-Kelly, where 1 unit = 1% of a bankroll, scaled so the average alerted play lands right around 1u. Bigger edges and longer odds earn bigger recommendations, and nothing ever earns a "max bet!!". Why HALF Kelly? Because full Kelly assumes your probabilities are perfect, and ours are merely good 😎

And finally, line shopping — the closest thing this industry has to free money. The same homer can be +600 at one book and +900 at another, and that's the difference between implied 14.3% and implied 10% on the same swing of the same bat. Our alerts list every book clearing the bar, best value first, so you can grab the top of the market. (One exception: exchange-style books like Novig and Kalshi show up in our tables but not our official plays. Prices there move too fast, and the resting liquidity is too thin to promise the posted number is actually gettable.)

A word about variance, because we'd rather say it up front than have you learn it the hard way: a 3-point edge on a +700 prop still loses most of the time. That is the shape of this business — many small positions on longshots, graded over months, not nights. We track every alerted play publicly, wins and losses both, and those tracked results are exactly what moved the thresholds above. No cherry-picking, no memory-holing the bad weeks.

Of course, an edge you hear about after first pitch is worth exactly nothing. Part 4 of ... covers the last mile: alerts, and the app. Thanks for reading!

---

# How Do We Predict Home Runs? Part 4 of ...

### Alert Alert, Home Run Edition

If you read our first-baskets series, you already know we're obsessive about the last mile [TK: link]. A projection that never reaches you, or reaches you stale, might as well not exist. So the whole system runs like a tiny newspaper with an extremely niche beat.

Every Monday at 2am, the models retrain on everything through the day before. Every morning at 6am, a full refresh: schedules, rosters, park factors, yesterday's plays graded and logged. And then every 30 minutes from 7am to 10pm ET, a tick: pull the newest pitch data, check for posted lineups and probable starters, grab a fresh hourly forecast for every ballpark, re-simulate every game whose inputs actually changed (the fingerprint trick from Part 2 [TK: link]), re-price 13 books, and push alerts.

Weather gets the full nerd treatment, by the way. Wind "blowing out" isn't a vibe — every forecast is resolved against the actual compass bearing from home plate to center field in that specific park, so a gust that's blowing out in one stadium is a crosswind in another. Domes are a permanent 72°F and windless, which is boring, but boring is easy to model. Retractable roofs get a per-game open-or-closed call.

Alerts land in Discord looking like this:

> **LAA @ TEX (8:05 pm ET)**
> - Josh Lowe 1+ (proj +519): +750 br (2.5u), +720 dk (2.3u), ...
> - Jorge Soler 2+ (proj +545): +600 br (0.7u)

Games in start-time order, plays sorted by edge, every qualifying book listed with its price and recommended units, best value first. And the emoji do actual work:

- 🚨 first time a play qualifies today
- 🚀 new day-high in value — the market moved further from us
- ⬇️ still playable, but off its peak
- 📖 a new book just joined the party

The high-water marks reset at midnight ET, so a 🚀 is always a genuinely new best — a play that faded and crawled back to a price it already hit today gets a ⬇️, never a fresh rocket.

And when a game goes live, we stop. Projections freeze at first pitch — no in-game re-pricing, no chasing. Pregame edges are our lane, and we stay in it.

If Discord isn't your speed, the same numbers live in the Slam Dunk Dashboard at [app.slamdunk.bet](https://app.slamdunk.bet): one card per game, with the weather, projected lineups, first-inning odds, and per-batter home run odds. And because we archive the full edge table on every single odds refresh, the app can show you how prices moved through the day — when the books adjusted, which direction, and whether the value is growing or already got eaten.

And that's the machine: 119 columns on every pitch, six million pitches of training data, a chain of models, an exact little 72-cell plate-appearance solve, 20,000 simulations per game, a calibration layer, 13 books twice an hour, half-Kelly sizing, and a rocket emoji when it matters. From data to dingers, we've covered the bases. For real this time — it's baseball. The bases are literal.

Thanks as always for reading, holler if you have questions ([@jimtheflash](https://x.com/jimtheflash) is the best way to find us), and if you want these alerts in your pocket, please consider [subscribing](https://sharpduel.com/slam_dunk_bets) — first month's free — or come hang out in the [Discord](https://whop.com/slam-dunk-bets) 🙏
