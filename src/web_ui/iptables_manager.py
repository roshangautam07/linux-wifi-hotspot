import subprocess
import re
import logging

# Configure logging
logger = logging.getLogger(__name__)
# Ensure the logger is configured to output messages.
# Basic config if not already set by Flask app's logging setup.
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


# --- Chain Names ---
CAPTIVE_PORTAL_AUTHED_CHAIN = "CAPTIVE_PORTAL_AUTHED"
# CAPTIVE_PORTAL_USERS_CHAIN = "CAPTIVE_PORTAL_USERS" # Might not be needed if redirection handled by dnsmasq

# --- Helper for running sudo commands ---
def _run_sudo_command(command_args, check=True):
    """Helper to run a command with sudo, log it, and return its output."""
    try:
        logger.info(f"Executing sudo command: {' '.join(command_args)}")
        result = subprocess.run(command_args, capture_output=True, text=True, check=check)
        if result.stdout:
            logger.info(f"stdout: {result.stdout.strip()}")
        if result.stderr:
            logger.warning(f"stderr: {result.stderr.strip()}") # Use warning for stderr that doesn't cause an exception
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Command '{' '.join(e.cmd)}' failed with error: {e.stderr.strip()}")
        raise # Re-raise the exception so callers can handle it
    except FileNotFoundError:
        logger.error(f"Command '{command_args[0]}' not found. Ensure it is installed and in PATH for root user.")
        raise

# --- Core Functions ---

def get_client_mac_address(client_ip):
    """
    Retrieves the MAC address for a given client IP address.
    Uses `ip neigh show` which is more modern than `arp -n`.
    """
    if not client_ip:
        logger.error("get_client_mac_address: client_ip is required.")
        return None
    try:
        # Example output: 192.168.12.101 dev wlan0 lladdr 00:11:22:33:44:55 REACHABLE
        result = _run_sudo_command(['ip', 'neigh', 'show', client_ip])
        output = result.stdout.strip()
        if output:
            match = re.search(r'lladdr\s+([0-9a-fA-F:]+)', output)
            if match:
                mac_address = match.group(1)
                logger.info(f"Found MAC address {mac_address} for IP {client_ip}")
                return mac_address.lower()
            else:
                logger.warning(f"Could not parse MAC address from 'ip neigh show {client_ip}' output: {output}")
                return None
        else:
            logger.warning(f"No output from 'ip neigh show {client_ip}'. IP may not be in neighbor table.")
            return None
    except subprocess.CalledProcessError:
        # Error already logged by _run_sudo_command
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred in get_client_mac_address for IP {client_ip}: {e}")
        return None


def _chain_exists(chain_name, table='filter'):
    """Checks if a given iptables chain exists."""
    try:
        _run_sudo_command(['sudo', 'iptables', '-t', table, '-L', chain_name], check=True)
        logger.info(f"Chain {chain_name} in table {table} already exists.")
        return True
    except subprocess.CalledProcessError: # Chain does not exist or other error
        logger.info(f"Chain {chain_name} in table {table} does not exist or error checking.")
        return False

def _create_chain_if_not_exists(chain_name, table='filter'):
    """Creates an iptables chain if it doesn't already exist."""
    if not _chain_exists(chain_name, table):
        try:
            _run_sudo_command(['sudo', 'iptables', '-t', table, '-N', chain_name])
            logger.info(f"Successfully created chain {chain_name} in table {table}.")
            return True
        except subprocess.CalledProcessError:
            logger.error(f"Failed to create chain {chain_name} in table {table}.")
            return False
    return True


