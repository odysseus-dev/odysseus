# Podman Quadlet Deployment with Automatic Updating
This guide provides instructions on how to set up Odysseus using Podman and systemd for automatic updating.

## Part 0: Pre-setup requirements
For this setup please make sure
- Linux
- Podman Desktop
- git
- nano

are installed properly. (It might work with Podman Engine, but I have not tested that)

## Part 1: Initial setup

1. Clone the Odysseus into your home directory
```bash
git clone https://github.com/pewdiepie-archdaemon/odysseus.git
```

2. Move into the folder
```bash
cd ~/odysseus
```

3. The default Odysseus branch is ```dev``` for a stable build use the main branch by running the following code
```bash
git checkout main
```
- You can check which branch is used by running ```git branch```.

4. Copy the example environment file to create your active .env file
```bash
cp .env.example .env
```

5. Add your API keys to the .env file using nano (Optional)
```bash
nano .env
```
- Use your arrow keys to move down and enter your API keys, then press ```Ctrl+0```, ```Enter``` and ```Ctrl+X``` to save and exit.

# Step 2: Create the Quadlet Container File
Now we create the folder and file that that tells Podman how to run the Odysseus container.

1. Create the systemd folder for Podman (```-p``` ensures it creates any missing parent folders.)
```bash
mkdir -p ~/.config/containers/systemd/
```

2. Create and open the container file using nano
```bash
nano ~/.config/containers/systemd/odysseus.container
```

3. Add the following into the file (use ```Ctrl+Shift+V``` to paste) and save and exit (```Ctrl+0```, ```Enter``` and ```Ctrl+X```).
```Ini, TOML
[Container]
Image=localhost/odysseus:latest
ContainerName=odysseus
PublishPort=7000:7000
Volume=%h/odysseus:/app:Z

[Service]
Restart=always

[Install]
WantedBy=default.target
```

## Step 3: Create the Auto-Updater Service
Next, we create the background service that checks the GitHub for new code.

1. Create the user systemd folder
```bash
mkdir -p ~/.config/systemd/user/
```

2. Create and open the updater service file
```bash
nano ~/.config/systemd/user/odysseus-updater.service
```

3. Add the following into the file (use ```Ctrl+Shift+V``` to paste) and save and exit (```Ctrl+0```, ```Enter``` and ```Ctrl+X```)
```Ini, TOML
[Unit]
Description=Check Github and rebuild Odysseus

[Service]
Type=oneshot
ExecStart=/usr/bin/bash -c "cd %h/odysseus && git fetch origin && if [ $(git rev-parse HEAD) != $(git rev-parse @{u}) ]; then git pull && podman build -t localhost/odysseus:latest . && systemctl --user restart odysseus; fi"
```

## Step 4: Create the Daily Timer
Now, we create the timer that tells the updater service to run automatically every day.

1. Create and open the timer file
```bash
nano ~/.config/systemd/user/odysseus-updater.timer
```

2. Add the following into the file (use ```Ctrl+Shift+V``` to paste) and save and exit (```Ctrl+0```, ```Enter``` and ```Ctrl+X```)
```Ini, TOML
[Unit]
Description=Check for Odysseus Updates Daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

## Step 5: Start and Enable Everything
Finally, we build the initial container and activate the automatic background tasks.

1. Reload systemd so it recognizes all the new files we have created
```bash
systemctl --user daemon-reload
```

2. Start and enable the daily update timer
```bash
systemctl --user enable --now odysseus-updater.timer
```

3. Build the Odysseus image manually for this very first run
```bash
cd ~/odysseus && podman build -t localhost/odysseus:latest .
```

4. Start your new Odysseus container
```bash
systemctl --user start odysseus
```

## Step 6: You're done, time to login!
If you installed Podman Desktop, you will see the odysseus container up and running, and you can visit http://localhost:7000 to use it.

When you visit http://localhost:7000 for the first time the app generates a temporary admin password, it can be found in the background system logs. Run the following to read the logs.
```bash
journalctl --user -u odysseus
```

Closer to the bottom, look for a section that looks like this:
```bash
[ok] Initial admin user created (admin)
      Temporary password: ****************
      ** Change it after first login. Set ODYSSEUS_ADMIN_PASSWORD to choose your own. **
```
(Press ```q``` to exit the log viewer when you're done).

The password can be reset in Odysseus app settings.

## Step 7: Enable lingering (Mandatory for headless server, optional otherwise)
By default, when a user logs out, all of their background processes are killed, including Odysseus. Enabling lingering will bypass this, which is necessary for headless servers (like Fedora server) since one usually only login temporary on those systems.

Use
```bash
loginctl enable-linger $USER
```
to enable lingering and
```bash
loginctl user-status $USER
```
to check if lingering is active for your account.

## Notes
- You can run the following command to see your active user timers to check if it worked. Look for odysseus-updater.timer.
```bash
systemctl --user list-timers
```

- You can force an update by manually starting the updater with the following command. Note that it's a background task and the terminal will therefore not output anything.
```bash
systemctl --user start odysseus-updater.service
```

To see what the updater script did during its run, you can look at the systemd log history. If new code has been published it should show a ```git pull``` followed by Podman building the new image layers and ending with a message that the odysseus container was restarted.
```bash
journalctl --user -u odysseus-updater.service
```

- If you switch GitHub branch you need to rebuild the image and restart the service, use the following command.
```bash
podman build -t localhost/odysseus:latest .
```
then,
```bash
systemctl --user restart odysseus
```

- The container can not be stopped in the Podman Desktop app since the systemd keeps restarting it. Run the following command if you want to stop the container.
```bash
systemctl --user stop odysseus
```
Use
```bash
systemctl --user start odysseus
```
to start the container/service again.
