# Daily Brief: Setup Guide (Mac)

This guide connects your Claude API credits to the daily brief page.
Do the parts in order. Each part ends with a check.

- **Time:** about 20 minutes.
- **Browser:** Safari or Chrome. The steps are the same in both.
- **Page address when done:** `https://cmrlmatthews-max.github.io/daily-brief/`

## Terms

- **API key** means a secret password that lets a script use your Claude API credits.
- **Repository (repo)** means a project folder on GitHub.
- **Secret** means an encrypted setting in a GitHub repository. Only the script can read it.
- **Workflow** means a script that GitHub runs for you on a schedule.

---

## Part 1. Check your credits and set a spend limit

1. Go to `https://platform.claude.com` and sign in.
2. Click your organization name at the bottom left.
3. Click **Organization settings**.
4. Click **Billing**.
5. Find your credit balance. It must show your monthly Max plan credit.
6. Scroll down to **Spend limits**.
7. Click **Set limit** (or **Adjust limit**).
8. Type `25` and click **Update limit**.

A limit of $25 is far above the expected cost of one brief per day. It stops runaway cost before it reaches your $100 credit. You can raise it later.

**Check:** The Billing page shows your credit balance and a $25 monthly limit.

> If a button name is different from this guide, look for the closest match. The Console layout can change (medium confidence in these exact names).

---

## Part 2. Create the empty repository

1. Go to `https://github.com/new`.
2. Make sure **Owner** shows `cmrlmatthews-max`.
3. In **Repository name**, type `daily-brief`.
4. Select **Public**.

> **CAUTION:** Do not add a README, a .gitignore, or a license in the next step. The repository must be empty, or my upload will conflict.

5. Leave all "Initialize" options off.
6. Click **Create repository**.

**Check:** GitHub shows a page that starts with "Quick setup". The repository is empty.

**Then:** Tell me in chat: "repo created." I will upload the code. Wait for my confirmation before Part 3.

---

## Part 3. Create the API key and store it in GitHub

> **WARNING:** The Console shows the key one time only. Do not paste the key into chat, email, notes, or any file. Paste it only into GitHub in step 13. If the key leaks, delete it in the Console and make a new one.

Do steps 1 to 14 in one session, with two browser tabs open.

**Tab A: GitHub**

1. Go to `https://github.com/cmrlmatthews-max/daily-brief`.
2. Click **Settings** (the gear tab at the top of the repository).
3. In the left menu, click **Secrets and variables**.
4. Click **Actions**.
5. Click **New repository secret**.
6. In **Name**, type `ANTHROPIC_API_KEY` exactly.

Keep this tab open.

**Tab B: Claude Console**

7. Open a new tab and go to `https://platform.claude.com`.
8. In the left menu, click **API keys**.
9. Click **Create key**.
10. In the name field, type `daily-brief`.
11. Click **Create** (or **Add**).
12. Click the copy button next to the key.

**Back to Tab A: GitHub**

13. Click in the **Secret** box and paste the key (Command + V).
14. Click **Add secret**.
15. Go back to Tab B and close the key window.

**Check:** The GitHub Secrets page lists `ANTHROPIC_API_KEY`. GitHub never shows the value again. That is correct.

---

## Part 4. Turn on GitHub Pages

1. In the repository, click **Settings**.
2. In the left menu, click **Pages**.
3. Under **Build and deployment**, find **Source**.
4. Select **GitHub Actions**.

GitHub saves this choice immediately. There is no save button.

**Check:** The Source box shows "GitHub Actions".

---

## Part 5. Run the first brief

The schedule runs every morning at 5:45 AM Eastern (4:45 AM in winter). Run it now one time to test it.

1. In the repository, click the **Actions** tab.
2. In the left list, click **Daily brief**.
3. Click **Run workflow** on the right.
4. Click the green **Run workflow** button in the menu.
5. Wait 1 to 3 minutes. Refresh the page if needed.
6. Look for a green check mark next to the run.
7. Open `https://cmrlmatthews-max.github.io/daily-brief/`.

**Check:** The page shows "Worth your attention today" with real items.

If the run shows a red X, click the run and then click **build**. Copy the red error text and send it to me.

---

## Part 6. Make it your Safari new tab page (Mac)

1. Open Safari.
2. Click **Safari** in the menu bar, then **Settings**.
3. Click the **General** tab.
4. In **Homepage**, paste `https://cmrlmatthews-max.github.io/daily-brief/`.
5. Set **New windows open with** to **Homepage**.
6. Set **New tabs open with** to **Homepage**.
7. Close the Settings window.

**Check:** Press Command + T. The brief opens.

---

## Part 7. Add it to your iPhone Home Screen

1. On the iPhone, open Safari.
2. Go to `https://cmrlmatthews-max.github.io/daily-brief/`.
3. Tap the **Share** button.
4. Tap **Add to Home Screen**.
5. Make sure the name is "Brief", then tap **Add**.

**Check:** A "B" icon is on your Home Screen. Tap it to open the brief.

---

## Part 8. Check the cost after one week

1. Go to `https://platform.claude.com`.
2. Open the **Usage** or **Cost** page.
3. Find the total for the `daily-brief` key.

Each run also records its token use at the bottom of `brief.json` on the site. Tell me the weekly total, and I will confirm the monthly estimate.

---

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Page says "Today's brief is not ready yet." | The first run did not finish, or it failed. | Do Part 5 again. Send me the error text. |
| Run fails with an authentication error. | The secret name or value is wrong. | Delete the secret. Make a new key. Do Part 3 again. |
| The brief stops updating after about two months. | GitHub turns off schedules in public repositories after 60 days without changes. GitHub sends an email first. | Open **Actions** > **Daily brief** > **Enable workflow**. Or ask me to add an automatic fix. |
| One topic is empty. | A source was down that day. | Do nothing. The footer shows "sources skipped" with the names. |
| You want different sources. | — | Edit `sources.yaml` in the repository, or ask me. |