def authorize_client(mac_address, ap_interface, internet_interface):
    """Authorizes a client MAC address by adding an iptables rule."""
    if not all([mac_address, ap_interface, internet_interface]):
        logger.error("authorize_client: mac_address, ap_interface, and internet_interface are required.")
        return False
    
    if not _create_chain_if_not_exists(CAPTIVE_PORTAL_AUTHED_CHAIN):
        return False # Failed to ensure chain exists

    # Rule: Allow traffic from this MAC through the AP to the internet interface
    # We insert at the top of our custom chain.
    rule_spec = [
        '-i', ap_interface,
        '-o', internet_interface,
        '-m', 'mac', '--mac-source', mac_address,
        '-j', 'ACCEPT'
    ]
    try:
        # Check if rule already exists to prevent duplicates (optional, -I handles it but can be noisy)
        # cmd_check = ['sudo', 'iptables', '-C', CAPTIVE_PORTAL_AUTHED_CHAIN] + rule_spec
        # try:
        #     _run_sudo_command(cmd_check, check=True)
        #     logger.info(f"Rule for MAC {mac_address} already exists. No action needed.")
        #     return True
        # except subprocess.CalledProcessError: # Rule does not exist, proceed to add
        #     pass

        _run_sudo_command(['sudo', 'iptables', '-I', CAPTIVE_PORTAL_AUTHED_CHAIN, '1'] + rule_spec)
        logger.info(f"Client MAC {mac_address} authorized on {ap_interface} to {internet_interface}.")
        return True
    except subprocess.CalledProcessError:
        logger.error(f"Failed to authorize client MAC {mac_address}.")
        return False

def deauthorize_client(mac_address, ap_interface, internet_interface):
    """Deauthorizes a client MAC address by removing the iptables rule."""
    if not all([mac_address, ap_interface, internet_interface]):
        logger.error("deauthorize_client: mac_address, ap_interface, and internet_interface are required.")
        return False

    if not _chain_exists(CAPTIVE_PORTAL_AUTHED_CHAIN):
        logger.warning(f"Chain {CAPTIVE_PORTAL_AUTHED_CHAIN} does not exist. Cannot deauthorize MAC {mac_address}.")
        return False # Or True if the goal is "ensure not authorized"

    rule_spec = [
        '-i', ap_interface,
        '-o', internet_interface,
        '-m', 'mac', '--mac-source', mac_address,
        '-j', 'ACCEPT'
    ]
    try:
        _run_sudo_command(['sudo', 'iptables', '-D', CAPTIVE_PORTAL_AUTHED_CHAIN] + rule_spec)
        logger.info(f"Client MAC {mac_address} deauthorized on {ap_interface} from {internet_interface}.")
        return True
    except subprocess.CalledProcessError:
        # This can happen if the rule doesn't exist. For deauthorization, this is often acceptable.
        logger.warning(f"Failed to deauthorize client MAC {mac_address} (rule might not have existed).")
        return False # Or True, depending on desired strictness. Let's say False for "command failed".


