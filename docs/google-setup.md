# Google setup: let the Pi read and change your family calendar

HomePlanner connects to Google as a **service account**, a robot Google account that belongs to you. You share your family calendar with it, the same way you'd share it with a person. It never needs a password and it doesn't expire, and it can only see the calendars you share with it.

It takes about 10 minutes, in a web browser on any computer. The menu names below may differ slightly as Google updates its console.

## 0. A shared family calendar
HomePlanner shows one shared Google calendar, called "Family" in this guide. If you don't have one yet:
1. In [Google Calendar](https://calendar.google.com) on a computer, click **+** next to **Other calendars → Create new calendar**. Name it e.g. `Family`.
2. Share it with everyone in the family (**Settings and sharing → Share with specific people or groups**), so it shows on their phones too.

## 1. Create a Google Cloud project
1. Go to <https://console.cloud.google.com/> and sign in with your normal Google account.
2. Open the project picker at the top and choose **New project**. Name it `HomePlanner` and click **Create**.
3. Make sure `HomePlanner` is selected in the project picker.

The project is free. The Calendar API has no charge at a household's usage.

## 2. Turn on the Google Calendar API
1. Go to **APIs & Services → Library**.
2. Search for **Google Calendar API**, open it, and click **Enable**.

## 3. Create the service account
1. Go to **IAM & Admin → Service Accounts** and click **Create service account**.
2. Name it `homeplanner` and click **Create and continue**.
3. Skip the optional "roles" and "user access" steps, then click **Done**.
4. **Copy its email address.** It looks like `homeplanner@homeplanner-123456.iam.gserviceaccount.com`. You'll need it in step 5.

## 4. Download its key
1. Click the new service account, open the **Keys** tab, then choose **Add key → Create new key → JSON → Create**.
2. A `.json` file downloads, named something like `homeplanner-123456-a1b2c3d4e5f6.json`. Rename it to `service-account.json`.

**Keep this file private.** Anyone with it can edit the family calendar. It only goes on the Pi: don't email it, put it in a shared folder or commit it to git. The repository's `.gitignore` excludes `service-account.json` and any other `.json` file at the top level, but keep it out of the project folder anyway. If it leaks, delete the key on the same Keys tab and create a new one.

If key creation is blocked with a message about an organization policy, you're signed in with a work or school account. Use a personal Google account instead.

## 5. Share the family calendar with the service account
1. Open <https://calendar.google.com> on a computer.
2. On the left, hover over your family calendar, then choose **⋮ → Settings and sharing**.
3. Under **Share with specific people or groups**, click **Add people and groups**. Paste the service account email from step 3.
4. Set permission to **Make changes to events** and click **Send**. The service account doesn't need to accept anything.

## 6. Find the calendar ID
On the same settings page, scroll to **Integrate calendar** and copy the **Calendar ID**. It looks like `abc123...@group.calendar.google.com`. This goes into `config.toml` as `calendar_id`.

## 7. Put the key on the Pi
**If you're setting up from the SD card** ([option A](../deploy/README.md#option-a-from-the-sd-card-macos) in the setup guide), skip this step. `prepare-sdcard.sh` takes the key from `~/Downloads/service-account.json` and handles it for you.

Otherwise, from your computer, with the Pi on and connected to the same network:

```bash
ssh YOUR_USER@homeplanner.local 'mkdir -p ~/.config/homeplanner && chmod 700 ~/.config/homeplanner'
scp ~/Downloads/service-account.json YOUR_USER@homeplanner.local:~/.config/homeplanner/
ssh YOUR_USER@homeplanner.local 'chmod 600 ~/.config/homeplanner/service-account.json'
```

That's the default location, so there's nothing to set in `config.toml`.

## Checking it works
After installing, the footer of the display shows **Synced HH:MM**. If it shows **⚠ Calendar not connected** instead, see [Troubleshooting](../deploy/README.md#troubleshooting) in the setup guide.
