# Basic Flask application setup
from flask import Flask, jsonify, request, send_file, render_template
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import subprocess
import re
import os
import signal
import qrcode
import io
import logging # Added for better logging

# Import iptables manager
from iptables_manager import (
    get_client_mac_address,
    authorize_client,
    deauthorize_client,
    setup_initial_captive_portal_rules,
    teardown_captive_portal_rules
)

app = Flask(__name__)

# Configure logging for the app
if not app.debug: # Only configure this if not in debug mode, debug mode might have its own
    app.logger.addHandler(logging.StreamHandler()) # Example: Log to stderr
    app.logger.setLevel(logging.INFO)

# Database Configuration
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///portal.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Database Models ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False) # Increased length for hash
    email = db.Column(db.String(120), unique=True, nullable=True)
    mac_address = db.Column(db.String(17), unique=True, nullable=True) # XX:XX:XX:XX:XX:XX
    session_expiry_time = db.Column(db.DateTime, nullable=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('plan.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False) # e.g., "Free Trial", "Basic 24h", "Unlimited"
    duration_hours = db.Column(db.Integer, nullable=False) # 0 for unlimited (manual deauth)

    users = db.relationship('User', backref='plan', lazy=True)

    def __repr__(self):
        return f'<Plan {self.name}>'

# Global dictionary to store PIDs of running create_ap processes
# Key: wifi_interface, Value: PID
running_hotspots_pids = {}

# Store active hotspot configuration (simple approach for now)
# This should ideally be managed more robustly, e.g., in a database or a dedicated config object
active_hotspot_config = {
    "ap_interface": None,
    "internet_interface": None,
    "ssid": None,
    "portal_ip": None, # Typically the gateway IP set by create_ap on ap_interface
    "portal_port": 5000 # Flask app's port
}

# It's good practice to add a comment about sudoers configuration
# For the application to run create_ap and iptables without requiring a password for sudo,
# add the following lines to /etc/sudoers using `sudo visudo`:
# <username> ALL=(ALL) NOPASSWD: /usr/bin/create_ap
# <username> ALL=(ALL) NOPASSWD: /usr/sbin/iptables
# Replace <username> with the user running the Flask application.

@app.route('/')
def index():
    return render_template('index.html')

# --- Captive Portal Page Route ---
@app.route('/portal/login', methods=['GET'])
def portal_login_page():
    # This page is what unauthenticated users will be redirected to.
    # It should not require any authentication itself.
    # Query parameters like client_mac, client_ip, original_url might be passed by the redirect mechanism.
    # For now, just serve the login page.
    # client_mac = request.args.get('client_mac')
    # client_ip = request.args.get('client_ip')
    # original_url = request.args.get('original_url')
    # You could pass these to the template if needed for more advanced scenarios.
    return render_template('login.html')

@app.route('/portal/landing', methods=['GET'])
# @require_authorized_session # Optional: decorator could be used here too
def portal_landing_page():
    # The landing_script.js will call /portal/api/my_details
    # which is protected by @require_authorized_session.
    # If that API call fails due to auth, the script will redirect to login.
    # So, direct protection on this route is optional but good for defense in depth.
    # For now, relying on the API call's protection.
    return render_template('landing.html')

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
        "other_interfaces": other_interfaces
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

    # Password length check only if not an open network (though create_ap might enforce its own rules)
    # This validation is now primarily handled by the frontend, but good to have backend checks too.
    # For captive portal, password might be for user auth, not AP auth, if AP is open.
    # Assuming AP is secured by this password for now.
    if len(password) < 8: # And some flag indicating it's not an open AP
        return jsonify({"status": "error", "message": "Password must be at least 8 characters long"}), 400
    
    # --- Determine Portal IP ---
    # This is tricky. create_ap usually sets the AP interface to an IP like 192.168.12.1.
    # We need this IP for iptables rules for the portal.
    # For now, let's assume a default or try to derive it.
    # A robust solution might involve querying the interface IP after create_ap starts.
    # For this iteration, we'll use a common default, assuming create_ap sets it.
    # This should be configurable or dynamically fetched.
    assumed_portal_ip_on_ap_interface = f"192.168.12.1" # Default create_ap gateway

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
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        running_hotspots_pids[wifi_interface] = process.pid
        
        # Store active hotspot config
        active_hotspot_config["ap_interface"] = wifi_interface
        active_hotspot_config["internet_interface"] = internet_interface
        active_hotspot_config["ssid"] = ssid
        active_hotspot_config["portal_ip"] = assumed_portal_ip_on_ap_interface # Store the assumed IP

        # Wait a moment for create_ap to set up the interface and network
        # This is a bit of a hack; ideally, we'd check when create_ap is fully ready.
        time.sleep(5) # Allow 5 seconds for create_ap to initialize

        # Setup initial iptables rules for captive portal
        # This needs the internet_interface if there is one.
        # If internet_interface is None, create_ap creates an isolated AP.
        # Captive portal on isolated AP might still be useful (local services).
        # For now, setup_initial_captive_portal_rules expects an internet_interface.
        # If it's None, we might skip this or have different rules.
        if internet_interface: # Only setup forwarding rules if internet is shared
            if not setup_initial_captive_portal_rules(
                portal_ip=active_hotspot_config["portal_ip"],
                portal_port=active_hotspot_config["portal_port"],
                ap_interface=wifi_interface,
                internet_interface=internet_interface # This must not be None
                # dns_ips could be specified, e.g., [active_hotspot_config["portal_ip"]] or public DNS
            ):
                # If iptables setup fails, we should ideally stop create_ap and report error
                # For now, just log and continue, but hotspot might not work as captive portal
                app.logger.error(f"Failed to setup initial captive portal rules for {wifi_interface}.")
                # Consider stopping create_ap here if rules are critical
        else:
            app.logger.info(f"No internet interface provided. Skipping forwarding-based captive portal iptables rules for {wifi_interface}.")
            # Basic rules for portal access on the AP interface itself might still be needed here if not sharing internet
            # For example, allowing INPUT to portal IP/port from ap_interface.
            # The current setup_initial_captive_portal_rules focuses on FORWARD chain.
            # A more nuanced setup_..._rules function would be needed for isolated APs.


        return jsonify({"status": "success", "message": f"Hotspot '{ssid}' initiated on {wifi_interface}. Captive portal setup attempted.", "pid": process.pid, "command": " ".join(cmd)})
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "create_ap command not found. Is it installed and in PATH?"}), 500
    except Exception as e:
        app.logger.error(f"Exception during hotspot start: {e}, Command: {' '.join(cmd)}")
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
    
    # Teardown iptables rules for the stopping interface
    # This needs the internet_interface if rules were set up with it.
    # We retrieve it from our stored config.
    internet_iface_for_teardown = active_hotspot_config.get("internet_interface")
    ap_iface_for_teardown = active_hotspot_config.get("ap_interface")

    if ap_iface_for_teardown == wifi_interface and internet_iface_for_teardown:
        if not teardown_captive_portal_rules(ap_iface_for_teardown, internet_iface_for_teardown):
            app.logger.warning(f"Failed to completely teardown captive portal rules for {wifi_interface}. Manual check may be needed.")
        else:
            app.logger.info(f"Successfully tore down captive portal rules for {wifi_interface}.")
    elif ap_iface_for_teardown == wifi_interface and not internet_iface_for_teardown:
        app.logger.info(f"No internet interface was configured for {wifi_interface}. Skipping forwarding-based iptables teardown.")
        # If specific rules for isolated APs were added, they should be torn down here.
    else:
        app.logger.warning(f"Could not find active config for {wifi_interface} to teardown iptables, or no internet_interface was set. Rules might persist if not matching.")


    try:
        # First, try stopping with create_ap --stop
        result = subprocess.run(cmd_stop, capture_output=True, text=True, check=False)
        
        message = f"Hotspot stop command executed for {wifi_interface}."
        response_data = {"status": "success", "message": message}

        if result.returncode != 0:
            response_data["status"] = "warning"
            response_data["message"] += f" `create_ap --stop` output: {result.stderr or result.stdout}"
        
        # If we have a PID, ensure the process is killed
        if pid_to_kill:
            try:
                os.kill(pid_to_kill, signal.SIGTERM) # Send TERM signal
                response_data["message"] += f" Process PID {pid_to_kill} for {wifi_interface} signaled to terminate."
            except ProcessLookupError:
                response_data["message"] += f" Process PID {pid_to_kill} for {wifi_interface} not found (already stopped)."
            except Exception as e_kill:
                 response_data["message"] += f" Error killing PID {pid_to_kill}: {str(e_kill)}"
        
        # Clear active hotspot config if this was the one
        if active_hotspot_config["ap_interface"] == wifi_interface:
            active_hotspot_config["ap_interface"] = None
            active_hotspot_config["internet_interface"] = None
            active_hotspot_config["ssid"] = None
            active_hotspot_config["portal_ip"] = None
            app.logger.info(f"Cleared active hotspot configuration for {wifi_interface}.")

        return jsonify(response_data)

    except FileNotFoundError:
        return jsonify({"status": "error", "message": "create_ap command not found."}), 500
    except Exception as e:
        app.logger.error(f"Exception during hotspot stop for {wifi_interface}: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/hotspot/status', methods=['GET'])
def hotspot_status():
    try:
        cmd = ['sudo', 'create_ap', '--list-running']
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        if result.returncode != 0 and "No running APs" not in result.stdout : # Handles case where no APs are running
             # If create_ap returns an error but it's not "No running APs", then it's a real error
            if "No running APs" not in result.stderr and "No running APs" not in result.stdout : # create_ap might output "No running APs" to stdout or stderr
                return jsonify({"status": "error", "message": "Failed to get hotspot status", "details": result.stderr or result.stdout}), 500
        
        output = result.stdout.strip()
        running_hotspots = []
        if output and "No running APs" not in output:
            lines = output.splitlines()
            # Example output:
            # PID    Ifaces    SSID
            # 12345  wlan0     MyAP
            # We assume the first line is a header
            for line in lines[1:]: # Skip header
                parts = line.split()
                if len(parts) >= 3:
                    pid = parts[0]
                    iface = parts[1] # This might list multiple ifaces if create_ap bridges them
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

    try:
        cmd = ['sudo', 'create_ap', '--list-clients', wifi_interface]
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
    with app.app_context():
        db.create_all() # Create tables if they don't exist
        # You might want to add some default plans here if they don't exist
        if Plan.query.count() == 0:
            default_plans = [
                Plan(name="Free Trial", duration_hours=1),
                Plan(name="Basic 24h", duration_hours=24),
                Plan(name="Admin Unlimited", duration_hours=0) # 0 for effectively unlimited
            ]
            db.session.bulk_save_objects(default_plans)
            db.session.commit()
            app.logger.info("Default plans created.") # Use app.logger
    app.run(debug=True, host='0.0.0.0', port=5000)

# --- Captive Portal API Endpoints ---
@app.route('/portal/api/register', methods=['POST'])
def portal_register():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"status": "error", "message": "Username and password are required"}), 400

    username = data.get('username')
    password = data.get('password')
    email = data.get('email') # Optional

    if User.query.filter_by(username=username).first():
        return jsonify({"status": "error", "message": "Username already exists"}), 400
    
    # Consider if email needs to be strictly unique if provided and not empty
    if email and User.query.filter_by(email=email).first():
        return jsonify({"status": "error", "message": "Email already registered"}), 400

    new_user = User(username=username, email=email if email else None) # Store None if email is empty string
    new_user.set_password(password)
    
    try:
        db.session.add(new_user)
        db.session.commit()
        # Optionally assign a default plan, e.g., "Free Trial"
        # free_trial_plan = Plan.query.filter_by(name="Free Trial").first()
        # if free_trial_plan:
        #    new_user.plan_id = free_trial_plan.id
        #    # For Free Trial, session_expiry_time could be set immediately upon registration
        #    # or upon first login. Let's assume it's set upon login for consistency.
        #    # new_user.session_expiry_time = datetime.utcnow() + timedelta(hours=free_trial_plan.duration_hours)
        #    db.session.commit()

        return jsonify({"status": "success", "message": "User registered successfully. Please log in."}), 201
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error registering user {username}: {e}")
        return jsonify({"status": "error", "message": "Registration failed due to a server error."}), 500


