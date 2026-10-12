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

The main morning run is the 5:00 AM iPhone Shortcut (Part 9). GitHub's schedule is a backup at 6:47 AM Eastern (5:47 AM in winter). It builds only if no paper exists for today. Run it now one time to test it.

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

---

## Part 9. The Print button and the morning run

The button named Print a New Edition is at the bottom of the page. The button runs an Apple Shortcut. An Apple Shortcut is a saved list of actions. An action is one step that the Shortcut runs.

The Shortcut must have this exact name: Print Daily Brief. The Shortcut tells GitHub to build a new paper. To build a paper means to create a new edition of the brief.

The same Shortcut can run automatically each morning. GitHub's own schedule remains turned on as a backup at 6:47 AM. It builds only if no paper exists for today. The button always builds.

**Step A. Make a GitHub token (fine-grained personal access token)**

A token is a secret password. It gives a program limited access to your GitHub account. This step makes a fine-grained personal access token.

1. On github.com, open Settings > Developer settings > Personal access tokens > Fine-grained tokens. Tap "Generate new token".
2. In the Token name box, type Print Daily Brief.
3. In Expiration, select the longest time that GitHub offers. Create a calendar reminder for the expiration date.
4. Under Repository access, select "Only select repositories". Select only the daily-brief repository.
5. Open Permissions > Repository permissions > Actions. Select "Read and write". Leave all other permissions set to "No access".

> **CAUTION:** Do not put this token in the repository, in a chat, or in a screenshot. A run is one execution of the workflow. Anyone with the token can start a run. Each run spends your API credit. One run costs about $0.10.

6. Tap "Generate token". Copy the token. GitHub shows the token one time only.

**Step B. Build the Shortcut (iPhone; it syncs to Mac with iCloud)**

Use an iPhone for this step. iCloud syncs the Shortcut to your Mac. A URL is a web address. A request is a message that the Shortcut sends to GitHub. A header is one line of extra information in a request.

1. Open the Shortcuts app. Tap +.
2. Name the Shortcut exactly: Print Daily Brief. The page uses this name.
3. Add the action Get Contents of URL.
4. In the URL box, type this URL: `https://api.github.com/repos/cmrlmatthews-max/daily-brief/actions/workflows/daily-brief.yml/dispatches`
5. Tap the arrow to show more options. Set Method to POST. The POST method sends data to GitHub.
6. Add three headers:
   - `Authorization = Bearer YOUR_TOKEN`
   - `Accept = application/vnd.github+json`
   - `X-GitHub-Api-Version = 2022-11-28`

   Replace YOUR_TOKEN with the token from Step A. Bearer is a label. It tells GitHub that the value is a token.
7. Set Request Body to JSON. JSON is a text format for structured data. Add one Text field. Set its key to ref. Set its value to main.
8. This step is optional. Add the action Show Notification. Set its text to "Printing a new edition".
9. Tap Done.

**Test:** Tap the Shortcut. After about 2 minutes, reload the page. The Printed time at the bottom of the page changes. If nothing changes, open github.com/cmrlmatthews-max/daily-brief/actions. Look at the newest run.

**Step C. Run the Shortcut each morning**

An automation is a Shortcut that starts automatically. Use an automation to run the Shortcut each morning.

1. In the Shortcuts app, tap Automation. Then tap +.
2. Choose Time of Day. Set the time to 5:00 AM. Set Repeat to Daily.
3. Choose Run Immediately. If you do not want a notice, turn off Notify When Run.
4. Tap Next. Choose Print Daily Brief. Tap Done.

**Note:** The phone must be on and connected to the internet at 5:00 AM. The phone can stay locked.

**Step D. Use the button**

Tap "Print a New Edition" at the bottom of the page. The Shortcuts app opens and runs the Shortcut. Safari opens the page again. The page shows "Printing a new edition". The page reloads automatically when the new paper is ready. This takes about 2 minutes. On a Mac, the button works in Safari if the Shortcut synced with iCloud.

**If the token expires:**

Make a new token (Step A). Paste it into the Authorization header (Step B, item 6).
