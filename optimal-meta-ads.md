---
description: Meta Ads command center — campaign + creative analysis, budget recommendations, geographic splits
allowed-tools: [Bash, Read, Write, Edit]
---

# Meta Ads Command Center

Full campaign + creative review. Answers: "Which campaigns and creatives should I scale or cut, and to what exact budget?"

## Credentials
- **Ad Account ID**: `act_935421465810600`
- **Access Token**: Check in this order:
  1. If the user passed a token as an argument to this command, use it directly
  2. Otherwise use env var `$META_ACCESS_TOKEN`
  3. If neither exists or error 190 (expired): tell user to refresh from https://developers.facebook.com/tools/explorer/ and pass as arg

## Steps

### 1. Read Baselines + Learnings
Read `.claude/docs/baselines.md` for:
- Breakeven CPI (attributed and blended)
- Organic multiplier
- Rev/download
- Trial-to-paid rate
- Trial start rate (to derive breakeven CPT)

Read `.claude/docs/learnings.md` for:
- Meta attribution gap (Meta under-reports trials — use this to adjust CPT breakeven)
- Organic multiplier confidence + stale risk
- Any campaign-type or geo learnings from prior runs

**Derived metrics:**
- Breakeven CPT (raw) = breakeven CPI / trial start rate (from baselines)
- Breakeven CPT (adjusted) = raw breakeven CPT / (1 - attribution gap) (from learnings — accounts for Meta under-counting trials)
- If learnings.md has no attribution gap data, use raw breakeven CPT and flag "needs AppsFlyer calibration"

Read `.claude/memory/dashboard.md` for prior week's Meta totals (for WoW if 14d data is incomplete).

Do NOT hardcode any metric values.

### 2. Fetch Data (4 calls — run all in parallel)

Use the resolved access token in place of ACCESS_TOKEN below.

**Call 1: Campaign totals (14d daily breakdown)**
Gives us both current and prior week in one call, plus daily trend for anomaly detection.
```bash
curl -s -G "https://graph.facebook.com/v22.0/act_935421465810600/insights" \
  --data-urlencode "access_token=ACCESS_TOKEN" \
  --data-urlencode "level=campaign" \
  --data-urlencode "fields=campaign_id,campaign_name,spend,impressions,clicks,cpc,ctr,frequency,actions,cost_per_action_type" \
  --data-urlencode "date_preset=last_14d" \
  --data-urlencode "time_increment=1" \
  --data-urlencode "limit=1000" \
  --data-urlencode 'filtering=[{"field":"campaign.delivery_info","operator":"IN","value":["active","recently_completed"]}]'
```

**Call 2: Ad-level creative data (last 7d)**
Shows which specific creatives are working or fatigued within each campaign.
```bash
curl -s -G "https://graph.facebook.com/v22.0/act_935421465810600/insights" \
  --data-urlencode "access_token=ACCESS_TOKEN" \
  --data-urlencode "level=ad" \
  --data-urlencode "fields=ad_id,ad_name,campaign_name,spend,impressions,clicks,ctr,frequency,actions,cost_per_action_type" \
  --data-urlencode "date_preset=last_7d" \
  --data-urlencode "limit=500" \
  --data-urlencode 'filtering=[{"field":"campaign.delivery_info","operator":"IN","value":["active","recently_completed"]}]'
```

**Call 3: Campaign budgets + status**
Needed for specific budget recommendations.
```bash
curl -s -G "https://graph.facebook.com/v22.0/act_935421465810600/campaigns" \
  --data-urlencode "access_token=ACCESS_TOKEN" \
  --data-urlencode "fields=id,name,daily_budget,lifetime_budget,budget_remaining,status,objective,start_time" \
  --data-urlencode "limit=100" \
  --data-urlencode 'filtering=[{"field":"effective_status","operator":"IN","value":["ACTIVE","PAUSED"]}]'
```

**Call 4: Geographic breakdown (last 7d)**
Shows which countries are profitable within each campaign.
```bash
curl -s -G "https://graph.facebook.com/v22.0/act_935421465810600/insights" \
  --data-urlencode "access_token=ACCESS_TOKEN" \
  --data-urlencode "level=campaign" \
  --data-urlencode "fields=campaign_id,campaign_name,spend,impressions,actions,cost_per_action_type" \
  --data-urlencode "breakdowns=country" \
  --data-urlencode "date_preset=last_7d" \
  --data-urlencode "limit=500" \
  --data-urlencode 'filtering=[{"field":"campaign.delivery_info","operator":"IN","value":["active","recently_completed"]}]'
```

### 3. Analyze

**Note**: This skill uses the organic multiplier and Meta attribution gap from `learnings.md` (calibrated by `/optimal-subs` via AppsFlyer). If those values seem stale, run `/optimal-subs` first to refresh them.

#### 3a. Campaign Performance (from Call 1)