@app.route('/portal/api/login', methods=['POST'])
def portal_login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({"status": "error", "message": "Username and password are required"}), 400

    username = data.get('username')
    password = data.get('password')
    client_ip = request.remote_addr # Get client's IP address

    user = User.query.filter_by(username=username).first()

    if user and user.check_password(password):
        if not user.is_active:
            return jsonify({"status": "error", "message": "User account is inactive. Please contact support."}), 403
        
        # --- Session and Authorization Logic (Placeholder for now) ---
        # 1. Check if user has a plan. If not, deny or assign default.
        #    For now, we assume plan assignment is manual or a later step for new users,
        #    or a default "Free Trial" plan could be automatically assigned here if not already.
        
        # Let's try to assign a "Free Trial" plan if the user has no plan yet.
        if not user.plan_id:
            free_trial_plan = Plan.query.filter_by(name="Free Trial").first()
            if free_trial_plan:
                user.plan_id = free_trial_plan.id
                db.session.commit()
                app.logger.info(f"User {username} automatically assigned to Free Trial plan.")
            else:
                app.logger.warning("Free Trial plan not found. Cannot assign default plan to user {username}.")
                # Depending on policy, you might deny login or allow login without a plan (limited access)
                # For now, let's proceed, but this highlights a policy decision.

        # 2. Update session_expiry_time based on their plan
        # This should ideally happen only once per "purchase" or plan activation.
        # For a simple model, let's update/set it on login if they have a plan.
        # A more robust system would check current expiry and if it's still valid.
        if user.plan:
            if user.plan.duration_hours > 0:
                # Set expiry from now. If they had an active session, this extends/restarts it.
                user.session_expiry_time = datetime.utcnow() + timedelta(hours=user.plan.duration_hours)
            else: # Unlimited plan (duration_hours == 0)
                user.session_expiry_time = None 
            
            # Get MAC address for the client IP
            client_mac = get_client_mac_address(client_ip)

            if not client_mac:
                app.logger.warning(f"Could not determine MAC address for client IP {client_ip}. Cannot authorize.")
                # Depending on policy, could deny login or allow login without authorization yet
                # For now, let's proceed but skip authorization. A stricter policy would return error here.
                # return jsonify({"status": "error", "message": f"Could not identify your device's MAC address ({client_ip}). Please ensure you are connected to the hotspot."}), 400
            else:
                user.mac_address = client_mac # Store/update MAC address
                # Authorize this client via iptables
                if active_hotspot_config["ap_interface"] and active_hotspot_config["internet_interface"]:
                    if not authorize_client(client_mac, 
                                            active_hotspot_config["ap_interface"], 
                                            active_hotspot_config["internet_interface"]):
                        app.logger.error(f"Failed to authorize MAC {client_mac} for user {username} via iptables.")
                        # This is a critical failure for access
                        return jsonify({"status": "error", "message": "Device authorization failed. Please try again or contact support."}), 500
                    else:
                        app.logger.info(f"Successfully authorized MAC {client_mac} for user {username}.")
                else:
                    app.logger.warning(f"No active hotspot AP or internet interface config found. Cannot authorize MAC {client_mac} for user {username}.")
                    # This implies captive portal login happened without an active hotspot sharing internet,
                    # which might be okay for local services but not for general internet.

            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Error updating session for user {username} on login: {e}")
                return jsonify({"status": "error", "message": "Login failed during session update."}), 500

            # 3. TODO: Authorize client MAC in iptables (next subtask)
            # For now, we proceed as if authorization will happen.
            app.logger.info(f"User {username} logged in. Plan: {user.plan.name}. Session expiry: {user.session_expiry_time}")
            return jsonify({
                "status": "success", 
                "message": "Login successful. Internet access should be enabled shortly.",
                "username": user.username,
                "plan": user.plan.name,
                "session_expiry_time": user.session_expiry_time.isoformat() if user.session_expiry_time else "Unlimited"
            }), 200
        else:
            # This case means user has no plan_id or the plan_id is invalid after attempting default assignment
            app.logger.warning(f"User {username} has no valid plan assigned after login attempt.")
            return jsonify({"status": "error", "message": "No active plan assigned. Please contact support or register for a plan."}), 403
    
    return jsonify({"status": "error", "message": "Invalid username or password"}), 401