def setup_initial_captive_portal_rules(portal_ip, portal_port, ap_interface, internet_interface, dns_ips=None):
    """
    Sets up the initial iptables rules for the captive portal.
    This is a simplified version. Assumes create_ap handles NAT, DHCP.
    Focuses on creating our chains and ensuring portal/DNS access.
    dns_ips should be a list of DNS server IPs to allow access to. If None, uses portal_ip.
    """
    if not all([portal_ip, portal_port, ap_interface, internet_interface]):
        logger.error("setup_initial_captive_portal_rules: Missing required parameters.")
        return False

    logger.info("Setting up initial captive portal iptables rules...")

    if dns_ips is None:
        dns_ips = [portal_ip] # Default to portal IP if no specific DNS IPs given

    try:
        # 1. Create our main authorization chain if it doesn't exist
        if not _create_chain_if_not_exists(CAPTIVE_PORTAL_AUTHED_CHAIN):
            return False # Critical failure

        # 2. Flush rules from our chain (if it existed and had rules)
        _run_sudo_command(['sudo', 'iptables', '-F', CAPTIVE_PORTAL_AUTHED_CHAIN])
        
        # 3. Add a default DROP at the end of our auth chain. Authorized clients get ACCEPT rules above this.
        _run_sudo_command(['sudo', 'iptables', '-A', CAPTIVE_PORTAL_AUTHED_CHAIN, '-j', 'DROP'])

        # 4. Ensure traffic from AP to Internet is FORWARDED to our chain
        # This rule needs to be inserted carefully to coexist with create_ap's rules.
        # Assuming create_ap sets up a general FORWARD rule for ap_interface to internet_interface.
        # We want to hijack that flow for unauthenticated users.
        # A common practice by create_ap is to have a chain like create_ap_forward_<ap_interface>
        # For now, let's insert a jump to our chain at the beginning of the main FORWARD chain for relevant traffic.
        # This is a strong assumption and might conflict.
        # A more robust method would be to find create_ap's specific FORWARD chain and hook into that,
        # or modify create_ap's generated rules.
        
        # Check if a general jump rule already exists to avoid duplicates
        forward_jump_check_cmd = ['sudo', 'iptables', '-C', 'FORWARD', '-i', ap_interface, '-o', internet_interface, '-j', CAPTIVE_PORTAL_AUTHED_CHAIN]
        try:
            _run_sudo_command(forward_jump_check_cmd, check=True)
            logger.info(f"FORWARD jump rule to {CAPTIVE_PORTAL_AUTHED_CHAIN} already exists.")
        except subprocess.CalledProcessError: # Rule does not exist
             # Insert the jump rule at the top of FORWARD for traffic from AP to Internet.
            _run_sudo_command(['sudo', 'iptables', '-I', 'FORWARD', '1',
                               '-i', ap_interface, '-o', internet_interface,
                               '-j', CAPTIVE_PORTAL_AUTHED_CHAIN])
            logger.info(f"Inserted FORWARD jump rule from {ap_interface} to {internet_interface} into {CAPTIVE_PORTAL_AUTHED_CHAIN}.")


        # 5. Allow access to the portal itself (HTTP/S) and DNS *before* the jump to CAPTIVE_PORTAL_AUTHED_CHAIN
        # These rules allow unauthenticated clients to reach the portal and resolve DNS.
        # Using -I FORWARD 1 to put them at the very top.
        
        # DNS (UDP and TCP port 53)
        for dns_ip in dns_ips:
            _run_sudo_command(['sudo', 'iptables', '-I', 'FORWARD', '1',
                               '-i', ap_interface, '-o', internet_interface,
                               '-p', 'udp', '--dport', '53', '-d', dns_ip,
                               '-j', 'ACCEPT'])
            _run_sudo_command(['sudo', 'iptables', '-I', 'FORWARD', '1',
                               '-i', ap_interface, '-o', internet_interface,
                               '-p', 'tcp', '--dport', '53', '-d', dns_ip,
                               '-j', 'ACCEPT'])
        logger.info(f"Added FORWARD rules for DNS to {dns_ips}.")

        # Portal Access (HTTP/S on portal_port)
        _run_sudo_command(['sudo', 'iptables', '-I', 'FORWARD', '1',
                           '-i', ap_interface, '-o', internet_interface,
                           '-p', 'tcp', '--dport', str(portal_port), '-d', portal_ip,
                           '-j', 'ACCEPT'])
        logger.info(f"Added FORWARD rule for Portal access to {portal_ip}:{portal_port}.")
        
        # Note: INPUT chain rules for portal_ip:portal_port and DNS are usually handled by create_ap
        # or the system's default INPUT policy. If Flask server is on portal_ip, it needs to accept connections.
        # If dnsmasq is on portal_ip, it also needs to accept.
        # These are not strictly FORWARDING rules but direct access to the server hosting the portal/DNS.
        # For simplicity, we'll assume create_ap or system config allows INPUT to portal_ip:portal_port and DNS.
        # The provided example rules for INPUT in the task description are good, but their placement relative
        # to create_ap's rules needs care. For now, focusing on FORWARD chain for client traffic.

        logger.info("Initial captive portal iptables rules setup/updated.")
        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Error setting up initial iptables rules: {e.stderr}")
        return False
    except Exception as e_gen:
        logger.error(f"Unexpected error setting up initial iptables rules: {e_gen}")
        return False

