# AppFollow Batch Workflow

You can only add 5 advertisers to AppFollow workspace at a time. Process them in batches:

## Batch 1: Advertisers 1-5

**Step 1:** Manually add these 5 to your AppFollow workspace:
1. Chime (+ 3 competitors: Current, Dave, MoneyLion)
2. Binance (+ 3 competitors: Coinbase, Kraken, eToro)
3. Albert (+ 3 competitors: Cleo, MoneyLion, Dave)
4. Coinbase (+ 3 competitors: Binance, Kraken, eToro)
5. eToro (+ 3 competitors: NAGA, Public, ZuluTrade)

**Step 2:** Run discovery to extract itemIds:
```bash
python scripts/appfollow_discover_itemids.py
```
This auto-updates `config/appfollow_groups.yaml` with the discovered itemIds.

**Step 3:** Collect reviews:
```bash
bash scripts/run_appfollow_to_server.sh
```

---

## Batch 2: Advertisers 6-10

**Step 1:** Manually add these 5 (after removing batch 1 from workspace):
6. Travel Town (+ 3 competitors: Gossip Harbor, Seaside Escape, Merge Mansion)
7. Pokemon GO (+ 3 competitors: Monster Hunter Now, Jurassic World Alive, Pikmin Bloom)
8. Royal Match (+ 3 competitors: Travel Town, MonopolyGo, Mistplay)
9. MoneyLion (+ 3 competitors: Chime, Dave, Albert)
10. Kraken (+ 3 competitors: Coinbase, Binance, Gemini)

**Step 2-3:** Same as above.

---

## Full Advertiser List (24 total, 4 batches of 5-6)

| Batch | #  | Advertiser       | Competitors (3 each)                         |
|-------|----|-----------------|--------------------------------------------|
| 1     | 1  | Chime           | Current, Dave, MoneyLion                   |
|       | 2  | Binance         | Coinbase, Kraken, eToro                    |
|       | 3  | Albert          | Cleo, MoneyLion, Dave                      |
|       | 4  | Coinbase        | Binance, Kraken, eToro                     |
|       | 5  | eToro           | NAGA, Public, ZuluTrade                    |
| 2     | 6  | Travel Town     | Gossip Harbor, Seaside Escape, Merge Mansion |
|       | 7  | Pokemon GO      | Monster Hunter Now, Jurassic World Alive, Pikmin Bloom |
|       | 8  | Royal Match     | Travel Town, MonopolyGo, Mistplay          |
|       | 9  | MoneyLion       | Chime, Dave, Albert                        |
|       | 10 | Kraken          | Coinbase, Binance, Gemini                  |
| 3     | 11 | Current         | Chime, Varo, Step                          |
|       | 12 | Dave            | EarnIn, Brigit, Tilt                       |
|       | 13 | Koho            | Wealthsimple, Neo Financial, PC Financial  |
|       | 14 | Mistplay        | JustPlay, Rewarded Play, Swagbucks          |
|       | 15 | MonopolyGo      | Coin Master, Board Kings, Dice Dreams       |
| 4     | 16 | Possible Finance| EarnIn, Brigit, OppLoans                   |
|       | 17 | Realtor         | Zillow, Redfin, Trulia                     |
|       | 18 | ScrabbleGo      | Words With Friends 2, Wordscapes, Wordle   |
|       | 19 | Shopback        | Rakuten, Honey, Ibotta                     |
|       | 20 | Stash           | Acorns, Robinhood, Webull                  |
|       | 21 | Tilt            | EarnIn, Cleo, Dave                         |
|       | 22 | Upside          | GasBuddy, Checkout 51, Ibotta              |
|       | 23 | swagbucks       | InboxDollars, MyPoints, Freecash            |
|       | 24 | testerup        | Mistplay, Freecash, Swagbucks               |
