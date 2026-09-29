// Chat application logic
const chatContainer = document.getElementById('chatContainer');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');

// Store conversation history (for local fallback)
let conversationHistory = [];

// Store session ID for multi-turn conversation
let sessionId = null;

// Add event listeners
sendButton.addEventListener('click', sendMessage);
chatContainer.addEventListener('click', (event) => {
    const prompt = event.target.closest('[data-prompt]');
    if (prompt) {
        messageInput.value = prompt.dataset.prompt;
        sendMessage();
    }
});
messageInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// New Chat button - reset session
document.getElementById('newChatBtn').addEventListener('click', () => {
    sessionId = null;
    conversationHistory = [];
    chatContainer.innerHTML = `
        <div class="welcome-message">
            <h2>Explore research directions</h2>
            <p>Try a research interest, compare two advisors, or ask a follow-up.</p>
            <div class="sample-prompts">
                <button type="button" data-prompt="Who works on trustworthy AI?">Trustworthy AI</button>
                <button type="button" data-prompt="I am interested in computer vision for healthcare">Vision + healthcare</button>
                <button type="button" data-prompt="Compare Ava Morgan and Leo Park">Compare advisors</button>
            </div>
            <p class="demo-note">Preview profiles are fictional. The full research graph is not connected here.</p>
        </div>
    `;
    console.log('[Session] New chat started - session reset');
});

// Send message function
async function sendMessage() {
    const message = messageInput.value.trim();

    if (!message) return;

    // Clear input
    messageInput.value = '';

    // Disable send button
    sendButton.disabled = true;

    // Remove welcome message if it exists
    const welcomeMsg = chatContainer.querySelector('.welcome-message');
    if (welcomeMsg) {
        welcomeMsg.remove();
    }

    // Add user message to chat
    addMessage(message, 'user');

    // Add to conversation history
    conversationHistory.push({ role: 'user', content: message });

    // Show typing indicator
    const typingIndicator = showTypingIndicator();

    try {
        // Send request to backend with session_id
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: message,
                session_id: sessionId,  // CRITICAL: Send session_id to backend
                history: conversationHistory.slice(0, -1)
            })
        });

        if (!response.ok) {
            throw new Error('Failed to get response');
        }

        const data = await response.json();

        // Remove typing indicator
        typingIndicator.remove();

        if (data.success) {
            // CRITICAL: Save session_id from backend response
            if (data.session_id) {
                sessionId = data.session_id;
                console.log('[Session] Session ID saved:', sessionId.substring(0, 8) + '...');
            }

            // Add assistant message
            addMessage(data.message, 'assistant');

            // Add to conversation history
            conversationHistory.push({ role: 'assistant', content: data.message });

            // Optional: Log rewriter info for debugging
            if (data.rewriter_info) {
                console.log('[Rewriter]', data.rewriter_info);
            }
            // Optional: Log tool calls for debugging
            if (data.tool_calls && data.tool_calls.length > 0) {
                console.log('Agent called tools:', data.tool_calls);
            }
        } else {
            addMessage('Sorry, I encountered an error. Please try again.', 'assistant');
        }

    } catch (error) {
        console.error('Error:', error);
        typingIndicator.remove();
        addMessage('Sorry, I encountered an error. Please try again.', 'assistant');
    } finally {
        // Re-enable send button
        sendButton.disabled = false;
        messageInput.focus();
    }
}

// Add message to chat container
function addMessage(text, sender) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}`;

    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'message-avatar';

    const avatarImg = document.createElement('img');
    avatarImg.src = sender === 'user' ? 'figure/web_logo/user-svgrepo-com.svg' : 'figure/web_logo/student-svgrepo-com.svg';
    avatarImg.alt = sender;

    avatarDiv.appendChild(avatarImg);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = text;

    messageDiv.appendChild(avatarDiv);
    messageDiv.appendChild(contentDiv);

    chatContainer.appendChild(messageDiv);

    // Scroll to bottom
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

// Show typing indicator
function showTypingIndicator() {
    const messageDiv = document.createElement('div');
    messageDiv.className = 'message assistant';

    const avatarDiv = document.createElement('div');
    avatarDiv.className = 'message-avatar';

    const avatarImg = document.createElement('img');
    avatarImg.src = 'figure/web_logo/student-svgrepo-com.svg';
    avatarImg.alt = 'assistant';

    avatarDiv.appendChild(avatarImg);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator';
    typingDiv.innerHTML = '<span></span><span></span><span></span>';

    contentDiv.appendChild(typingDiv);

    messageDiv.appendChild(avatarDiv);
    messageDiv.appendChild(contentDiv);

    chatContainer.appendChild(messageDiv);

    // Scroll to bottom
    chatContainer.scrollTop = chatContainer.scrollHeight;

    return messageDiv;
}