def teardown_captive_portal_rules(ap_interface, internet_interface):
    """Removes all iptables rules and chains related to the captive portal."""
    logger.info("Tearing down captive portal iptables rules...")
    try:
        # 1. Remove the jump rule from FORWARD to our auth chain
        # This needs to be done carefully by specifying the exact rule.
        # If multiple jump rules exist (should not happen with proper setup), this only removes one.
        try:
            _run_sudo_command(['sudo', 'iptables', '-D', 'FORWARD',
                               '-i', ap_interface, '-o', internet_interface,
                               '-j', CAPTIVE_PORTAL_AUTHED_CHAIN], check=False) # check=False as rule might not exist
            logger.info(f"Removed FORWARD jump rule from {ap_interface} to {CAPTIVE_PORTAL_AUTHED_CHAIN}.")
        except subprocess.CalledProcessError: # Rule might not exist
             logger.warning(f"Could not remove FORWARD jump rule to {CAPTIVE_PORTAL_AUTHED_CHAIN} (might not exist or other error).")


        # 2. Remove rules for portal/DNS access that we added to FORWARD
        # This is tricky as we inserted them with -I. We need to delete them specifically.
        # For simplicity, we assume they are the ones matching the spec.
        # It's safer to flush a dedicated chain if we had one for pre-auth rules.
        # For now, we'll attempt to delete them based on their known specification.
        # This part is fragile. A better way is to have a dedicated chain for these too.
        # Example for portal (assuming portal_ip, portal_port are known or retrieved from config)
        # _run_sudo_command(['sudo', 'iptables', '-D', 'FORWARD', ...spec...], check=False)
        # For now, this step is omitted due to complexity of tracking exact rules added by setup.
        # A full teardown often involves flushing the specific chains and removing the jump rules.
        # Relying on `create_ap` hotspot stop to reset FORWARD rules might be cleaner if it does.

        # 3. Flush our custom chain
        if _chain_exists(CAPTIVE_PORTAL_AUTHED_CHAIN):
            _run_sudo_command(['sudo', 'iptables', '-F', CAPTIVE_PORTAL_AUTHED_CHAIN])
            logger.info(f"Flushed chain {CAPTIVE_PORTAL_AUTHED_CHAIN}.")

            # 4. Delete our custom chain
            _run_sudo_command(['sudo', 'iptables', '-X', CAPTIVE_PORTAL_AUTHED_CHAIN])
            logger.info(f"Deleted chain {CAPTIVE_PORTAL_AUTHED_CHAIN}.")
        else:
            logger.info(f"Chain {CAPTIVE_PORTAL_AUTHED_CHAIN} did not exist, no need to flush or delete.")

        logger.info("Captive portal iptables rules teardown complete.")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Error tearing down iptables rules: {e.stderr}")
        return False
    except Exception as e_gen:
        logger.error(f"Unexpected error tearing down iptables rules: {e_gen}")
        return False

# --- DNSMASQ Management (Conceptual) ---
# def configure_dnsmasq_for_redirect(portal_ip, ap_interface):
#     """
#     Modifies the dnsmasq configuration used by create_ap to redirect DNS.
#     This is ADVANCED and highly dependent on create_ap's internals.
#     Example: Add 'address=/#/<portal_ip>' to create_ap's dnsmasq config.
#     Then, find dnsmasq PID and send SIGHUP.
#     """
#     # 1. Find create_ap's dnsmasq config file (e.g., /tmp/create_ap.<ap_interface>.conf.<random>/dnsmasq.conf)
#     # 2. Append/modify 'address=/#/<portal_ip>'
#     # 3. Find create_ap's dnsmasq PID
#     # 4. sudo kill -HUP <dnsmasq_pid>
#     logger.warning("DNSMASQ redirection configuration is conceptual and not fully implemented here.")
#     pass

# def restore_dnsmasq_config(ap_interface):
#     """
#     Restores the original dnsmasq configuration if modified.
#     """
#     logger.warning("DNSMASQ restoration is conceptual and not fully implemented here.")
#     pass