Split the 14d daily data into two 7d windows (days 1-7 = prior week, days 8-14 = current week).

**Per campaign, current 7d:**
- **Installs**: `omni_app_install` from `actions`, fall back to `mobile_app_install`. If neither, installs = 0.
- **Trial starts**: `omni_purchase` from `actions` (Meta SDK fires trial starts as in-app purchases). Fall back to `purchase` or `app_custom_event.fb_mobile_purchase`. This is what Meta calls "Results" / "In-app purchases" in Ads Manager.
- **Cost Per Trial (CPT)**: From `cost_per_action_type` for `omni_purchase`. This is the PRIMARY decision metric — it's what Meta optimizes for and what directly predicts revenue.
- **Install-to-Trial Rate**: trial starts / installs. Measures traffic quality. Higher = better intent users.
- **Attributed CPI**: From `cost_per_action_type` for `omni_app_install`.
- **Blended CPI**: Attributed CPI / organic multiplier (from baselines.md)
- **vs Breakeven**: Compare CPT to breakeven CPT (derived in step 1). Also compare blended CPI to blended breakeven.
- **WoW**: Compare current 7d totals to prior 7d totals (spend, installs, trial starts, CPT, CPI)

**Campaign age**: Calculate days since `start_time` (from Call 3). If <7 days, mark as LEARNING.

**IMPORTANT**: CPT is the primary ranking metric. A campaign with high CPI but low CPT is actually good — it's getting expensive installs that convert. A campaign with low CPI but high CPT (or zero trials) is junk traffic.

#### 3b. Daily Trend & Anomaly Detection (from Call 1)

- Sum total spend, installs, and trial starts per day across all campaigns
- Calculate daily averages
- Flag any day with spend >50% above or below average → possible outage or overspend
- Track daily CPT trend alongside CPI trend
- Detect **diminishing returns**: if CPT is worsening for 3+ consecutive days while spend increases, flag as SATURATING
- Detect **quality drop**: if install-to-trial rate drops >10pp from weekly average on any day

#### 3c. Spend Pacing (from Call 1 + Call 3)

For each campaign:
- Current daily budget (from Call 3, convert from cents to dollars — Meta returns budget in cents)
- Actual avg daily spend (from Call 1, current 7d spend / 7)
- Pacing ratio = actual / budget
  - <0.7 = UNDER-DELIVERING (audience exhaustion or bid too low)
  - 0.7-1.1 = HEALTHY
  - >1.1 = OVER-DELIVERING (Meta spending more than budget — possible bug/outage)

#### 3d. Creative Analysis (from Call 2)

