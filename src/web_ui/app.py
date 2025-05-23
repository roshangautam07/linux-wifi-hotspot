# Basic Flask application setup
from flask import Flask, jsonify, request, send_file, render_template
import subprocess
import re
import os
import signal
import qrcode
import io

app = Flask(__name__)

# Global dictionary to store PIDs of running create_ap processes
# Key: wifi_interface, Value: PID
running_hotspots_pids = {}

# It's good practice to add a comment about sudoers configuration
# For the application to run create_ap without requiring a password for sudo,
# add the following line to /etc/sudoers using `sudo visudo`:
# <username> ALL=(ALL) NOPASSWD: /usr/bin/create_ap
# Replace <username> with the user running the Flask application.

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/interfaces', methods=['GET'])
def get_interfaces():
    wifi_interfaces = []
    other_interfaces = []
    try:
        # Get Wi-Fi interfaces using iw dev
        iw_dev_output = subprocess.check_output(['iw', 'dev'], text=True, stderr=subprocess.PIPE)
        wifi_interfaces = re.findall(r'Interface\s+([^\s]+)', iw_dev_output)

        # Get all network interfaces using ip link
        ip_link_output = subprocess.check_output(['ip', 'link', 'show'], text=True, stderr=subprocess.PIPE)
        all_interfaces = re.findall(r'^\d+:\s+([\w.-]+):', ip_link_output, re.MULTILINE)
        
        # Filter out loopback and other non-relevant interfaces if necessary
        # For now, we assume all interfaces found by 'ip link' are potentially usable
        
        # Determine other interfaces by excluding Wi-Fi interfaces
        other_interfaces = [iface for iface in all_interfaces if iface not in wifi_interfaces and iface != 'lo']

    except subprocess.CalledProcessError as e:
        return jsonify({"error": "Failed to execute network command", "details": e.stderr}), 500
    except FileNotFoundError:
        return jsonify({"error": "Network utility (iw or ip) not found. Ensure they are installed and in PATH."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({
        "wifi_interfaces": wifi_interfaces,
        "other_interfaces": [other_interfaces,wifi_interfaces]
    })

@app.route('/hotspot/start', methods=['POST'])
def start_hotspot():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data provided"}), 400

    ssid = data.get('ssid')
    password = data.get('password')
    wifi_interface = data.get('wifi_interface')
    internet_interface = data.get('internet_interface') # Can be None

    if not all([ssid, password, wifi_interface]):
        return jsonify({"status": "error", "message": "Missing required parameters: ssid, password, wifi_interface"}), 400

    if len(password) < 8:
        return jsonify({"status": "error", "message": "Password must be at least 8 characters long"}), 400

    # Construct create_ap command
    # Example: sudo create_ap wlan0 eth0 MyAccessPoint MyPassword123
    # If internet_interface is None, create_ap will create a hotspot without internet sharing.
    cmd = ['sudo', 'create_ap', wifi_interface]
    if internet_interface:
        cmd.append(internet_interface)
    cmd.append(ssid)
    cmd.append(password)
    
    # Add other options as needed, e.g., --no-virt, --daemon, --hidden, --freq-band, --channel
    if data.get('no_virt', False): # Example of an optional parameter
        cmd.append('--no-virt')
    if data.get('hidden', False):
        cmd.append('--hidden')
    if data.get('freq_band'):
        cmd.extend(['--freq-band', str(data.get('freq_band'))])
    if data.get('channel'):
         cmd.extend(['--channel', str(data.get('channel'))])


    try:
        # Run create_ap in the background
        # Using Popen to run in background and store PID
        print(cmd)
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        running_hotspots_pids[wifi_interface] = process.pid
        return jsonify({"status": "success", "message": f"Hotspot '{ssid}' initiated on {wifi_interface}.", "pid": process.pid, "command": " ".join(cmd)})
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "create_ap command not found. Is it installed and in PATH?"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e), "command": " ".join(cmd)}), 500