# (The init_db_command function placeholder can be removed or kept for CLI use later)
# def init_db_command(): ...
# @app.cli.command("init-db") ...

# --- Background task for deauthorizing expired sessions ---
# This would typically run in a separate thread or process, e.g., using APScheduler or a cron job.
# For APScheduler:
# from flask_apscheduler import APScheduler # Install with pip install Flask-APScheduler
# scheduler = APScheduler()
# scheduler.init_app(app)
# scheduler.start()
# @scheduler.task('interval', id='deauth_job', seconds=60, misfire_grace_time=900)
# def scheduled_deauthorize_expired_users():
#     with app.app_context(): // Important for db access in scheduled task
#        deauthorize_expired_users()

def deauthorize_expired_users():
    """
    Iterates through users with expired sessions and deauthorizes them.
    This should be called periodically by a background scheduler.
    """
    with app.app_context(): # Need app context to access db
        now = datetime.utcnow()
        # Find users whose session_expiry_time is not null, is in the past, and who have a MAC address assigned.
        expired_users = User.query.filter(
            User.session_expiry_time.isnot(None), 
            User.session_expiry_time < now,
            User.mac_address.isnot(None) # Only try to deauth if there's a MAC
        ).all()
        
        active_ap = active_hotspot_config.get("ap_interface")
        active_internet = active_hotspot_config.get("internet_interface")

        if not active_ap or not active_internet:
            app.logger.info("Deauthorization check: Hotspot not fully configured (missing AP or Internet interface). Skipping deauthorization.")
            return

        if not expired_users:
            app.logger.info("Deauthorization check: No users with expired sessions and active MAC addresses found.")
            return

        app.logger.info(f"Deauthorization check: Found {len(expired_users)} user(s) with expired sessions.")
        commit_needed = False
        for user in expired_users:
            app.logger.info(f"User {user.username}'s session expired at {user.session_expiry_time}. Attempting to deauthorize MAC: {user.mac_address}.")
            if deauthorize_client(user.mac_address, active_ap, active_internet):
                app.logger.info(f"Successfully deauthorized MAC {user.mac_address} for user {user.username} via iptables.")
                # Update user record: clear MAC and session time, or mark as inactive session
                user.mac_address = None 
                # user.session_expiry_time = None # Keep expiry time as record of when it expired
                # Optionally, could add a field like user.last_session_active = False
                commit_needed = True
            else:
                app.logger.warning(f"Failed to deauthorize MAC {user.mac_address} for user {user.username} via iptables (rule might not exist or error).")
                # If deauth command fails, we might not want to clear the MAC, to retry later.
                # Or, clear it if the rule likely doesn't exist. For now, we only clear on successful deauth.

        if commit_needed:
            try:
                db.session.commit()
                app.logger.info("Committed changes to user records after deauthorization.")
            except Exception as e:
                db.session.rollback()
                app.logger.error(f"Error committing user changes after deauthorization: {e}")


