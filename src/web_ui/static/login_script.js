document.addEventListener('DOMContentLoaded', function() {
    const loginForm = document.getElementById('login-form');
    const usernameInput = document.getElementById('username');
    const passwordInput = document.getElementById('password');
    const messageArea = document.getElementById('message-area');
    const loginButton = document.getElementById('login-button');

    loginForm.addEventListener('submit', async function(event) {
        event.preventDefault(); // Prevent default form submission
        clearMessages();

        const username = usernameInput.value.trim();
        const password = passwordInput.value.trim();

        if (!username || !password) {
            showMessage('Username and password are required.', 'error');
            return;
        }

        loginButton.disabled = true;
        loginButton.textContent = 'Logging in...';

        try {
            const response = await fetch('/portal/api/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ username: username, password: password }),
            });

            const data = await response.json();

            if (response.ok && data.status === 'success') {
                // showMessage(data.message || 'Login successful! Redirecting...', 'success');
                // Redirect to the landing page on successful login
                window.location.href = '/portal/landing';
            } else {
                showMessage(data.message || 'Login failed. Please check your credentials.', 'error');
                loginButton.disabled = false;
                loginButton.textContent = 'Login';
            }
        } catch (error) {
            console.error('Login request error:', error);
            showMessage('An error occurred during login. Please try again.', 'error');
            loginButton.disabled = false;
            loginButton.textContent = 'Login';
        }
    });

    function showMessage(message, type) {
        messageArea.textContent = message;
        messageArea.className = `message ${type}`; // Apply 'success' or 'error' class
        messageArea.style.display = 'block';
    }

    function clearMessages() {
        messageArea.textContent = '';
        messageArea.style.display = 'none';
    }
});
