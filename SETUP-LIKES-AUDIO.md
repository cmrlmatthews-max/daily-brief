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

> **CAUTION:** Do not tap the ▶ button on the tile. The copy still starts a print run until you change the address.

1. Tap the name **Send Daily Brief Likes** to open the editor. If the editor does not open, touch and hold the tile, then tap **Edit**.
2. In the **Get contents of** block, tap the address. Delete it.
3. Type this address by hand, with no space at the end:

   `https://api.github.com/repos/cmrlmatthews-max/daily-brief/actions/workflows/likes.yml/dispatches`

### 1.3 Remove the copied 5:00 AM trigger

The copy also takes the "At 5:00 AM" trigger from "Print Daily Brief."

1. Tap the gray **⊗** at the right end of the **At 5:00 AM** line.
2. If a menu asks you to confirm, tap **Remove** or **Delete**.

Do this only in the copy. Do not change "Print Daily Brief."

### 1.4 Let the Shortcut take text

Do this step before you add the likes field. Until the Shortcut takes input, the "Shortcut Input" variable does not exist.

1. At the bottom of the editor, tap the **ⓘ** button.
2. Turn on **Show in Share Sheet**. Tap **Done**.
3. A block "Receive **Apps and 18 more** from **Share Sheet**" appears at the top. Tap **Apps and 18 more**.
4. Turn on **Text** only. Turn off every other type. Tap **Done**.

**You should now see:** "Receive **Text** from **Share Sheet**."

### 1.5 Change the request body

1. In the **Get contents of** block, tap the **›** circle after the address. Method, Headers and Request Body show.
2. Keep **Method** as **POST** and keep the **Authorization** header as it is.
3. Under **Request Body**, keep **JSON** and keep the field `ref` with the value `main`.
4. Tap **Add new field**. Tap **Dictionary**. Type the key `inputs` by hand.
5. Tap the blue **0 items** on the `inputs` row. A panel opens.
6. In the panel, tap **Add new field**. Tap **Text**. Type the key `likes` by hand.
7. Tap the gray value box next to `likes`. In the bar above the keyboard, tap **Shortcut Input**.

> **CAUTION:** Do not tap **Select Variable** in this panel. It waits for a pick in the editor behind the panel, so the panel freezes and **Done** turns gray. If that happens, tap the value box again or swipe the panel down. If the panel still does not close, close the Shortcuts app and open it again.

8. Make sure the value is the **Shortcut Input** tag only. If the word "likes" also shows in front of the tag, tap after the word and delete it.
9. Tap **Done**.

**You should now see:** `inputs` with "1 item," and inside it `likes` with only the blue **Shortcut Input** tag.

### 1.6 Optional: show an error if GitHub says no

GitHub sends back nothing when the likes arrive. It sends back a message when something is wrong. Without this step, a failed send shows no message, but your stars stay on the phone, so you can send them again.

1. Delete the **Stop and output** block that came with the copy.
2. Under **Get contents of**, add the action **If**.
3. Set it to: **If** `Contents of URL` **has any value**.
4. Inside the If, add **Show Alert**. Type `Likes did not send:` and then insert the `Contents of URL` variable.
5. Turn on **Show Cancel Button**.
6. Delete the **Otherwise** part if the app added one. Keep **End If**.

When an error alert shows, tap **Cancel**. Your stars then stay on the page.

### 1.7 Token check

Your "Print Daily Brief" token already starts a workflow, so it can also start the likes workflow. If GitHub sends back "Resource not accessible," edit the token on GitHub and give it **Actions: Read and write** for the **daily-brief** repository only. Do not give it access to other repositories.

### 1.8 Test it

1. Tap the back arrow **‹** to save and leave the editor.
2. Open the Daily Brief.
3. Tap ☆ on one story. The ☆ changes to ★.
4. Tap **Send 1 like** at the bottom of the screen.
5. If Safari asks to open Shortcuts, tap **Open**.

**You should now see:** the Shortcut runs, the paper comes back, and the page shows "Likes sent." The like shows in `likes.jsonl` on the `data` branch about 10 seconds later.

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