**Per ad within each campaign:**
- Trial starts (`omni_purchase`), installs, spend, CPT, CPI, CTR, frequency, install-to-trial rate
- Rank ads within each campaign by CPT (primary) then blended CPI (secondary)
- Flag creatives where:
  - frequency > 3.0 → FATIGUED (audience has seen it too many times)
  - CTR < 0.5% → DEAD CREATIVE
  - CPT > 2x campaign average → UNDERPERFORMER (killing the campaign's average)
  - Zero trial starts + spend > $10 → ZERO-CONVERT (getting installs that never trial)
  - High installs but zero trials → JUNK TRAFFIC (the creative attracts clickers, not buyers)

**Key insight**: A campaign can look bad at the campaign level but have one great creative dragged down by others. Identify winners to keep and losers to kill. A creative with expensive installs but cheap trial starts is actually your best performer.

#### 3e. Geographic Breakdown (from Call 4)

For each campaign with spend in multiple countries:
- Trial starts, installs, CPT, CPI by country
- Install-to-trial rate by country (reveals geo-specific intent differences)
- Flag countries over/under breakeven CPT
- If a campaign is mixed-geo and one country is profitable while another isn't → recommend splitting into separate campaigns or excluding the bad geo

#### 3f. Budget Recommendations (from Call 1 + Call 3)

For each campaign, use daily data to find the profitable spend level:
- Group the 7 days by spend level (low/medium/high)
- Calculate CPT at each level (primary), CPI as secondary
- If CPT is better on low-spend days → the campaign has diminishing returns at scale
- Recommend: set daily budget to the level where CPT was at or below breakeven CPT
- Express as a specific dollar amount: "Set daily budget from $X to $Y"

If a campaign is consistently under breakeven CPT at all spend levels → recommend scaling (increase budget by 30-50%).
If a campaign is consistently over breakeven CPT at all spend levels → recommend killing.
If a campaign has zero trial starts → KILL regardless of CPI (junk traffic).

### 4. Update Dashboard
Write the "Ads > Meta" section of `.claude/memory/dashboard.md`.

### 5. Present

```
## Meta Ads Command Center — [Today's Date]

### Overview
| Metric | This Week | Prior Week | WoW |
|--------|-----------|------------|-----|
| Spend | $X,XXX | $X,XXX | +/-XX% |
| Trial starts | XXX | XXX | +/-XX% |
| Installs | XXX | XXX | +/-XX% |
| **Cost Per Trial (CPT)** | **$X.XX** | **$X.XX** | **+/-XX%** |
| vs Breakeven CPT (adjusted) | X.XXx OVER/UNDER | | |
| Attributed CPI | $X.XX | $X.XX | +/-XX% |
| Blended CPI (÷ X.XXx multiplier) | $X.XX | $X.XX | +/-XX% |
| vs Breakeven CPI (blended) | X.XXx OVER/UNDER | | |
| Install-to-Trial Rate | XX.X% | XX.X% | |
| Active campaigns | X | | |
| Organic multiplier source | learnings.md (measured [date]) | | |

### Daily Trend (14d)
| Date | Spend | Installs | Trials | CPT | CPI | I→T Rate | Flag |
|------|-------|----------|--------|-----|-----|----------|------|
(14 rows — flag anomalies, quality drops, mark current vs prior week)

[Call out: diminishing returns, outages, spend spikes]

### Campaign Ranking (Best → Worst by CPT)
| # | Campaign | Age | Budget | Spend | Trials | Installs | CPT | vs BE CPT | I→T | Attr CPI | Blended CPI | vs BE CPI | Action |
|---|----------|-----|--------|-------|--------|----------|-----|-----------|-----|----------|-------------|-----------|--------|
(Ranked by CPT. Show BOTH attributed and blended CPI. Flag zero-trial, LEARNING, SATURATING, JUNK TRAFFIC campaigns)

### Budget Recommendations
| Campaign | Current Budget | Recommended | Why |
|----------|---------------|-------------|-----|
(Specific dollar amounts for each campaign. Include "KILL" for campaigns to turn off.)

### Creative Winners & Losers (top campaigns only)
For each campaign with >$100 spend:
| Campaign | Ad | Spend | Trials | Installs | CPT | I→T Rate | CTR | Freq | Verdict |
|----------|-----|-------|--------|----------|-----|----------|-----|------|---------|
(Show top 3 best + worst creatives per campaign. Flag FATIGUED, DEAD, ZERO-CONVERT, JUNK TRAFFIC.)

[Call out: "Campaign X has 1 winner ($X.XX CPT, 45% I→T) dragged down by 2 junk creatives (0 trials). Kill [ad names], keep [ad name]."]

### Geographic Splits
| Campaign | Country | Spend | Trials | Installs | CPT | I→T Rate | vs BE CPT | Action |
|----------|---------|-------|--------|----------|-----|----------|-----------|--------|
(Only show campaigns with multi-country spend. Flag profitable vs unprofitable geos by CPT.)

### Pacing Issues
| Campaign | Budget | Actual | Pacing | Issue |
|----------|--------|--------|--------|-------|
(Only show campaigns with pacing <0.7 or >1.1)

### Actions Summary
- **KILL TODAY**: [campaigns/creatives to turn off immediately, with $ saved/day]
- **SET BUDGET**: [specific budget changes: "Campaign X: $200/day → $100/day"]
- **KILL CREATIVES**: [specific ads to turn off within otherwise good campaigns]
- **SCALE**: [campaigns/geos to increase, with new budget]
- **WATCH**: [campaigns <7 days old or borderline]

### Estimated Weekly Impact
| Action | Savings/Week | Trial Change | Install Change |
|--------|-------------|--------------|----------------|
| Kill [campaigns] | +$XXX | -XX trials | -XX installs |
| Cut [campaigns] | +$XXX | -XX trials | -XX installs |
| Scale [campaigns] | -$XXX | +XX trials | +XX installs |
| **Net** | **+/-$XXX** | **+/-XX trials** | **+/-XX installs** |
| **New CPT** | **$X.XX** | | |
| **New blended CPI** | **$X.XX** | | |
```

## Notes
- Do NOT hardcode breakeven CPI, CPT, organic multiplier, or any metric values — always read/derive from baselines.md
- **CPT is the primary metric**, CPI is secondary. Rank and decide by CPT.
- `omni_purchase` in Meta's API = "In-app purchases" in Ads Manager = trial starts in the app (free trial fires as IAP)
- Breakeven CPT = breakeven CPI / trial start rate (both from baselines.md)
- Meta returns `daily_budget` and `lifetime_budget` in CENTS — divide by 100 for dollars
- Campaign age <7 days = LEARNING phase — recommend WATCH not CUT
- Diminishing returns = CPT worsening for 3+ consecutive days while spend rises
- One great creative can save a bad campaign — always check ad-level before killing a whole campaign
- A campaign with high CPI but low CPT is GOOD — expensive installs that all convert
- A campaign with low CPI but high/no CPT is BAD — cheap junk traffic that never trials
