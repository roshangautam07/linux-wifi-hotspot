document.addEventListener('DOMContentLoaded', function() {
    // --- DOM Elements ---
    const wifiInterfaceSelect = document.getElementById('wifi-interface');
    const internetInterfaceSelect = document.getElementById('internet-interface');
    const ssidInput = document.getElementById('ssid');
    const activePid = document.getElementById('active-pid');
    const passwordInput = document.getElementById('password');
    const openHotspotCheckbox = document.getElementById('open-hotspot');
    const createHotspotBtn = document.getElementById('create-hotspot-btn');
    const stopHotspotBtn = document.getElementById('stop-hotspot-btn');
    const refreshClientsBtn = document.getElementById('refresh-clients-btn');
    const clientsTbody = document.getElementById('clients-tbody');
    const qrcodeImg = document.getElementById('qrcode-img');
    const qrcodeSection = document.getElementById('qrcode-section');
    const activeHotspotSection = document.getElementById('active-hotspot-section');
    const activeSsidSpan = document.getElementById('active-ssid');
    const activeInterfaceSpan = document.getElementById('active-interface');
    const statusArea = document.getElementById('status-area');
    const errorArea = document.getElementById('error-area');

    // Advanced Settings
    const enableChannelCheckbox = document.getElementById('enable-channel');
    const channelInput = document.getElementById('channel');
    // ... (add other advanced settings elements here as they are implemented)

    let currentHotspotInterface = null; // Stores the interface of the currently active hotspot
    let activeProcessId = null;
    // --- Utility Functions ---
    function showStatus(message) {
        statusArea.textContent = message;
        statusArea.style.display = 'block';
        errorArea.style.display = 'none';
        setTimeout(() => statusArea.style.display = 'none', 5000);
    }

    function showError(message) {
        errorArea.textContent = message;
        errorArea.style.display = 'block';
        statusArea.style.display = 'none';
        setTimeout(() => errorArea.style.display = 'none', 8000);
    }

    function clearMessages() {
        statusArea.style.display = 'none';
        errorArea.style.display = 'none';
    }

    // --- API Calls ---
    async function fetchInterfaces() {
        try {
            const response = await fetch('/interfaces');
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP error! status: ${response.status}`);
            }
            const data = await response.json();

            wifiInterfaceSelect.innerHTML = ''; // Clear existing options
            data.wifi_interfaces.forEach(iface => {
                const option = new Option(iface, iface);
                wifiInterfaceSelect.add(option);
            });

            const currentInternetInterfaceVal = internetInterfaceSelect.value;
            internetInterfaceSelect.innerHTML = '<option value="">None (No Internet Sharing)</option>'; // Clear existing, add default
            data.other_interfaces.forEach(iface => {
                const option = new Option(iface, iface);
                internetInterfaceSelect.add(option);
            });
            // Restore previous selection if it's still valid
            if (Array.from(internetInterfaceSelect.options).some(opt => opt.value === currentInternetInterfaceVal)) {
                internetInterfaceSelect.value = currentInternetInterfaceVal;
            }

        } catch (error) {
            showError(`Failed to load interfaces: ${error.message}`);
            console.error('Fetch interfaces error:', error);
        }
    }

    async function fetchHotspotStatus() {
        clearMessages();
        try {
            const response = await fetch('/hotspot/status');
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.message || `HTTP error! status: ${response.status}`);
            }
            const data = await response.json();

            if (data.running_hotspots && data.running_hotspots.length > 0) {
                const hotspot = data.running_hotspots[0]; // Assuming one hotspot for now
                console.log(hotspot)
                currentHotspotInterface = hotspot.interface; // Store the active interface
                activeProcessId = hotspot.pid;
                
                // Try to get SSID and password from form or previous state if available
                // This part is tricky as `create_ap --list-running` does not reliably give SSID
                // We'll update with the form's SSID if we started it from here.
                activeSsidSpan.textContent = ssidInput.value || 'Unknown (check form)'; 
                activeInterfaceSpan.textContent = currentHotspotInterface;
                activePid.textContent = activeProcessId
                activeHotspotSection.style.display = 'block';
                stopHotspotBtn.style.display = 'inline-block';
                createHotspotBtn.disabled = true;
                wifiInterfaceSelect.disabled = true;
                internetInterfaceSelect.disabled = true; // Disable if hotspot is running
                ssidInput.disabled = true;
                passwordInput.disabled = true;
                openHotspotCheckbox.disabled = true;

                refreshClientsBtn.disabled = false;
                fetchConnectedClients(currentHotspotInterface);
                // Fetch QR code if SSID and password are known (e.g., from form inputs)
                if (ssidInput.value) {
                    fetchQrCode(ssidInput.value, passwordInput.value, openHotspotCheckbox.checked ? 'nopass' : 'WPA');
                } else {
                    qrcodeSection.style.display = 'none';
                }

                showStatus(`Hotspot is active on ${currentHotspotInterface}.`);
            } else {
                resetToNoHotspotState();
            }
        } catch (error) {
            showError(`Failed to get hotspot status: ${error.message}`);
            resetToNoHotspotState(); // Ensure UI is in a consistent state
            console.error('Fetch hotspot status error:', error);
        }
    }

    async function createHotspot() {
        clearMessages();
        const wifiInterface = wifiInterfaceSelect.value;
        const internetInterface = internetInterfaceSelect.value;
        const ssid = ssidInput.value.trim();
        const password = passwordInput.value;
        const isOpen = openHotspotCheckbox.checked;

        if (!wifiInterface) {
            showError("Wi-Fi Interface must be selected.");
            return;
        }
        if (!ssid) {
            showError("Hotspot Name (SSID) cannot be empty.");
            return;
        }
        if (!isOpen && password.length < 8) {
            showError("Password must be at least 8 characters long for a secured hotspot.");
            return;
        }

        const payload = {
            wifi_interface: wifiInterface,
            internet_interface: internetInterface || null, // Send null if empty
            ssid: ssid,
            password: isOpen ? "12345678" : password, // create_ap needs a password, even if it's for an open network (internally it might ignore it or use a default)
            // For truly open, the backend should handle how create_ap is called (e.g. --no-passwd)
            // For now, we send a dummy if open, and rely on backend to adjust create_ap params
            // The backend should ideally support an 'encryption' type like 'none' or 'open'
            // and if 'open', it should not pass the password to create_ap or pass appropriate flags.
            // Let's assume backend /hotspot/start handles `password` for open networks correctly.
            // A better approach for "open" would be to have backend handle it specifically,
            // e.g. by not sending password or sending a specific flag.
            // For now, if open, we set a dummy password and rely on backend to know.
            // The QR code generation uses 'nopass' for open networks.
            
            // Advanced options
            freq_band: document.querySelector('input[name="freq_band"]:checked').value,
            hidden: document.getElementById('hidden').checked,
            no_virt: document.getElementById('no-virt').checked,
        };
        if (enableChannelCheckbox.checked && channelInput.value) {
            payload.channel = channelInput.value;
        }
        // ... (add other advanced settings to payload)

        createHotspotBtn.disabled = true;
        createHotspotBtn.textContent = 'Creating...';

        try {
            const response = await fetch('/hotspot/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                showStatus(data.message || "Hotspot created successfully!");
                currentHotspotInterface = wifiInterface; // Store active interface
                activeSsidSpan.textContent = ssid;
                activeInterfaceSpan.textContent = currentHotspotInterface;
                activeHotspotSection.style.display = 'block';
                stopHotspotBtn.style.display = 'inline-block';
                wifiInterfaceSelect.disabled = true;
                internetInterfaceSelect.disabled = true;
                ssidInput.disabled = true;
                passwordInput.disabled = true;
                openHotspotCheckbox.disabled = true;

                fetchQrCode(ssid, isOpen ? "" : password, isOpen ? 'nopass' : 'WPA');
                refreshClientsBtn.disabled = false;
                fetchConnectedClients(currentHotspotInterface);
            } else {
                throw new Error(data.message || "Failed to start hotspot.");
            }
        } catch (error) {
            showError(`Error creating hotspot: ${error.message}`);
            createHotspotBtn.disabled = false; // Re-enable button on failure
        } finally {
            createHotspotBtn.textContent = 'Create Hotspot';
        }
    }

    async function stopHotspot() {
        clearMessages();
        if (!currentHotspotInterface) {
            showError("No active hotspot interface found to stop. Please refresh status.");
            // Attempt to get from UI if somehow currentHotspotInterface is null
            const activeInterfaceFromUI = activeInterfaceSpan.textContent;
            if (activeInterfaceFromUI && activeInterfaceFromUI !== "N/A"){
                currentHotspotInterface = activeInterfaceFromUI;
            } else {
                 // Fallback to trying the selected wifi interface if nothing else is known
                currentHotspotInterface = wifiInterfaceSelect.value;
                if (!currentHotspotInterface) {
                     showError("Please select a Wi-Fi interface to attempt stopping the hotspot.");
                     return;
                }
                showStatus(`Attempting to stop hotspot on interface: ${currentHotspotInterface} (best guess).`);
            }
        }

        stopHotspotBtn.disabled = true;
        stopHotspotBtn.textContent = 'Stopping...';

        try {
            const response = await fetch('/hotspot/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wifi_interface: currentHotspotInterface })
            });
            const data = await response.json();
            if (response.ok && (data.status === 'success' || data.status === 'warning')) {
                showStatus(data.message || "Hotspot stopping process initiated.");
                resetToNoHotspotState();
            } else {
                throw new Error(data.message || "Failed to stop hotspot.");
            }
        } catch (error) {
            showError(`Error stopping hotspot: ${error.message}`);
            // Don't re-enable stop button immediately, let status refresh handle it
            // fetchHotspotStatus(); // Refresh status to reflect actual state
        } finally {
            stopHotspotBtn.textContent = 'Stop Hotspot';
            // Refresh status after a short delay to allow backend to process
            setTimeout(fetchHotspotStatus, 1000);
        }
    }
    
    function resetToNoHotspotState() {
        currentHotspotInterface = null;
        activeHotspotSection.style.display = 'none';
        qrcodeSection.style.display = 'none';
        qrcodeImg.src = "";
        activeSsidSpan.textContent = "N/A";
        activeInterfaceSpan.textContent = "N/A";
        
        createHotspotBtn.disabled = false;
        stopHotspotBtn.style.display = 'none';
        stopHotspotBtn.disabled = false; // Ensure it's re-enabled for future use
        
        wifiInterfaceSelect.disabled = false;
        internetInterfaceSelect.disabled = false;
        ssidInput.disabled = false;
        passwordInput.disabled = false;
        openHotspotCheckbox.disabled = false;
        
        clientsTbody.innerHTML = '<tr><td colspan="3">No clients connected or hotspot inactive.</td></tr>';
        refreshClientsBtn.disabled = true;
    }


    async function fetchConnectedClients(wifiInterface) {
        if (!wifiInterface) {
            clientsTbody.innerHTML = '<tr><td colspan="3">Hotspot interface not specified.</td></tr>';
            return;
        }
        refreshClientsBtn.disabled = true;
        refreshClientsBtn.textContent = 'Refreshing...';
        try {
            const response = await fetch(`/hotspot/clients?wifi_interface=${encodeURIComponent(wifiInterface)}`);
            const data = await response.json();
            clientsTbody.innerHTML = ''; // Clear existing rows

            if (response.ok && data.status === 'success') {
                if (data.clients && data.clients.length > 0) {
                    data.clients.forEach(client => {
                        const row = clientsTbody.insertRow();
                        row.insertCell().textContent = client.mac || 'N/A';
                        row.insertCell().textContent = client.ip || 'N/A';
                        row.insertCell().textContent = client.hostname || 'N/A';
                    });
                } else {
                    clientsTbody.innerHTML = '<tr><td colspan="3">No clients connected.</td></tr>';
                }
            } else if (response.status === 404) { // AP not running on interface
                 clientsTbody.innerHTML = `<tr><td colspan="3">${data.message || `Hotspot on ${wifiInterface} not running.`}</td></tr>`;
                 resetToNoHotspotState(); // If server says AP is not running, update UI fully
            }
            else {
                clientsTbody.innerHTML = `<tr><td colspan="3">Error: ${data.message || 'Failed to load clients.'}</td></tr>`;
            }
        } catch (error) {
            showError(`Failed to fetch clients: ${error.message}`);
            clientsTbody.innerHTML = `<tr><td colspan="3">Error fetching clients: ${error.message}</td></tr>`;
        } finally {
            refreshClientsBtn.disabled = false;
            refreshClientsBtn.textContent = 'Refresh Clients';
        }
    }

    function fetchQrCode(ssid, password, encryption) {
        if (!ssid) {
            qrcodeSection.style.display = 'none';
            return;
        }
        // For open networks, password should be empty for QR generation
        const effectivePassword = (encryption === 'nopass') ? "" : password;

        const qrUrl = `/hotspot/qr?ssid=${encodeURIComponent(ssid)}&password=${encodeURIComponent(effectivePassword)}&encryption=${encodeURIComponent(encryption)}`;
        qrcodeImg.src = qrUrl;
        qrcodeSection.style.display = 'block';
    }

    // --- Event Listeners ---
    openHotspotCheckbox.addEventListener('change', function() {
        passwordInput.disabled = this.checked;
        if (this.checked) {
            passwordInput.value = ''; // Clear password if open hotspot is selected
        }
    });

    enableChannelCheckbox.addEventListener('change', function() {
        channelInput.disabled = !this.checked;
        if (!this.checked) {
            channelInput.value = '';
        }
    });
    // ... (add listeners for other advanced option toggles)

    createHotspotBtn.addEventListener('click', createHotspot);
    stopHotspotBtn.addEventListener('click', stopHotspot);
    refreshClientsBtn.addEventListener('click', () => {
        if (currentHotspotInterface) {
            fetchConnectedClients(currentHotspotInterface);
        } else {
            // Attempt to get from UI if somehow currentHotspotInterface is null
            const activeInterfaceFromUI = activeInterfaceSpan.textContent;
            if (activeInterfaceFromUI && activeInterfaceFromUI !== "N/A"){
                 fetchConnectedClients(activeInterfaceFromUI);
            } else {
                showError("No active hotspot interface known. Cannot refresh clients.");
            }
        }
    });

    // --- Initial Load ---
    fetchInterfaces(); // Load network interfaces first
    fetchHotspotStatus(); // Then check current hotspot status

    // Periodically refresh interfaces and status (optional)
    // setInterval(fetchInterfaces, 30000); // e.g., every 30 seconds
    // setInterval(fetchHotspotStatus, 10000); // e.g., every 10 seconds
    // Be careful with frequent polling; consider WebSocket for real-time updates if necessary.
});