# Example of how to manually trigger for testing (e.g., via a hidden admin route)
# In a production app, this endpoint should be protected by admin authentication.
@app.route('/portal/admin/deauth_expired', methods=['POST'])
def admin_deauth_expired():
    # Add authentication/authorization for this admin endpoint in a real app
    # For example, require a specific admin user to be logged in.
    deauthorize_expired_users()
    return jsonify({"status": "success", "message": "Expired user deauthorization process triggered."})

@app.route('/portal/api/logout', methods=['POST'])
def portal_logout():
    client_ip = request.remote_addr
    app.logger.info(f"Logout request received from IP: {client_ip}")

    active_ap = active_hotspot_config.get("ap_interface")
    active_internet = active_hotspot_config.get("internet_interface")

    if not active_ap or not active_internet:
        app.logger.warning(f"Logout attempt from {client_ip} but hotspot is not fully configured. Cannot deauthorize.")
        return jsonify({"status": "error", "message": "Hotspot not active or not configured for internet sharing. Cannot process logout."}), 503

    client_mac = get_client_mac_address(client_ip)

    if not client_mac:
        app.logger.warning(f"Logout attempt from {client_ip}, but could not determine MAC address. Cannot deauthorize.")
        return jsonify({"status": "error", "message": "Could not identify your device. Logout failed."}), 400

    app.logger.info(f"Client IP {client_ip} mapped to MAC {client_mac}. Attempting logout.")

    # Find user by MAC address to clear their session details
    user = User.query.filter_by(mac_address=client_mac).first()

    if not user:
        app.logger.warning(f"No user found with MAC address {client_mac}. Deauthorizing MAC anyway if hotspot rules exist.")
        if deauthorize_client(client_mac, active_ap, active_internet):
            app.logger.info(f"Successfully deauthorized MAC {client_mac} (no user associated).")
            return jsonify({"status": "success", "message": "Device deauthorized. If you were logged in, your session is cleared."})
        else:
            app.logger.warning(f"Failed to deauthorize MAC {client_mac} (no user associated, rule might not exist).")
            return jsonify({"status": "warning", "message": "Device deauthorization command failed or rule did not exist. You may already be logged out."})


    app.logger.info(f"User {user.username} (MAC: {client_mac}) is logging out.")
    
    if deauthorize_client(client_mac, active_ap, active_internet):
        app.logger.info(f"Successfully deauthorized MAC {client_mac} for user {user.username} via iptables.")
    else:
        app.logger.error(f"CRITICAL: Failed to deauthorize MAC {client_mac} for user {user.username} via iptables. Manual intervention may be required.")
        return jsonify({"status": "error", "message": "Logout failed: Could not remove device authorization. Please contact support."}), 500

    # Clear session-related info
    user.mac_address = None
    user.session_expiry_time = None # Actively logged out, session is over.
    
    try:
        db.session.commit()
        app.logger.info(f"User {user.username}'s session information cleared from database.")
        return jsonify({"status": "success", "message": "Logout successful. Internet access has been disconnected."})
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error committing database changes for user {user.username} during logout: {e}")
        return jsonify({"status": "error", "message": "Logout partially failed: DB update error. Please contact support."}), 500

