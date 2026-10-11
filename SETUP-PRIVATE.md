# Today and Mail Tabs: Setup Guide (Mac)

This guide connects your Google Calendar and Gmail to the locked **Today** and **Mail** tabs.
Do the parts in order. Each part ends with a check.

- **Time:** about 25 minutes.
- **Browser:** Safari or Chrome.
- **What you add to GitHub:** five secrets.

| Secret name | What it holds | Part |
|---|---|---|
| `BRIEF_PASSPHRASE` | The passphrase that locks the tabs | 1 |
| `GCAL_ICS_URL` | Your calendar's private address | 2 |
| `GMAIL_CLIENT_ID` | The Gmail connection's ID | 3 |
| `GMAIL_CLIENT_SECRET` | The Gmail connection's password | 3 |
| `GMAIL_REFRESH_TOKEN` | Read-only permission to your Gmail | 3 |

## Terms

- **Secret** means an encrypted setting in your GitHub repository. Only the daily script can read it.
- **Passphrase** means a long password made of words.
- **OAuth** means Google's system for giving an app limited permission without sharing your Google password.
- **Refresh token** means a long code that lets the script ask Google for read-only access each morning.

> **WARNING:** Never paste a passphrase, address, ID, secret, or token from this guide into chat, email, notes, or a file. Paste each one only into GitHub. If one leaks, Part 6 tells you how to cancel it.

## How to add a GitHub secret

You will do this five times. Come back to these steps each time.

1. Go to `https://github.com/cmrlmatthews-max/daily-brief/settings/secrets/actions`.
2. Click **New repository secret**.
3. In **Name**, type the secret name exactly, for example `BRIEF_PASSPHRASE`.
4. In **Secret**, paste the value.
5. Click **Add secret**.

---

## Part 1. Create the passphrase

The encrypted file is public. The passphrase is the only lock, so it must be strong.

1. Open the **Passwords** app on your Mac.
2. Click **+** to make a new entry.
3. In **Title**, type `CMRLM Daily Brief`.
4. In **Password**, type at least 5 random, unrelated words with spaces between them. Example pattern: `lantern copper orbit velvet thistle`. Do not use this example.
5. Click **Save**. Passwords syncs it to your iPhone and iPad.
6. Copy the passphrase.
7. Add it as the GitHub secret `BRIEF_PASSPHRASE`.

> **CAUTION:** Do not use names, dates, song lyrics, or a password you use anywhere else. Anyone can download the encrypted file and try passphrases on their own computer.

**Check:** The GitHub Secrets page lists `BRIEF_PASSPHRASE`.

---

## Part 2. Connect Google Calendar

> **WARNING:** The secret address gives read access to all events on that calendar. Treat it like a password.

1. Go to `https://calendar.google.com`.
2. Click the gear icon at the top right, then click **Settings**.
3. In the left menu, under **Settings for my calendars**, click your main calendar (your name).
4. Scroll down to **Integrate calendar**.
5. Find **Secret address in iCal format**. Click the copy icon next to it.
6. Add it as the GitHub secret `GCAL_ICS_URL`.

The Today tab reads this one calendar. If you want more calendars, tell me and I will add support for a second address.

**Check:** The GitHub Secrets page lists `GCAL_ICS_URL`.

---

## Part 3. Connect Gmail (read-only)

This part creates a small private Google Cloud project that can only **read** your Gmail. It cannot send, delete, or change mail.

> **Note:** Google renames buttons in Google Cloud from time to time. If a name is different, look for the closest match (medium confidence in exact names).

### 3A. Create the project

1. Go to `https://console.cloud.google.com` and sign in with `cmrlmatthews@gmail.com`.
2. If Google asks you to accept terms, accept them.
3. Click the project picker at the top left (it may say **Select a project**).
4. Click **New project**.
5. In **Project name**, type `daily-brief`.
6. Click **Create**.
7. When it is done, open the project picker again and select `daily-brief`.

**Check:** The top bar shows `daily-brief`.

### 3B. Turn on the Gmail API

1. Go to `https://console.cloud.google.com/apis/library/gmail.googleapis.com`.
2. Make sure the top bar still shows `daily-brief`.
3. Click **Enable**.

**Check:** The page shows "API enabled" or a **Manage** button.

### 3C. Set up the consent screen

1. Go to `https://console.cloud.google.com/auth/overview`.
2. Click **Get started**.
3. In **App name**, type `Daily Brief`.
4. In **User support email**, select your Gmail address. Click **Next**.
5. In **Audience**, select **External**. Click **Next**.
6. In **Contact information**, type your Gmail address. Click **Next**.
7. Agree to the policy and click **Continue**, then **Create**.

### 3D. Allow read-only Gmail access

1. In the left menu, click **Data Access**.
2. Click **Add or remove scopes**.
3. In the filter box, type `gmail.readonly`.
4. Check the box for `.../auth/gmail.readonly`.
5. Click **Update**, then **Save**.

> **CAUTION:** Select only `gmail.readonly`. Do not select any scope that can send, modify, or delete mail.

### 3E. Set the app to "In production"

