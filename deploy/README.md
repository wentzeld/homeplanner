# Setting up HomePlanner on a Raspberry Pi

When you're done, turning on the Pi brings up the family calendar full-screen, with no desktop and nothing to click. It keeps itself up to date and turns the screen off at night.

**You need:**
- a Raspberry Pi 5 with the official 27W USB-C power supply
- a microSD card (16 GB or more; an A1/A2-rated name brand is best)
- a screen with HDMI, plus a micro-HDMI to HDMI cable
- a wireless keyboard and mouse (or a USB touchscreen)
- a computer to prepare things on, and about 45 minutes

Commands below are written for macOS or Linux. On Windows, run them in [WSL](https://learn.microsoft.com/windows/wsl/), and use [option B](#option-b-over-ssh) in step 5.

## 1. Get the code
```bash
git clone https://github.com/wentzeld/homeplanner.git homeplanner
cd homeplanner
```
All later commands on your computer are run from this `homeplanner` folder.

## 2. Prepare the SD card
1. Install **Raspberry Pi Imager** from <https://www.raspberrypi.com/software/>.
2. Choose **Raspberry Pi 5**, then **Raspberry Pi OS (64-bit)** (the normal one *with* desktop), then your SD card.
3. When it asks about OS customisation, choose **Edit settings** and set:
   - Hostname: `homeplanner`
   - A username and password (the rest of this guide calls the username `YOUR_USER`)
   - Your Wi-Fi name and password, and your timezone. Better still, use a guest network or an Ethernet cable; see [Limiting what's on the Pi](#limiting-whats-on-the-pi).
   - On the **Services** tab: **Enable SSH** (password authentication)
4. Write the card. If you'll use [option A](#option-a-from-the-sd-card-macos) in step 6, keep the card in your computer for now. Otherwise, put it in the Pi and power on. The first boot takes a few minutes.

## 3. Connect the hardware
- The screen goes into the Pi's HDMI port **nearest the USB-C power port** (HDMI0).
- The keyboard/mouse receiver goes into any USB port.
- Plug in the power last.

## 4. Google setup
Follow [docs/google-setup.md](../docs/google-setup.md). At the end you'll have a key file (`service-account.json`) and your family calendar's ID. Step 6 gets the key onto the Pi.

## 5. Fill in config.toml
```bash
cp config.example.toml config.toml
```
Open `config.toml` in any text editor and fill in:
- `calendar_id`: from the Google setup.
- `timezone`: for example `America/Los_Angeles`.
- `[[members]]`: one block per person, each with a **different** `color_id` (1–11). These are Google Calendar's event colors, so use the colors people already see on their phones.
- `[weather]`: your latitude and longitude. Right-click your home in Google Maps to copy them.
- `[sleep]`: when the screen turns off and on.

You don't need to set the key location. It defaults to `~/.config/homeplanner/service-account.json` for whatever username you chose in Imager. Set `service_account_file` only to keep the key somewhere else.

`config.toml` holds your personal settings and is never committed (it's in `.gitignore`).

## 6. Copy to the Pi and install
Choose one option.

### Option A: from the SD card (macOS)
No SSH needed. This option uses macOS's `diskutil` to find and eject the card.
1. After Imager has written the card, take it out and put it back in. A drive called `bootfs` appears.
2. Run:
   ```bash
   ./deploy/prepare-sdcard.sh
   ```
   The script:
   - checks `config.toml` for mistakes
   - copies HomePlanner onto the card
   - copies your key from `~/Downloads/service-account.json` (use `--key PATH` if it's somewhere else)
   - ejects the card
3. Put the card in the Pi and start it. When the desktop appears, open **Terminal** and run:
   ```bash
   bash /boot/firmware/homeplanner/deploy/setup-from-sdcard.sh
   ```
   The Pi needs internet for this step. The script:
   - copies everything into place and stores the key privately
   - removes the copies from the card
   - runs the installer (see [What the installer does](#what-the-installer-does))
   - asks whether to reboot

If macOS says *"The disk you inserted was not readable by this computer"*, click **Ignore**. That's the Pi's Linux partition, and it's normal.

### Option B: over SSH
1. Copy the project to the Pi:
   ```bash
   rsync -av --exclude .venv --exclude cache --exclude __pycache__ --exclude .pytest_cache --exclude .env --exclude /data \
     ./ YOUR_USER@homeplanner.local:~/homeplanner/
   ```
2. Copy the key as described in [step 7 of the Google setup](../docs/google-setup.md#7-put-the-key-on-the-pi).
3. Install:
   ```bash
   ssh -t YOUR_USER@homeplanner.local 'cd ~/homeplanner && ./deploy/install.sh && sudo reboot'
   ```

### What the installer does
`deploy/install.sh`:
- installs what's needed (system packages and a Python environment)
- sets the Pi to log in to the desktop automatically and never blank the screen
- starts the HomePlanner service at every boot
- opens the calendar full-screen when the desktop starts

After the reboot, the calendar should appear within about a minute.

**Once it's working,** move the key out of your Downloads folder, for example into a password manager. The Pi has its own copy.

## Using it
| Key | Action |
|---|---|
| `←` / `→` (or ◀ / ▶) | Previous or next week |
| `W` | Jump to next week |
| `T` | Back to this week |
| `N` | Add an event |
| `Tab`, then `Enter` (or click) | Open an event to see its details, edit it or delete it |
| `R` (or click the "Synced" time) | Refresh everything now |
| `S` (or ⚙) | Settings: other calendars, phone access |
| `Enter` / `Esc` | Save / cancel in a form |

- The display shows the week from **Sunday to Saturday**. Days already past are dimmed. After 5 minutes without input, it goes back to this week.
- **Hover** over an event to see its location. **Click** it for all its details.
- **Family events** can be edited or deleted. For repeating ones you choose **This event** or **All events**. Events from other calendars (school, sports) are read-only.
- **Events someone else created**, where the family calendar is only a guest, can only have their person changed. Google only lets the organizer change the rest.
- **Click a name or calendar** in the legend at the bottom to highlight only its events. Click it again, or leave it for a minute, to show everything.
- **At night** the screen is off. Press any key or move the mouse to wake it for a few minutes.

## Changing someone's color
Each family member's color is a Google event color, which is what everyone's phones show. There are 11 of them: Lavender, Sage, Grape, Flamingo, Banana, Tangerine, Peacock, Graphite, Blueberry, Basil and Tomato.
1. Change the person's `color_id` in `config.toml` and [update the Pi](#updating).
2. Recolor their **upcoming** events in Google, so HomePlanner still recognises them. On the Pi, run:
   ```bash
   cd ~/homeplanner && .venv/bin/python -m homeplanner.recolor Lavender Flamingo
   ```
   It lists the events it would change and asks before changing anything. Repeating events are recolored as a whole series, including past occurrences of that series.

To use a color on the **wall only** from Google's larger 24-color palette (e.g. Cherry blossom), add `display_color = "cherry-blossom"` under that person in `config.toml`. Phones keep showing the `color_id` color.

## Other calendars (school, sports…)
Press **S** on the display, or click **⚙**, to open **Settings**. Under **Other calendars**:
1. Enter a name (e.g. *Lincoln Elementary*).
2. Paste the calendar's iCal link. Schools and clubs usually offer "Subscribe", "iCal" or "Add to calendar" links ending in `.ics` or starting with `webcal://`. For a Google calendar, use one of these:
   - its **"Public address in iCal format"**, if the calendar is public
   - its **"Secret address in iCal format"** (Settings and sharing → Integrate calendar), if it's private

   A Google "embed" link (`calendar/embed?src=…`) is a web page, not a calendar, and won't work.
3. Pick a color (any of Google's 24 calendar colors) and choose **Add calendar**. HomePlanner checks the link and shows how many events it found.

Their events appear on the display in that color, labelled with the calendar's name. They're read-only and are checked for changes every 30 minutes. To remove a calendar, click **Remove** twice.

These calendars only show on the display. To see one on your own phone, use **Copy link**, then add it in Google Calendar (*Other calendars → + → From URL*).

## Using HomePlanner from your phone or laptop
Once a PIN is set, anyone on your home Wi-Fi who knows it can view the calendar, add and edit events, and manage calendars from their own device.
1. **Set the PIN** on the display: go to **Settings → Use HomePlanner from phones & laptops**, enter a 6–8 digit PIN twice, and choose **Set PIN**.
2. **Open the calendar** on a phone or laptop: go to **http://homeplanner.local:8000** and enter the PIN. The device stays signed in for 30 days.
   - If `homeplanner.local` doesn't open (some Android phones), use the Pi's IP address instead, e.g. `http://192.168.1.50:8000`.
   - To find the address, run `hostname -I` on the Pi, or look for `homeplanner` in your router's device list.

Security:
- **Wrong guesses:** 5 wrong PINs lock sign-in for 15 minutes.
- **Only the display can change the PIN.** Changing it signs everyone out. The display also has **Sign out all devices** and **Turn off remote access**.
- **Home network only:** the connection is plain HTTP, so use this only on your own home Wi-Fi, and don't forward the port on your router.
- **Guest networks:** if the Pi is on a guest network, other devices usually can't reach it (see [Limiting what's on the Pi](#limiting-whats-on-the-pi)).
- **Until a PIN is set,** other devices only see "Remote access is off".

To keep HomePlanner reachable only on the display itself: in `deploy/homeplanner.service`, remove the `HOMEPLANNER_REMOTE=1` line and change `--host 0.0.0.0` to `--host 127.0.0.1`. Then run the installer again.

## Updating
Use this when you've pulled new code or changed `config.toml`.

**Over SSH:** run the `rsync` command from [option B](#option-b-over-ssh), then:
```bash
ssh -t YOUR_USER@homeplanner.local 'cd ~/homeplanner && ./deploy/install.sh && sudo reboot'
```
If only `config.toml` changed, restarting the app is enough:
```bash
ssh YOUR_USER@homeplanner.local 'systemctl --user restart homeplanner'
```

**From the SD card (macOS):**
1. Shut the Pi down: press `Ctrl+Alt+F2`, log in, and run `sudo poweroff`.
2. Put the card in your computer and run `./deploy/prepare-sdcard.sh`. The key is optional this time; the Pi keeps its existing one.
3. Put the card back and start the Pi. The calendar comes up full-screen, so there's no Terminal. Press `Ctrl+Alt+F2` for a text login, log in, and run:
   ```bash
   bash /boot/firmware/homeplanner/deploy/setup-from-sdcard.sh
   ```
   Answer **Y** to reboot. Your saved events, calendars and PIN are kept.

## Troubleshooting
Run these over `ssh YOUR_USER@homeplanner.local`, or on the Pi itself after pressing `Ctrl+Alt+F2` and logging in.

- **See what the app is doing:** `journalctl --user -u homeplanner -f`

- **Footer says "⚠ Calendar not connected: …"**
  - Check that the key exists: `ls -la ~/.config/homeplanner/`, or wherever `service_account_file` points if you set it.
  - Check that the family calendar is shared with the service account's email with **Make changes to events**.
  - Check that `calendar_id` is right.

- **Footer says "Waiting for network…"**
  - This is normal for a few seconds after startup, while Wi-Fi connects. The app retries every 15 seconds.
  - If it stays, the Pi has no internet. Check Wi-Fi, and check the weather: it's also missing when the network is down.

- **Footer says "⚠ Can't reach Google Calendar"**
  - The Pi is offline. It shows the last events it synced and catches up by itself when the network returns.

- **"Google didn't allow this change" when editing**
  - Someone else created that event and invited the family calendar. Only they can change its title, time or place, but you can still change who it's for.

- **Adding another calendar fails with HTTP 404 or 401**
  - The link isn't a public iCal link. See [Other calendars](#other-calendars-school-sports).

- **Screen doesn't turn off at night**
  - Check the output name by running `WAYLAND_DISPLAY=wayland-0 wlr-randr`. The first line of each block (for example `HDMI-A-1`) is the name.
  - Set `[sleep] output` in `config.toml` to that name and restart the app.
  - If `wlr-randr` says it can't connect, run `ls /run/user/$(id -u)/` and look for the `wayland-*` socket. If it isn't `wayland-0`, change `WAYLAND_DISPLAY` in `~/.config/systemd/user/homeplanner.service` to match, then run `systemctl --user daemon-reload && systemctl --user restart homeplanner`.

- **Key presses don't wake the screen:** reboot once after the first install. The `input` group membership only takes effect after logging in again.

- **Forgot the PIN:** on the display, open **Settings** and set a new one. You don't need the old PIN there.

- **Get the normal desktop back:** run `./deploy/uninstall.sh`, then `sudo reboot`. To go back to the calendar, run `./deploy/install.sh` again.

## If the Pi is lost or stolen
The Google key on the SD card is not your Google password. It can't get into your Gmail, Drive or other calendars. It **can** read and change the family calendar. Anyone with the card can also read your Wi-Fi password and the saved copy of upcoming events.

Do these straight away, from any computer or phone:
1. **Delete the key.** In the [Google Cloud console](https://console.cloud.google.com/), go to IAM & Admin → Service Accounts → `homeplanner` → **Keys**, and delete the key. The file on the stolen card stops working immediately.
2. **Unshare the calendar.** In Google Calendar, open the family calendar's **Settings and sharing** and remove the service account's email. This also stops anyone reading your family's schedule, which shows when the house is empty.
3. **Change the Wi-Fi password** the Pi used. If it was a guest network (see below), change only that one.
4. **Change the Pi's password** anywhere you reused it.

For a replacement Pi, create a new key ([step 4 of the Google setup](../docs/google-setup.md#4-download-its-key)), then re-share the calendar ([step 5](../docs/google-setup.md#5-share-the-family-calendar-with-the-service-account)).

## Limiting what's on the Pi
Pick whichever of these suits your router. The first two are best.

- **Ethernet cable (best):** if a cable can reach the screen, use it and leave Wi-Fi out of the Imager settings. Then no Wi-Fi password is stored on the Pi at all.
- **Guest or IoT Wi-Fi network:** most home routers can create a second network with its own password, usually called "Guest network" or "IoT network" in the router app. Put only the Pi on it:
  - If the Pi is stolen, only that password leaks, and you can change it without reconnecting your other devices.
  - Guest networks usually block their devices from reaching your other devices. The Pi only needs the internet, so that's a plus.
  - The catch: with that blocking on, your computer can't reach the Pi over SSH, and phones can't reach it either. Either join the same network while you work on the Pi, or look for a router setting such as "allow guests to access the local network". "IoT network" options often allow this by default.
- **Not worth it:**
  - Filtering by MAC address: a thief can copy the Pi's address.
  - Storing the Wi-Fi key in encrypted form on the Pi: the stored key still joins the network, so it only hides the original wording.

## Trying it without a Pi
You can run HomePlanner on any computer in demo mode, with made-up events and weather and no Google account. See [Try the demo](../README.md#try-the-demo) in the main README.
