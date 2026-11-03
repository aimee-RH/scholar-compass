const chatContainer = document.getElementById('chatContainer');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const isDemo = document.body.dataset.demo === 'true';
const welcomeMarkup = chatContainer.innerHTML;
let conversationHistory = [];
let sessionId = null;
let requestVersion = 0;

document.getElementById('chatForm').addEventListener('submit', (event) => {
  event.preventDefault();
  sendMessage();
});

document.addEventListener('click', (event) => {
  const prompt = event.target.closest('[data-prompt]');
  if (!prompt) return;
  messageInput.value = prompt.dataset.prompt;
  sendMessage();
});

document.getElementById('newChatBtn').addEventListener('click', () => {
  requestVersion += 1;
  sessionId = null;
  conversationHistory = [];
  chatContainer.innerHTML = welcomeMarkup;
  sendButton.disabled = false;
  messageInput.value = '';
  messageInput.focus();
});

async function sendMessage() {
  const message = messageInput.value.trim();
  if (!message || sendButton.disabled) return;
  messageInput.value = '';
  sendButton.disabled = true;
  chatContainer.querySelector('.welcome-message')?.remove();
  addMessage(message, 'user');
  const history = conversationHistory.slice();
  conversationHistory.push({role: 'user', content: message});
  const pending = showTypingIndicator();
  const version = ++requestVersion;

  try {
    const response = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message, session_id: sessionId, history})
    });
    const data = await response.json();
    if (version !== requestVersion) return;
    if (!response.ok || !data.success) throw new Error(data.error || 'The request could not be completed.');
    sessionId = data.session_id || sessionId;
    pending.remove();
    addMessage(data.message, 'assistant');
    conversationHistory.push({role: 'assistant', content: data.message});
    if (isDemo && Array.isArray(data.advisors) && data.advisors.length) addAdvisorCards(data.advisors);
  } catch (error) {
    if (version !== requestVersion) return;
    pending.remove();
    addMessage(error.message || 'Something went wrong. Please try again.', 'assistant', true);
  } finally {
    if (version === requestVersion) {
      sendButton.disabled = false;
      messageInput.focus();
    }
  }
}

function addMessage(text, sender, isError = false) {
  const row = document.createElement('div');
  row.className = `message ${sender}`;
  const avatar = document.createElement('div');
  avatar.className = 'message-avatar';
  avatar.setAttribute('aria-hidden', 'true');
  avatar.textContent = sender === 'user' ? '●' : '✦';
  const content = document.createElement('div');
  content.className = `message-content${isError ? ' error-message' : ''}`;
  content.textContent = text;
  row.append(avatar, content);
  chatContainer.append(row);
  scrollToLatest();
  return row;
}

function showTypingIndicator() {
  const row = addMessage('', 'assistant');
  const content = row.querySelector('.message-content');
  const indicator = document.createElement('span');
  indicator.className = 'typing-indicator';
  indicator.setAttribute('aria-label', 'Loading response');
  for (let i = 0; i < 3; i += 1) indicator.append(document.createElement('span'));
  content.append(indicator);
  return row;
}

function addAdvisorCards(advisors) {
  const results = document.createElement('section');
  results.className = 'advisor-results';
  results.setAttribute('aria-label', 'Illustrative advisor profiles');
  const caption = document.createElement('p');
  caption.className = 'results-caption';
  caption.textContent = 'Explore these fictional profiles';
  const grid = document.createElement('div');
  grid.className = 'advisor-grid';
  advisors.forEach((advisor) => {
    const card = document.createElement('article');
    card.className = 'advisor-card';
    const top = document.createElement('div');
    top.className = 'advisor-card-top';
    const initials = document.createElement('span');
    initials.className = 'advisor-initials';
    initials.textContent = advisor.name.split(' ').map((part) => part[0]).join('').slice(0, 2);
    const heading = document.createElement('div');
    const name = document.createElement('h3');
    name.textContent = advisor.name;
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = 'FICTIONAL ADVISOR';
    heading.append(name, label);
    top.append(initials, heading);
    const focus = document.createElement('p');
    focus.className = 'focus';
    focus.textContent = advisor.focus;
    const approach = document.createElement('p');
    approach.textContent = advisor.approach;
    const ask = document.createElement('button');
    ask.type = 'button';
    ask.dataset.prompt = `Tell me about ${advisor.name}`;
    ask.textContent = 'Explore profile →';
    card.append(top, focus, approach, ask);
    grid.append(card);
  });
  results.append(caption, grid);
  chatContainer.append(results);
  scrollToLatest();
}

function scrollToLatest() {
  chatContainer.scrollTop = chatContainer.scrollHeight;
}