> **WARNING:** Do this step. If the app stays in "Testing," Google cancels the Gmail permission every 7 days, and the Mail tab stops working.

1. In the left menu, click **Audience**.
2. Under **Publishing status**, click **Publish app**.
3. Click **Confirm**.

Google does not review the app, because only you use it. Google allows an unreviewed app to have up to 100 users, and you are its only user (medium confidence in these Google limits).

**Check:** **Publishing status** shows **In production**.

### 3F. Create the client ID and secret

1. In the left menu, click **Clients**.
2. Click **Create client**.
3. In **Application type**, select **Web application**.
4. In **Name**, type `daily-brief`.
5. Under **Authorized redirect URIs**, click **Add URI**.
6. Paste `https://developers.google.com/oauthplayground` exactly.
7. Click **Create**.

> **CAUTION:** Google may show the client secret one time only. Keep the window open until step 10 is done.

8. Copy the **Client ID**. Add it as the GitHub secret `GMAIL_CLIENT_ID`.
9. Copy the **Client secret**. Add it as the GitHub secret `GMAIL_CLIENT_SECRET`.
10. Keep both values available for Part 3G. Do not save them in a file.

### 3G. Get the refresh token

1. Go to `https://developers.google.com/oauthplayground`.
2. Click the gear icon at the top right.
3. Check **Use your own OAuth credentials**.
4. Paste your **Client ID** into **OAuth Client ID**.
5. Paste your **Client secret** into **OAuth Client secret**.
6. Click **Close**.
7. On the left, find the box **Input your own scopes**.
8. Type `https://www.googleapis.com/auth/gmail.readonly` exactly.
9. Click **Authorize APIs**.
10. Select your Gmail account.
11. Google shows "Google hasn't verified this app." This is expected, because you are the developer. Click **Advanced**, then **Go to Daily Brief (unsafe)**.
12. Check that the request says it can **read** your email only. Click **Continue** (or **Allow**).
13. Back in the Playground, in **Step 2**, click **Exchange authorization code for tokens**.
14. Copy the **Refresh token**.
15. Add it as the GitHub secret `GMAIL_REFRESH_TOKEN`.
16. Close the Playground tab.

**Check:** The GitHub Secrets page lists all three: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and `GMAIL_REFRESH_TOKEN`.

---

## Part 4. Run and unlock

1. Go to `https://github.com/cmrlmatthews-max/daily-brief/actions`.
2. Click **Daily brief** on the left.
3. Click **Run workflow**, then the green **Run workflow** button.
4. Wait 1 to 3 minutes for the green check mark.
5. Open `https://cmrlmatthews-max.github.io/daily-brief/`.
6. Click the **Today** tab.
7. Type your passphrase and click **Unlock**.
8. Check **Remember on this device** only on your own devices. Read the caution in Part 5 first.

**Check:** **Today** shows your events, and **Mail** shows "The Morning Post" with your categories.

If the run shows a red X, click the run and then **build**. Copy only the error **type** (for example `HTTPError`) and send it to me. The log never shows your private data.

---

## Part 5. Daily use and the "Remember" setting

- The script runs at 2:17 AM (1:17 AM in winter). **Today** and **Mail** show that early-morning snapshot.
- To read a message, click its line in **Mail**. Gmail opens in a new tab.

> **CAUTION:** "Remember on this device" saves the passphrase in this browser. Your other GitHub Pages sites (for example your anatomy app and your political map) share storage with this site, because they all use `cmrlmatthews-max.github.io`. A bad script on any of those sites could read the saved passphrase. To be safe, either leave "Remember" off, or keep those other sites free of scripts loaded from outside services. Ask me if you want a separate address for this site.

---

## Part 6. If something leaks, or to disconnect

| What leaked | What to do |
|---|---|
| Passphrase | Make a new passphrase (Part 1). Replace `BRIEF_PASSPHRASE` in GitHub. Run the workflow. |
| Calendar address | Google Calendar > Settings > your calendar > **Integrate calendar** > **Reset** next to the secret address. Replace `GCAL_ICS_URL`. |
| Client secret or refresh token | Go to `https://myaccount.google.com/connections`, find **Daily Brief**, and remove its access. Then repeat 3F and 3G. |
| Disconnect Gmail completely | Remove access at `https://myaccount.google.com/connections`. Delete the three `GMAIL_` secrets in GitHub. |

## Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| "Plans and mail are not set up yet." | `BRIEF_PASSPHRASE` is missing, or the run has not finished. | Add the secret (Part 1). Run the workflow (Part 4). |
| "That passphrase did not work." | A typing error, or the secret changed. | Type it again. Copy it from the Passwords app. |
| Today says "not connected" | `GCAL_ICS_URL` is missing or was reset. | Do Part 2 again. |
| Mail says "not connected" after about a week | The app is still in "Testing." | Do 3E, then 3G again. Replace `GMAIL_REFRESH_TOKEN`. |
| Mail says "not connected" right away | A wrong ID, secret, or token, or the Gmail API is off. | Check 3B. Redo 3F and 3G. |
