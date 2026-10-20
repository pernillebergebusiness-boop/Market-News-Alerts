# Market News Alerts

Your free news radar. Every 15 minutes it reads world and finance news
(NRK, BBC, CNBC, Yahoo Finance, MarketWatch, the Fed, ECB, and Google News,
which also picks up E24, DN, Finansavisen and Reuters headlines) and sends a
push notification to your phone when:

- a company on **your watchlist** is in the news (loud alert), or
- there is **big market news**: interest rates, oil, Hormuz, crashes, records,
  takeovers, profit warnings (normal alert).

At night (23:00–07:00) alerts arrive silently. If lots of news arrives at once,
you get one bundled notification instead of ten. Every alert is also saved in
`news.md`, so you can scroll back through the news log.

Cost: **0 NOK**. No AI inside. For analysis and "should I buy?", ask Claude.

---

## Setup (about 20 minutes, one time)

### 1. Install the ntfy app on your phone
- App Store → search **ntfy** → install (it's free, no account needed).
- Open it → tap **+** → for the topic name, make up a long secret name that
  nobody can guess, for example `pernille-markets-k7x92q`.
  Anyone who knows the name could read your alerts, so keep it secret.
- Write the name down. You need it in step 4.

### 2. Create a free GitHub account
- Go to github.com → **Sign up**.

### 3. Create the project and upload the files
1. On GitHub, click **+** (top right) → **New repository**.
2. Name: `market-news-alerts`.
3. Choose **Public**. Why: public projects get unlimited free running time,
   private ones only enough for a check every ~30 minutes. Nothing personal is
   in the files; your secret topic name is stored separately (step 4).
4. Tick **Add a README file** → **Create repository**.
5. Click **Add file → Upload files**, and drag in everything from the folder
   *except* the `.github` folder: `README.md`, `news_alerts.py`,
   `config.yaml`, `requirements.txt`, `news.md`, `.gitignore`, and the
   `data` folder.
   Click **Commit changes**.
6. Now add the schedule: **Add file → Create new file**. In the name box type
   exactly `.github/workflows/news-alerts.yml` (the slashes create folders).
   Paste in the content of the `news-alerts.yml` file. Click **Commit changes**.

*(Easier alternative: create the empty repository, then tell Claude its name.
Claude can upload all the files for you once GitHub is connected.)*

### 4. Add your secret topic name
1. In your repository: **Settings → Secrets and variables → Actions**.
2. **New repository secret**. Name: `NTFY_TOPIC`. Secret: your topic name
   from step 1. **Add secret**.

### 5. Allow it to save its progress
**Settings → Actions → General → Workflow permissions** → choose
**Read and write permissions** → **Save**.

### 6. Start it
**Actions** tab → if asked, click **I understand my workflows, go ahead and
enable them** → click **Market news alerts** → **Run workflow**.

Within a minute your phone should show **"Market News Alerts is live ✅"**.
From then on it runs by itself every 15 minutes.

---

## Changing what you get alerts about

Open `config.yaml` on GitHub → click the pencil ✏️ → edit → **Commit changes**.

- **Bought a stock?** Add it to `watchlist`, copying the layout:
  ```yaml
    - name: Kitron
      ticker: KIT
      match: [Kitron]
  ```
- **Too many alerts?** Remove broad words from `important_keywords`, or remove
  very newsy companies (Microsoft, Tesla) from the watchlist.
- **Nothing about football, please:** add words to `ignore_keywords`.
- **Different night hours:** change `quiet_hours_start` / `quiet_hours_end`.

## Good to know
- GitHub sometimes starts a check a few minutes late. That's normal.
- If a news source stops working, the others carry on. Problems are listed at
  the bottom of each run's log in the **Actions** tab ("Feed problem").
- GitHub may pause schedules on projects with no activity for 60 days. This
  project saves its progress regularly, so that shouldn't happen. If GitHub
  ever emails you about it, just click to re-enable.
- This tool shows news, not advice. You make the final call on every trade.