@app.route('/hotspot/stop', methods=['POST'])
def stop_hotspot():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No data provided"}), 400
    
    wifi_interface = data.get('wifi_interface')
    if not wifi_interface:
        return jsonify({"status": "error", "message": "Missing wifi_interface parameter"}), 400

    cmd_stop = ['sudo', 'create_ap', '--stop', wifi_interface]
    pid_to_kill = running_hotspots_pids.pop(wifi_interface, None)

    try:
        # First, try stopping with create_ap --stop
        result = subprocess.run(cmd_stop, capture_output=True, text=True, check=False)
        
        message = f"Hotspot stop command executed for {wifi_interface}."
        response_data = {"status": "success", "message": message}

        if result.returncode != 0:
            response_data["status"] = "warning" # Or "error" depending on how critical this is
            response_data["message"] += f" `create_ap --stop` failed or partially failed: {result.stderr or result.stdout}"
        
        # If we have a PID, ensure the process is killed
        if pid_to_kill:
            try:
                os.kill(pid_to_kill, signal.SIGTERM) # Send TERM signal
                # Optionally wait and send KILL if it doesn't terminate
                # time.sleep(1)
                # os.kill(pid_to_kill, signal.SIGKILL)
                response_data["message"] += f" Process PID {pid_to_kill} for {wifi_interface} signaled to terminate."
            except ProcessLookupError:
                response_data["message"] += f" Process PID {pid_to_kill} for {wifi_interface} not found, likely already stopped."
            except Exception as e_kill:
                 response_data["message"] += f" Error trying to kill PID {pid_to_kill}: {str(e_kill)}"
        
        return jsonify(response_data)

    except FileNotFoundError:
        return jsonify({"status": "error", "message": "create_ap command not found."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

def getHotSpotPID():
        cmd = ['sudo', 'create_ap', '--list-running']
        # cmd = ['sudo', 'create_ap', '--list-clients','68284']

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        # print(result)
        if result.returncode != 0 and "No running APs" not in result.stdout : # Handles case where no APs are running
             # If create_ap returns an error but it's not "No running APs", then it's a real error
            if "No running APs" not in result.stderr and "No running APs" not in result.stdout : # create_ap might output "No running APs" to stdout or stderr
                return jsonify({"status": "error", "message": "Failed to get hotspot status", "details": result.stderr or result.stdout}), 500
        
        output = result.stdout.strip()
        # print(output.splitlines())
        running_hotspots = []
        if output and "No running APs" not in output:
            lines = output.splitlines()
            print(lines[0].split()[0])
            print(len(lines[0].split()))
            # Example output:
            # PID    Ifaces    SSID
            # 12345  wlan0     MyAP
            # We assume the first line is a header
            # for line in lines[0].split(): # Skip header
                # print(line)
                # parts = line
                # if len(parts) >= 3:
            pid = lines[0].split()[0]
            iface = lines[0].split()[1] # This might list multiple ifaces if create_ap bridges them
                    # ssid = " ".join(parts[2:]) # SSID can have spaces, though create_ap output might simplify
            running_hotspots.append({"pid": pid, "interface": iface}) # SSID might be tricky to parse reliably here
            return running_hotspots

@app.route('/hotspot/status', methods=['GET'])
def hotspot_status():
    try:
        cmd = ['sudo', 'create_ap', '--list-running']
        # cmd = ['sudo', 'create_ap', '--list-clients','68284']

        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        # print(result)
        if result.returncode != 0 and "No running APs" not in result.stdout : # Handles case where no APs are running
             # If create_ap returns an error but it's not "No running APs", then it's a real error
            if "No running APs" not in result.stderr and "No running APs" not in result.stdout : # create_ap might output "No running APs" to stdout or stderr
                return jsonify({"status": "error", "message": "Failed to get hotspot status", "details": result.stderr or result.stdout}), 500
        
        output = result.stdout.strip()
        # print(output.splitlines())
        running_hotspots = []
        if output and "No running APs" not in output:
            lines = output.splitlines()
            # print(lines[0].split()[0])
            # print(len(lines[0].split()))
            # Example output:
            # PID    Ifaces    SSID
            # 12345  wlan0     MyAP
            # We assume the first line is a header
            # for line in lines[0].split(): # Skip header
                # print(line)
                # parts = line
                # if len(parts) >= 3:
            pid = lines[0].split()[0]
            iface = lines[0].split()[1] # This might list multiple ifaces if create_ap bridges them
                    # ssid = " ".join(parts[2:]) # SSID can have spaces, though create_ap output might simplify
            running_hotspots.append({"pid": pid, "interface": iface}) # SSID might be tricky to parse reliably here
        
        # Augment with our internally tracked PIDs for consistency, though create_ap --list-running is the source of truth
        # This also helps to show hotspots that might have been started by this app but create_ap --list-running failed for some reason
        # or if parsing its output is incomplete.
        # for iface, pid in running_hotspots_pids.items():
        #    if not any(h['interface'] == iface and h['pid'] == str(pid) for h in running_hotspots):
        #        running_hotspots.append({"pid": str(pid), "interface": iface, "status_source": "internal_tracking"})


        return jsonify({"status": "success", "running_hotspots": running_hotspots})

    except FileNotFoundError:
        return jsonify({"status": "error", "message": "create_ap command not found."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/hotspot/clients', methods=['GET'])
def hotspot_clients():
    wifi_interface = request.args.get('wifi_interface')
    if not wifi_interface:
        return jsonify({"status": "error", "message": "Missing wifi_interface query parameter"}), 400
    pid = getHotSpotPID();
    # print('HELLO',len(pid[0]))
    try:
        cmd = ['sudo', 'create_ap', '--list-clients',pid[0]['pid']]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            # Check if the error is because the AP is not running on that interface
            if "is not running" in result.stderr or "not found" in result.stderr:
                 return jsonify({"status": "error", "message": f"Hotspot on {wifi_interface} is not running or interface not found.", "details": result.stderr}), 404
            return jsonify({"status": "error", "message": f"Failed to get client list for {wifi_interface}", "details": result.stderr or result.stdout}), 500
        
        output = result.stdout.strip()
        clients = []
        if output and "No clients" not in output:
            lines = output.splitlines()
            # Example output (this can vary based on create_ap version and backend (hostapd/dnsmasq)):
            # MAC Address       IP Address      Hostname
            # aa:bb:cc:dd:ee:ff 192.168.12.100  client-hostname
            # Or sometimes just MAC and IP
            for line in lines[1:]: # Skip header
                parts = line.split()
                if len(parts) >= 2: # At least MAC and IP
                    mac = parts[0]
                    ip = parts[1]
                    hostname = parts[2] if len(parts) > 2 else "N/A"
                    clients.append({"mac": mac, "ip": ip, "hostname": hostname})
        
        return jsonify({"status": "success", "clients": clients, "interface": wifi_interface})

    except FileNotFoundError:
        return jsonify({"status": "error", "message": "create_ap command not found."}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/hotspot/qr', methods=['GET'])
def hotspot_qr():
    ssid = request.args.get('ssid')
    password = request.args.get('password')
    encryption = request.args.get('encryption', 'WPA') # Default to WPA, common types: WPA, WEP, nopass

    if not ssid:
        return jsonify({"status": "error", "message": "SSID parameter is required"}), 400
    
    # Password can be empty for 'nopass' encryption
    if encryption != 'nopass' and not password:
        return jsonify({"status": "error", "message": "Password is required for WPA/WEP encryption"}), 400
    if encryption == 'nopass':
        password = "" # Ensure password is empty string for 'nopass'

    # Validate encryption type
    valid_encryptions = ['WPA', 'WEP', 'nopass'] # create_ap might support WPA2/WPA3 via WPA
    if encryption not in valid_encryptions:
        return jsonify({"status": "error", "message": f"Invalid encryption type. Valid types are: {', '.join(valid_encryptions)}"}), 400

    qr_string = f"WIFI:S:{ssid};T:{encryption};P:{password};;"
    
    try:
        img = qrcode.make(qr_string)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        return send_file(buf, mimetype='image/png')
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to generate QR code: {str(e)}"}), 500


if __name__ == '__main__':
    # Make sure to run this with sudo if create_ap requires it
    # e.g., sudo python3 app.py
    # Or configure sudoers for passwordless create_ap execution
    app.run(debug=True, host='0.0.0.0', port=5000)