# --- Access Control Decorator (Conceptual) ---
from functools import wraps

def require_authorized_session(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        client_ip = request.remote_addr
        client_mac = get_client_mac_address(client_ip)

        if not client_mac:
            return jsonify({"status": "error", "message": "Access denied: Could not identify your device."}), 403

        user = User.query.filter_by(mac_address=client_mac).first()

        if not user:
            return jsonify({"status": "error", "message": "Access denied: No active session found for your device."}), 403
        
        if user.session_expiry_time and user.session_expiry_time < datetime.utcnow():
            # Session has expired, attempt to deauthorize just in case the scheduled task hasn't run
            active_ap = active_hotspot_config.get("ap_interface")
            active_internet = active_hotspot_config.get("internet_interface")
            if active_ap and active_internet:
                deauthorize_client(client_mac, active_ap, active_internet)
                user.mac_address = None # Clear MAC as session is now confirmed expired and deauthed
                db.session.commit()
            return jsonify({"status": "error", "message": "Access denied: Your session has expired."}), 403
        
        # User is authorized and session is valid
        return f(user, *args, **kwargs) # Pass the user object to the route
    return decorated_function

# Example of a protected route
@app.route('/portal/api/my_details', methods=['GET'])
@require_authorized_session
def my_details(current_user): # Receives current_user from the decorator
    """Example protected route to get user details."""
    return jsonify({
        "status": "success",
        "username": current_user.username,
        "email": current_user.email,
        "plan": current_user.plan.name if current_user.plan else "None",
        "session_expiry_time": current_user.session_expiry_time.isoformat() if current_user.session_expiry_time else "Unlimited",
        "mac_address": current_user.mac_address
    })
