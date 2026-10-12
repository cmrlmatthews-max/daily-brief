# Setup: Likes and Listen

This guide sets up two new iPhone Shortcuts:

- **Send Daily Brief Likes** sends the stories that you star to the likes log.
- **Read Daily Brief** reads the paper aloud.

Do these steps on your iPhone. Each part takes about 5 minutes.

---

## Part 1. Send Daily Brief Likes

This Shortcut is a copy of your "Print Daily Brief" Shortcut. It uses the same GitHub token, but it starts a different workflow. A workflow is a job that GitHub runs for you.

> **CAUTION:** The iPhone keyboard can add a space at the end of a word when you tap a suggestion. A space at the end of a key or a value stops the Shortcut. Type each value by hand. Do not tap the suggestions above the keyboard.

### 1.1 Make the copy

1. Open the **Shortcuts** app.
2. Touch and hold **Print Daily Brief**.
3. Tap **Duplicate**.
4. Touch and hold the new copy. Tap **Rename**.
5. Type the name `Send Daily Brief Likes` exactly. Tap **Done**.

### 1.2 Change the address

1. Open **Send Daily Brief Likes**.
2. In the **Get Contents of URL** action, tap the URL.
3. Replace the URL with this text:

   `https://api.github.com/repos/cmrlmatthews-max/daily-brief/actions/workflows/likes.yml/dispatches`

4. Keep the **Method** as **POST**.
5. Keep the two **Headers** (Authorization and Accept) as they are.

### 1.3 Change the request body

1. Under **Request Body**, keep **JSON**.
2. Keep the field `ref` with the text value `main`.
3. Tap **Add new field**. Tap **Dictionary**. Type the key `inputs`.
4. Tap the new `inputs` dictionary to open it.
5. Inside `inputs`, tap **Add new field**. Tap **Text**. Type the key `likes`.
6. Tap the value box for `likes`. Above the keyboard, tap **Shortcut Input**.

### 1.4 Let the Shortcut take text

1. Look at the top of the Shortcut. When you inserted **Shortcut Input**, a line "Receive **Any** input from **Nowhere**" appeared.
2. Tap **Any**. Turn off every type except **Text**.
3. Leave "from **Nowhere**" as it is. The paper sends the text through a link, so no share sheet is needed.

### 1.5 Show an error if GitHub says no

GitHub sends back nothing when the likes arrive. It sends back a message when something is wrong.

1. Under **Get Contents of URL**, add the action **If**.
2. Set it to: **If** `Contents of URL` **has any value**.
3. Inside the If, add **Show Alert**. Type `Likes did not send:` and then insert the `Contents of URL` variable.
4. Turn on **Show Cancel Button**.
5. Delete the **Otherwise** part if the app added one. Keep **End If**.
6. Make sure no action comes after **End If**.

When an error alert shows, tap **Cancel**. Your stars then stay on the page, and you can send them again later.

### 1.6 Token check

Your "Print Daily Brief" token already starts a workflow, so it can also start the likes workflow. If GitHub sends back "Resource not accessible," edit the token on GitHub and give it **Actions: Read and write** for the **daily-brief** repository only. Do not give it access to other repositories.

### 1.7 Test it

1. Open the Daily Brief in Safari.
2. Tap ☆ on two stories. The ☆ changes to ★.
3. Tap **Send 2 likes** at the bottom of the screen.
4. Allow Safari to open Shortcuts if it asks.
5. The Shortcut runs and returns to the paper. The page shows "Likes sent."

---

## Part 2. Read Daily Brief

Each morning the paper makes an audio file, `listen.mp3`, read by a natural voice (Kokoro Michael). This one Shortcut plays that file. On a day with no audio file, it speaks the paper with the iPhone voice.

> **CAUTION:** The iPhone keyboard can add a space at the end of a word when you tap a suggestion. A space at the end of an address stops the Shortcut. Type each address by hand. Do not tap the suggestions above the keyboard.

### 2.1 Download a better free voice (for days with no audio)

1. Open **Settings**.
2. Tap **Accessibility**, then **Spoken Content**, then **Voices**, then **English**.
3. Pick a voice that has **Enhanced** or **Premium** in its name. Tap the download button.
4. Wait for the download to finish. The voice works offline after this.

### 2.2 Make the Shortcut

1. Open the **Shortcuts** app. Tap **+**.
2. Name the Shortcut `Read Daily Brief` exactly.
3. Add the action **Get Contents of URL**. Tap the URL. Type `https://cmrlmatthews-max.github.io/daily-brief/brief.json?v=`
4. Right after `?v=`, insert the variable **Current Date**. Tap the variable. Set **Date Format** to **ISO 8601**. This step stops the iPhone from reading an old copy.
5. Add the action **Get Dictionary Value**. Set it to: Get **Value** for `audio` in `Contents of URL`.
6. Add the action **If**. Set it to: **If** `Dictionary Value` **has any value**.
7. Inside the **If**, add the action **Get Contents of URL**. Tap the URL. Type `https://cmrlmatthews-max.github.io/daily-brief/listen.mp3?v=`
8. Right after `?v=`, insert the variable **Current Date**. Set **Date Format** to **ISO 8601**.
9. Under it, add the action **Play Sound**. Set it to play `Contents of URL` (the one from step 7).
10. In the **Otherwise** part, add the action **Get Dictionary Value**. Set it to: Get **Value** for `listen` in the `Contents of URL` from step 3.
11. Under it, add the action **Speak Text**. Set it to speak `Dictionary Value` (the one from step 10).
12. Tap the arrow in **Speak Text**. Turn on **Wait Until Finished**. Set **Voice** to the voice from step 2.1.
13. Tap **Done**.

If you cannot tell the two **Contents of URL** variables apart, tap the variable. The Shortcut shows the action that it comes from.

### 2.3 Use it

- On the paper, tap **Listen to Today's Paper** in the footer.
- In the car, say "Hey Siri, Read Daily Brief."

On Sundays, the audio also reads the week in review.

> **NOTE:** **Play Sound** has no pause button and no skip. To pause or skip, use the audio player on the page.

### 2.4 If there is no audio today

Some mornings the audio job can fail. The paper is still fine. It has no `audio` value, so the **If** goes to **Otherwise**, and the Shortcut speaks the `listen` text with the iPhone voice. You do not need to do anything.

---

## For the record: where the data lives

- **Back issues:** each paper saves to `archive/DATE.json` on the `data` branch of the repo.
- **Likes log:** `likes.jsonl` on the `data` branch. The repo is public, so this log is public.
- **The page:** stars that you have not sent yet stay only on your phone.
