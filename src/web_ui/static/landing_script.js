document.addEventListener('DOMContentLoaded', function() {
    const welcomeMessageElem = document.getElementById('welcome-message');
    const userUsernameElem = document.getElementById('user-username');
    const userPlanElem = document.getElementById('user-plan');
    const userExpiryElem = document.getElementById('user-expiry');
    const userMacElem = document.getElementById('user-mac');
    const logoutButton = document.getElementById('logout-button');
    const messageArea = document.getElementById('message-area');

    function showMessage(message, type) {
        messageArea.textContent = message;
        messageArea.className = `message ${type}`;
        messageArea.style.display = 'block';
        setTimeout(() => { messageArea.style.display = 'none'; }, 5000);
    }

    async function fetchUserDetails() {
        try {
            const response = await fetch('/portal/api/my_details');
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.message || `Error ${response.status}: Failed to fetch user details.`);
            }
            const data = await response.json();

            if (data.status === 'success') {
                welcomeMessageElem.textContent = `Welcome, ${data.username}!`;
                userUsernameElem.textContent = data.username;
                userPlanElem.textContent = data.plan || 'N/A';
                userExpiryElem.textContent = data.session_expiry_time ? new Date(data.session_expiry_time).toLocaleString() : 'Unlimited';
                userMacElem.textContent = data.mac_address || 'N/A';
            } else {
                throw new Error(data.message || 'Could not retrieve user details.');
            }
        } catch (error) {
            console.error('Fetch user details error:', error);
            showMessage(error.message, 'error');
            // If fetching details fails (e.g. not authorized), redirect to login after a delay
            setTimeout(() => {
                window.location.href = '/portal/login';
            }, 3000);
        }
    }

    logoutButton.addEventListener('click', async function() {
        showMessage('Logging out...', 'info'); // Using 'info' class, assuming portal_style.css might have it
        logoutButton.disabled = true;

        try {
            const response = await fetch('/portal/api/logout', { method: 'POST' });
            const data = await response.json();

            if (response.ok && data.status === 'success') {
                showMessage(data.message || 'Logged out successfully. Redirecting to login...', 'success');
                setTimeout(() => {
                    window.location.href = '/portal/login';
                }, 2000);
            } else {
                throw new Error(data.message || 'Logout failed.');
            }
        } catch (error) {
            console.error('Logout error:', error);
            showMessage(error.message, 'error');
            logoutButton.disabled = false; // Re-enable button if logout fails
        }
    });

    // Initial load
    fetchUserDetails();
});
