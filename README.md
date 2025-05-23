## Linux Wifi Hotspot

<!-- [![Build Status](https://travis-ci.com/lakinduakash/linux-wifi-hotspot.svg?branch=master)](https://travis-ci.com/lakinduakash/linux-wifi-hotspot) -->
![Build](https://github.com/lakinduakash/linux-wifi-hotspot/actions/workflows/build.yml/badge.svg)
<!--[![Gitter](https://badges.gitter.im/linux-wihotspot/community.svg)](https://gitter.im/linux-wihotspot/community?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge) -->
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Flakinduakash%2Flinux-wifi-hotspot.svg?type=shield)](https://app.fossa.com/projects/git%2Bgithub.com%2Flakinduakash%2Flinux-wifi-hotspot?ref=badge_shield)

### What's new
* Use aa-complain instead of complain to fix the permission issue for dnsmasq
* Fix some 5Ghz band not working issue
* Compatible with iw 6.7

#### Thank you for all the contributions made while I was not active on this repository.

### Features

* Share your wifi like in Windows - Use wifi and enable hotspot at the same time.
* Share a wifi access point from any network interface
* [Create a hotspot with VPN](#vpn-hotspot) - The hotspot has the traffic tunnelled through VPN. Useful for devices with no VPN app support like TV or gaming consoles.
* Share wifi via QR code
* MAC filter
* View connected devices
* Includes Both command line and GUI.
* Support both 2.4GHz and 5GHz (Need to be compatible with your wifi adapter). Ex: You have connected to the 5GHz network and share a connection with 2.4GHz.
* Customise wifi Channel, Change MAC address, etc.
* Hide SSID
* customize gateway IP address
* Enable IEEE 80211n, IEEE 80211ac and IEEE 80211ax modes

![screenshot](docs/sc4.png)

## Web UI (Experimental)

A new experimental web-based user interface is available for managing the hotspot. This interface provides a modern way to control hotspot functionalities through a web browser.

### Web UI Features
*   Start and stop the Wi-Fi hotspot.
*   Configure SSID, password, Wi-Fi interface, and internet sharing interface.
*   Support for advanced options like frequency band (2.4GHz, 5GHz), hidden SSID, and using the physical interface directly (no-virt).
*   Set a custom channel for the hotspot.
*   Enable open (no password) hotspot.
*   View connected client devices (MAC address, IP address, hostname).
*   Generate a QR code for easy Wi-Fi connection.
*   Responsive design for use on different screen sizes.

### Web UI Installation

The Web UI requires Python 3 and several Python packages. The core dependencies of `linux-wifi-hotspot` (like `hostapd`, `dnsmasq`, and `create_ap` itself) must still be installed as per the general [Dependencies](#dependencies) and [Installation](#installation) sections.

1.  **Install Python Dependencies:**
    The Python dependencies are listed in `src/web_ui/requirements.txt`. Install them using pip:
    ```bash
    sudo pip install -r src/web_ui/requirements.txt
    ```
    *Note: It's generally recommended to use a virtual environment for Python projects, but for system-wide tools like this, direct installation with `sudo pip` might be necessary if the application itself will be run with `sudo`.*

2.  **Ensure `create_ap` is Installed:**
    Follow the main installation instructions to ensure `create_ap` is installed and available in your system's PATH.

### Running the Web UI

1.  **Start the Flask Application:**
    The web application needs to be run with root privileges because `create_ap` (which it calls) requires root access to manage network interfaces and services.
    Navigate to the repository's root directory and run:
    ```bash
    sudo python3 src/web_ui/app.py
    ```

2.  **Access the Web UI:**
    Once the server is running, open your web browser and go to:
    `http://localhost:5000`
    (Or `http://<your-server-ip>:5000` if accessing from another device on the network).

3.  **Important Note on Permissions (Recommended for Advanced Users):**
    Running the entire Flask web server as root is generally not recommended for security reasons. A more secure approach is to allow the user running the Flask application (e.g., your regular user) to execute only the `create_ap` command with `sudo` without a password.
    To do this, you would add a line to your `sudoers` file using `sudo visudo`. For example, if your username is `youruser`:
    ```
    youruser ALL=(ALL) NOPASSWD: /usr/bin/create_ap
    ```
    After this configuration, you could potentially run the Flask app as `youruser` (without `sudo python3 ...`), provided `youruser` has the necessary permissions to bind to port 5000 (ports below 1024 typically require root, but 5000 is usually fine). The Python script itself makes calls like `sudo create_ap ...`, so this `sudoers` rule would allow those calls to proceed without a password prompt.

### Command line help and documentation

Read [Command line help and documentation here](src/scripts/README.md).

If you only need the command line without GUI run `make install-cli-only` as the root user.

### Notes

- Sometimes there are troubles with **5Ghz bands** due to some vendor restrictions. If you cannot start the hotspot while you are connected to the 5Ghz band, Unselect **Auto** and select **2.4Ghz** in frequency selection.

- If any problems with **RealTeK Wifi Adapters** see [this](docs/howto/realtek.md)

- **Unable to allocate IP: firewalld issue:** Please check for potential fixes: [#209](https://github.com/lakinduakash/linux-wifi-hotspot/issues/209) [#166](https://github.com/lakinduakash/linux-wifi-hotspot/issues/166)

## Installation

#### Debian/Ubuntu

Download the Debian package from the latest [release](https://github.com/lakinduakash/linux-wifi-hotspot/releases/latest)

**OR**
Good news! I was able to restore keys, new versions will be available via the PPA
```bash
sudo add-apt-repository ppa:lakinduakash/lwh
sudo apt update
sudo apt install linux-wifi-hotspot

```

#### Arch based distributions

Linux Wifi Hotspot is available as an [AUR package](https://aur.archlinux.org/packages/linux-wifi-hotspot/). You can install it manually or with your favorite AUR helper.
For example, if you use `yay` you can do:
`yay -S linux-wifi-hotspot`

### Fedora based distributions
copr based repo is available for Fedora 
```bash
sudo dnf copr enable zinix01/linux-wifi-hotspot
sudo dnf install linux-wifi-hotspot 
```

## Dependencies

#### General
* bash
* util-linux (for getopt)
* procps or procps-ng
* hostapd
* iproute2
* iw
* iwconfig (you only need this if 'iw' can not recognize your adapter)
* haveged (optional)

_Make sure you have those dependencies by typing them in terminal. If any of dependencies fail
install it using your distro's package manager_

#### For 'NATed' or 'None' Internet sharing method
* dnsmasq
* iptables

#### To build from source

* make
* gcc and g++
* build-essential
* pkg-config
* gtk
* libgtk-3-dev
* libqrencode-dev (for qr code generation)
* libpng-dev (for qr code generation)

On Ubuntu or Debian install dependencies by,

```bash
sudo apt install -y libgtk-3-dev build-essential gcc g++ pkg-config make hostapd libqrencode-dev libpng-dev
```

On Fedora/CentOS/Red Hat Enterprise Linux/Rocky Linux/Oracle Linux
```bash
sudo dnf install -y gtk3-devel gcc gcc-c++ kernel-devel pkg-config make hostapd qrencode-devel libpng-devel
```

## Installation

    git clone https://github.com/lakinduakash/linux-wifi-hotspot
    cd linux-wifi-hotspot

    #build binaries
    make

    #install
    sudo make install

## Uninstallation
    sudo make uninstall

## Running
You can launch the GUI by searching for "Wifi Hotspot" in the Application Menu
or using the terminal with:

    wihotspot

<h2 id="vpn-hotspot">Create VPN Hotspot</h2>

After connecting to VPN, Open `wihotspot` GUI. Select the virtual interface created by the VPN. In this case it is `tun0`

![image](docs/vpn.png)




## Run on Startup
The `wihotspot` GUI uses `create_ap` to create and manage access points. This service and core logic were originally created by
[@oblique](http://github.com/oblique), and are now maintained in this
repository.

Start the hotspot service on startup (using your saved configuration) with:

    systemctl enable create_ap





## Contributing

If you found a bug or you have an idea about improving this make an issue. Even a small contribution makes the open source world more beautiful.
Please read [CONTRIBUTING.md](CONTRIBUTING.md) for more info.

## Disclaimer
<div>Icons made by <a href="https://www.freepik.com" title="Freepik">Freepik</a> from <a href="https://www.flaticon.com/" title="Flaticon">www.flaticon.com</a></div>


## Stargazers over time

[![Stargazers over time](https://starchart.cc/lakinduakash/linux-wifi-hotspot.svg)](https://starchart.cc/lakinduakash/linux-wifi-hotspot)


## License
FreeBSD

Copyright (c) 2013, oblique

Copyright (c) 2024, lakinduakash


[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Flakinduakash%2Flinux-wifi-hotspot.svg?type=large)](https://app.fossa.com/projects/git%2Bgithub.com%2Flakinduakash%2Flinux-wifi-hotspot?ref=badge_large)
