// Admin Console Core - Global variables, authentication, navigation, and utilities
// Copyright (c) 2025-2026 Cindy's World LLC and contributors
// Licensed under the MIT License. See LICENSE.md for details.

let currentDirectory = '';
let autoSaveTimers = {}; // Track auto-save timers for each textarea
let savingStates = {}; // Track saving state for each textarea
let currentPage = 1;
let itemsPerPage = 10;
let currentTotalPages = 0;
let currentTotalItems = 0;
let currentSearchQuery = '';
let currentMediaType = 'all';
let searchDebounceTimer = null;
let pendingScrollToMediaId = null;
const API_BASE = '/admin/api';
let appInitialized = false;
let requestCooldownTimer = null;
const DEFAULT_COOLDOWN_SECONDS = 30;

// Global timeout trackers for debounced operations
let globalDocsSaveTimeout = null;
let globalDocsFilenameTimeout = null;
let agentDocsSaveTimeout = null;
let agentDocsFilenameTimeout = null;
let globalPromptsSaveTimeout = null;
let globalPromptsFilenameTimeout = null;

const authOverlay = document.getElementById('auth-overlay');
const authStatusEl = document.getElementById('auth-status');
const authErrorEl = document.getElementById('auth-error');
const requestCodeBtn = document.getElementById('request-code-btn');
const verifyCodeBtn = document.getElementById('verify-code-btn');
const otpInput = document.getElementById('otp-input');
const initialRequestButtonLabel = requestCodeBtn ? requestCodeBtn.textContent : 'Send verification code';

requestCodeBtn?.addEventListener('click', requestVerificationCode);
verifyCodeBtn?.addEventListener('click', verifyVerificationCode);
otpInput?.addEventListener('keyup', (event) => {
    if (event.key === 'Enter') {
        verifyVerificationCode();
    }
});

checkAuthStatus();

function initializeApp() {
    if (appInitialized) {
        return;
    }
    appInitialized = true;
    hideAuthOverlay();

    // Don't add old event listeners - we use event delegation now
    // The event delegation is set up below in the script

    const directorySelect = document.getElementById('directory-select');
    const mediaLimitContainer = document.getElementById('media-limit-container');
    const mediaLimitInput = document.getElementById('media-limit');
    const mediaSearchContainer = document.getElementById('media-search-container');
    const mediaSearchInput = document.getElementById('media-search');
    const mediaTypeContainer = document.getElementById('media-type-container');
    const mediaTypeSelect = document.getElementById('media-type-select');
    const clearSearchBtn = document.getElementById('clear-search-btn');
    
    if (directorySelect) {
        directorySelect.addEventListener('change', (event) => {
            currentDirectory = event.target.value;
            if (currentDirectory) {
                // Show controls only for selected directory
                const isStateMedia = currentDirectory.includes('state/media') || currentDirectory.endsWith('state/media');
                if (mediaLimitContainer) {
                    toggle(mediaLimitContainer, isStateMedia, 'block');
                }
                if (mediaSearchContainer) {
                    show(mediaSearchContainer, 'block');
                }
                if (mediaTypeContainer) {
                    show(mediaTypeContainer, 'block');
                }
                if (mediaLimitInput && !isStateMedia) {
                    mediaLimitInput.value = ''; // Clear limit when switching away from state/media
                }
                // Reset filters when changing directory
                currentSearchQuery = '';
                currentMediaType = 'all';
                currentPage = 1;
                if (mediaSearchInput) {
                    mediaSearchInput.value = '';
                }
                if (mediaTypeSelect) {
                    mediaTypeSelect.value = 'all';
                }
                        if (clearSearchBtn) {
                            hide(clearSearchBtn);
                        }
                // No need to manually clean up debounced function
                loadMediaFiles(currentDirectory);
            } else {
                // Clear everything when directory is deselected
                currentPage = 1;
                currentTotalPages = 0;
                currentTotalItems = 0;
                currentSearchQuery = '';
                currentMediaType = 'all';
                document.getElementById('media-container').innerHTML =
                    '<div class="loading">Select a directory to view media files</div>';
                // Hide pagination and filter controls
                hide('pagination-top');
                hide('pagination-bottom');
                updatePaginationControls();
                populatePageSelect();
                if (mediaLimitContainer) {
                    hide(mediaLimitContainer);
                }
                if (mediaSearchContainer) {
                    hide(mediaSearchContainer);
                }
                if (mediaTypeContainer) {
                    hide(mediaTypeContainer);
                }
                if (mediaLimitInput) {
                    mediaLimitInput.value = '';
                }
                if (mediaSearchInput) {
                    mediaSearchInput.value = '';
                }
                if (mediaTypeSelect) {
                    mediaTypeSelect.value = 'all';
                }
                        if (clearSearchBtn) {
                            hide(clearSearchBtn);
                        }
            }
        });

        fetchDirectories(directorySelect);
    }
    
    // Add event listener to limit field to reload media when it changes
    if (mediaLimitInput) {
        let limitTimeout;
        mediaLimitInput.addEventListener('input', () => {
            // Debounce the reload to avoid excessive API calls
            clearTimeout(limitTimeout);
            limitTimeout = setTimeout(() => {
                if (currentDirectory) {
                    currentPage = 1; // Reset to first page when limit changes
                    loadMediaFiles(currentDirectory);
                }
            }, 500); // Wait 500ms after user stops typing
        });
    }
    
    // Add event listener for search input with debouncing
    if (mediaSearchInput) {
        const handleSearchInput = debounce((event) => {
            const query = event.target.value.trim();
            currentSearchQuery = query;
            if (clearSearchBtn) {
                toggle(clearSearchBtn, query, 'inline-block');
            }
            if (currentDirectory) {
                currentPage = 1; // Reset to first page when searching
                loadMediaFiles(currentDirectory);
            }
        }, 300); // Debounce 300ms
        
        mediaSearchInput.addEventListener('input', handleSearchInput);
    }
    
    // Add event listener for media type filter
    if (mediaTypeSelect) {
        mediaTypeSelect.addEventListener('change', (event) => {
            currentMediaType = event.target.value;
            if (currentDirectory) {
                currentPage = 1; // Reset to first page when filtering
                loadMediaFiles(currentDirectory);
            }
        });
    }

    setupPaginationControls();
    
    // Ensure pagination is hidden initially (no directory selected)
    hide('pagination-top');
    hide('pagination-bottom');
}

function fetchDirectories(selectElement) {
    fetchWithAuth(`${API_BASE}/directories`)
        .then((response) => response.json())
        .then((directories) => {
            directories.forEach((dir) => {
                selectElement.appendChild(createOption(dir.path, dir.name));
            });
        })
        .catch((error) => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            console.error('Failed to load directories', error);
        });
}

function fetchWithAuth(url, options = {}) {
    // Always add cache: 'no-store' to prevent browser caching
    // This ensures fresh data for all requests
    // Put defaults AFTER options so they overwrite any cache settings from caller
    const finalOptions = Object.assign({}, options, {
        credentials: 'same-origin',
        cache: 'no-store'
    });
    return fetch(url, finalOptions).then((response) => {
        if (response.status === 401) {
            handleUnauthorized();
        }
        return response;
    });
}

function handleUnauthorized(message) {
    showAuthOverlay(message || 'Session expired. Please verify again.');
    throw new Error('unauthorized');
}

function showAuthOverlay(message) {
    clearRequestCooldown();
    if (typeof message === 'string') {
        showAuthStatus(message);
    } else {
        showAuthStatus('');
    }
    showAuthError('');
    authOverlay?.classList.remove('hidden');
    document.body.classList.add('auth-locked');
    if (otpInput) {
        otpInput.value = '';
        window.setTimeout(() => otpInput.focus(), 0);
    }
}

function hideAuthOverlay() {
    authOverlay?.classList.add('hidden');
    document.body.classList.remove('auth-locked');
    showAuthStatus('');
    showAuthError('');
    clearRequestCooldown();
}

function showAuthStatus(message) {
    if (!authStatusEl) {
        return;
    }
    authStatusEl.textContent = message || '';
    authStatusEl.classList.toggle('hidden', !message);
}

function showAuthError(message) {
    if (!authErrorEl) {
        return;
    }
    authErrorEl.textContent = message || '';
    authErrorEl.classList.toggle('hidden', !message);
}

function clearRequestCooldown() {
    if (requestCooldownTimer) {
        clearInterval(requestCooldownTimer);
        requestCooldownTimer = null;
    }
    if (requestCodeBtn) {
        requestCodeBtn.disabled = false;
        requestCodeBtn.textContent = initialRequestButtonLabel;
    }
}

function setRequestCooldown(seconds) {
    if (!requestCodeBtn) {
        return;
    }
    clearRequestCooldown();
    let remaining = Math.max(Math.floor(seconds), 0);
    if (remaining <= 0) {
        return;
    }
    requestCodeBtn.disabled = true;
    requestCodeBtn.textContent = `Resend code (${remaining}s)`;
    requestCooldownTimer = window.setInterval(() => {
        remaining -= 1;
        if (remaining <= 0) {
            clearRequestCooldown();
            showAuthStatus('You can request a new code if needed.');
        } else {
            requestCodeBtn.textContent = `Resend code (${remaining}s)`;
        }
    }, 1000);
}

async function requestVerificationCode() {
    if (!requestCodeBtn) {
        return;
    }
    showAuthError('');
    showAuthStatus('Sending verification code...');
    requestCodeBtn.disabled = true;

    try {
        const response = await fetchWithAuth(`${API_BASE}/auth/request-code`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        const result = await response.json();
        if (response.status === 200 && result.success) {
            const expiresIn = typeof result.expires_in === 'number' ? result.expires_in : 0;
            const expireMinutes = Math.max(Math.round(expiresIn / 60) || 1, 1);
            showAuthStatus(`Verification code sent. It expires in ${expireMinutes} minute${expireMinutes === 1 ? '' : 's'}.`);
            const cooldown = typeof result.cooldown === 'number' ? result.cooldown : DEFAULT_COOLDOWN_SECONDS;
            setRequestCooldown(cooldown);
            return;
        }
        if (response.status === 429 && result.retry_after) {
            showAuthError(result.error || 'Please wait before requesting a new code.');
            setRequestCooldown(result.retry_after);
            return;
        }
        showAuthError(result.error || 'Failed to send verification code.');
        showAuthStatus('');
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        showAuthError(`Failed to send verification code: ${error.message || error}`);
        showAuthStatus('');
    } finally {
        if (!requestCooldownTimer && requestCodeBtn) {
            requestCodeBtn.disabled = false;
            requestCodeBtn.textContent = initialRequestButtonLabel;
        }
    }
}

async function verifyVerificationCode() {
    if (!verifyCodeBtn) {
        return;
    }
    const code = (otpInput?.value || '').trim();
    if (!/^\d{6}$/.test(code)) {
        showAuthError('Enter the six digit verification code.');
        return;
    }

    showAuthError('');
    showAuthStatus('Verifying code...');
    verifyCodeBtn.disabled = true;

    try {
        const response = await fetchWithAuth(`${API_BASE}/auth/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code }),
        });
        const result = await response.json();
        if (response.status === 200 && result.success) {
            showAuthStatus('Verification successful.');
            clearRequestCooldown();
            if (otpInput) {
                otpInput.value = '';
            }
            hideAuthOverlay();
            initializeApp();
            return;
        }
        if (result.already_verified) {
            hideAuthOverlay();
            initializeApp();
            return;
        }
        showAuthError(result.error || 'Verification failed.');
        if (typeof result.remaining_attempts === 'number') {
            showAuthStatus(`Attempts remaining: ${result.remaining_attempts}`);
        } else {
            showAuthStatus('');
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        showAuthError(`Verification failed: ${error.message || error}`);
        showAuthStatus('');
    } finally {
        verifyCodeBtn.disabled = false;
    }
}

function checkAuthStatus() {
    fetchWithAuth(`${API_BASE}/auth/status`)
        .then((response) => response.json())
        .then((data) => {
            if (data.verified) {
                initializeApp();
            } else {
                showAuthOverlay('Request a verification code to continue.');
            }
        })
        .catch((error) => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            showAuthError('Unable to reach the server. Try again in a moment.');
            showAuthOverlay();
        });
}
function handleMainTabClick(e) {
    const button = e.target.closest('button.tab-button[data-tab]');
    if (!button) return;
    
    // Make sure it's in the main tab bar (not a subtab)
    const mainTabBar = document.querySelector('.header').nextElementSibling;
    if (!mainTabBar || !mainTabBar.classList.contains('tab-bar')) return;
    if (!mainTabBar.contains(button)) return;
    
    // Make sure it's not a subtab button
    if (button.hasAttribute('data-subtab') && !button.hasAttribute('data-tab')) return;
    
    e.preventDefault();
    e.stopPropagation();
    const tabName = button.getAttribute('data-tab');
    if (!tabName) return;
    
    // Update main tab buttons (only top-level ones)
    mainTabBar.querySelectorAll('.tab-button[data-tab]').forEach(btn => {
        btn.classList.remove('active');
    });
    button.classList.add('active');
    
    // Update main tab panels - clear all first
    document.querySelectorAll('.tab-panel[data-tab-panel]').forEach(panel => {
        panel.classList.remove('active');
        panel.style.display = ''; // Clear inline display style to let CSS handle it
    });
    
    // Activate the selected panel
    const panel = document.querySelector(`.tab-panel[data-tab-panel="${tabName}"]`);
    if (panel) {
        panel.classList.add('active');
        panel.style.display = 'block'; // Explicitly set to ensure visibility
        
        // Ensure subtab bar is visible
        const subtabBar = panel.querySelector('.tab-bar');
        if (subtabBar) {
            subtabBar.style.display = 'flex';
            subtabBar.style.visibility = 'visible';
            subtabBar.style.opacity = '1';
            subtabBar.style.height = 'auto';
            subtabBar.style.minHeight = '40px';
            
            // Force show each button
            const subtabButtons = subtabBar.querySelectorAll('.tab-button[data-subtab]');
            subtabButtons.forEach(btn => {
                btn.style.display = 'inline-block';
                btn.style.visibility = 'visible';
                btn.style.opacity = '1';
            });
        }
    }
    
    // Load data when switching to main tabs
    // Always reload agents dropdown when switching to Agents or Conversations tabs
    if (tabName === 'agents' || tabName === 'conversations') {
        loadAgents();
    }
    
    if (tabName === 'agents') {
        // Show first subtab (which will also call loadAgents)
        switchSubtab('profile');
    } else if (tabName === 'conversations') {
        // Show first subtab (which will also call loadAgents)
        switchSubtab('profile-conv');
        // Load recent conversations
        loadRecentConversations();
        // If an agent is already selected, load conversation partners
        const conversationsAgentSelect = document.getElementById('conversations-agent-select');
        const agentName = conversationsAgentSelect?.value;
        if (agentName) {
            loadConversationPartners(agentName, 'conversations');
        }
    } else if (tabName === 'global') {
        // Global tab - show first subtab
        switchSubtab('media');
    }
}

// Attach event delegation to the main tab bar
const mainTabBar = document.querySelector('.header').nextElementSibling;
if (mainTabBar && mainTabBar.classList.contains('tab-bar')) {
    mainTabBar.addEventListener('click', handleMainTabClick);
}

// Subtab switching logic (for Global, Agents and Conversations tabs)
function switchSubtab(subtabName) {
    // Get the active main tab - check global, agents and conversations tabs
    // First try to find an active tab panel
    let activeMainTab = document.querySelector('.tab-panel.active[data-tab-panel="global"]');
    if (!activeMainTab) {
        activeMainTab = document.querySelector('.tab-panel.active[data-tab-panel="agents"]');
    }
    if (!activeMainTab) {
        activeMainTab = document.querySelector('.tab-panel.active[data-tab-panel="conversations"]');
    }
    
    // If no active tab found, try to find by checking which main tab button is active
    if (!activeMainTab) {
        const activeMainTabButton = document.querySelector('nav.tab-bar:first-of-type .tab-button.active[data-tab]');
        if (activeMainTabButton) {
            const tabName = activeMainTabButton.getAttribute('data-tab');
            activeMainTab = document.querySelector(`.tab-panel[data-tab-panel="${tabName}"]`);
        }
    }
    
    // If still not found, this shouldn't happen, but return early
    if (!activeMainTab) {
        return;
    }
    
    // Ensure the main tab panel stays active and visible
    activeMainTab.classList.add('active');
    activeMainTab.style.display = 'block'; // Explicitly set to block to ensure visibility
    
    // Ensure the subtab bar is visible
    const subtabBar = activeMainTab.querySelector('.tab-bar');
    if (subtabBar) {
        subtabBar.style.display = 'flex';
        subtabBar.style.visibility = 'visible';
        subtabBar.style.opacity = '1';
        subtabBar.style.height = 'auto';
        subtabBar.style.minHeight = '40px';
    }
    
    const mainTabName = activeMainTab.getAttribute('data-tab-panel');
    
    // Check if the subtab is already active - if so, we still want to reload data
    const wasAlreadyActive = activeMainTab.querySelector(`.tab-button[data-subtab="${subtabName}"]`)?.classList.contains('active');
    
    // Update subtab buttons within the active main tab
    const subtabButtons = activeMainTab.querySelectorAll('.tab-button[data-subtab]');
    subtabButtons.forEach(btn => {
        btn.classList.remove('active');
        btn.style.display = 'inline-block';
        btn.style.visibility = 'visible';
        btn.style.opacity = '1';
        if (btn.getAttribute('data-subtab') === subtabName) {
            btn.classList.add('active');
        }
    });
    
    // Update subtab panels within the active main tab
    const subtabPanels = activeMainTab.querySelectorAll('.subtab-panel');
    subtabPanels.forEach(panel => {
        panel.classList.remove('active');
        if (panel.getAttribute('data-subtab-panel') === subtabName) {
            panel.classList.add('active');
        }
    });
    
    // Load data for the subtab
    // Always reload agents dropdown when switching subtabs in Agents or Conversations tabs
    // This ensures asterisks are updated for the current subtab
    if (mainTabName === 'agents' || mainTabName === 'conversations') {
        loadAgents();
    }
    
    if (mainTabName === 'agents') {
        const agentSelect = document.getElementById('agents-agent-select');
        // Capture agentName right before use to ensure it's current
        // This prevents using stale values if loadAgents() is still updating the select
        const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
        if (agentName) {
            // Always reload data, even if the subtab was already active
            // This ensures fresh data when clicking the same tab again
            if (subtabName === 'profile') {
                loadAgentProfile(agentName);
            } else if (subtabName === 'contacts') {
                loadAgentContacts(agentName);
            } else if (subtabName === 'parameters') {
                loadAgentConfiguration(agentName);
            } else if (subtabName === 'memories') {
                // loadMemories will validate agentName matches current selection
                loadMemories(agentName);
            } else if (subtabName === 'intentions') {
                loadIntentions(agentName);
            } else if (subtabName === 'documents-agent') {
                loadAgentDocs(agentName);
            } else if (subtabName === 'memberships') {
                loadMemberships(agentName);
            }
        } else {
            // No agent selected - clear profile if switching to profile subtab
            if (subtabName === 'profile') {
                loadAgentProfile('');
            }
        }
    } else if (mainTabName === 'global') {
        if (subtabName === 'documents-global') {
            loadGlobalDocsConfigDirectories();
        } else if (subtabName === 'role-prompts') {
            loadGlobalPromptsConfigDirectories();
        } else if (subtabName === 'parameters-global') {
            loadGlobalParameters();
        } else if (subtabName === 'llms-global') {
            loadGlobalLLMs();
        } else if (subtabName === 'new-agent') {
            initializeNewAgentForm();
        }
    } else if (mainTabName === 'conversations') {
        // Reload conversation partners dropdown to update asterisks for new subtab
        const agentSelect = document.getElementById('conversations-agent-select');
        const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
        if (agentName) {
            loadConversationPartners(agentName, 'conversations');
        }
        
        // Reload recent conversations dropdown to update asterisks for new subtab
        loadRecentConversations();
        
        const partnerSelect = document.getElementById('conversations-partner-select');
        const userIdInput = document.getElementById('conversations-user-id');
        const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
        
        if (subtabName === 'profile-conv') {
            loadConversationProfile();
        } else if (agentName && userId) {
            if (subtabName === 'notes-conv') {
                loadNotesForPartner();
            } else if (subtabName === 'conversation-parameters') {
                loadConversationParameters();
            } else if (subtabName === 'plans') {
                loadPlans();
            } else if (subtabName === 'conversation') {
                loadConversation();
            } else if (subtabName === 'xsend') {
                // Show XSend content
                const xsendContainer = document.getElementById('xsend-container');
                const xsendContent = document.getElementById('xsend-content');
                if (xsendContainer && xsendContent) {
                    xsendContainer.querySelector('.loading').style.display = 'none';
                    xsendContent.style.display = 'block';
                }
            } else if (subtabName === 'work-queue') {
                WorkQueueUI.load();
            }
        }
    }

    scheduleAutoGrowRefresh(activeMainTab);
}

// Subtab button click handlers - simple event delegation
document.addEventListener('click', function(e) {
    const button = e.target.closest('button.tab-button[data-subtab]');
    if (!button) return;
    
    // Skip if it's a main tab button
    if (button.hasAttribute('data-tab')) return;
    
    // Must be inside a tab panel
    if (!button.closest('.tab-panel[data-tab-panel]')) return;
    
    const subtabName = button.getAttribute('data-subtab');
    if (subtabName) {
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        // Always call switchSubtab, even if the button is already active
        // This ensures data is reloaded when clicking an already-active tab
        switchSubtab(subtabName);
    }
}, true);


// Helper function to strip asterisks from agent/partner names for value comparison
function stripAsterisk(text) {
    return text.replace(/\s*\*$/, '');
}

// Helper function to get current subtab name for agents tab
function getCurrentAgentsSubtab() {
    const activeSubtab = document.querySelector('.tab-panel[data-tab-panel="agents"] .tab-button.active');
    return activeSubtab ? activeSubtab.getAttribute('data-subtab') : 'profile';
}

// Helper function to get current subtab name for conversations tab
function getCurrentConversationsSubtab() {
    const activeSubtab = document.querySelector('.tab-panel[data-tab-panel="conversations"] .tab-button.active');
    return activeSubtab ? activeSubtab.getAttribute('data-subtab') : 'profile-conv';
}

// Helper function to check if agent has nontrivial content for a subtab
async function agentHasContent(agentName, subtabName) {
    try {
        if (subtabName === 'contacts') {
            return false;
        }
        if (subtabName === 'parameters') {
            // Check if agent has custom LLM or non-empty prompt
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration`);
            const data = await response.json();
            if (data.error) return false;
            const defaultLLM = data.available_llms?.find(llm => llm.is_default)?.value;
            const hasCustomLLM = data.llm && data.llm !== defaultLLM;
            const hasPrompt = data.prompt && data.prompt.trim().length > 0;
            return hasCustomLLM || hasPrompt;
        } else if (subtabName === 'memories') {
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memories`);
            const data = await response.json();
            if (data.error) return false;
            return data.memories && data.memories.length > 0;
        } else if (subtabName === 'intentions') {
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/intentions`);
            const data = await response.json();
            if (data.error) return false;
            return data.intentions && data.intentions.length > 0;
        } else if (subtabName === 'documents-agent') {
            // Check if agent has documents using the has_documents field from agents list
            // This is more efficient than making a separate API call
            const response = await fetchWithAuth(`${API_BASE}/agents`);
            const data = await response.json();
            if (data.error) return false;
            const agent = data.agents?.find(a => a.config_name === agentName);
            return agent?.has_documents || false;
        } else if (subtabName === 'plans') {
            // Check if agent has plans using the has_plans field from agents list
            // This is more efficient than checking all conversations
            const response = await fetchWithAuth(`${API_BASE}/agents`);
            const data = await response.json();
            if (data.error) return false;
            const agent = data.agents?.find(a => a.config_name === agentName);
            return agent?.has_plans || false;
        } else if (subtabName === 'profile') {
            // Check if agent has profile content (non-empty profile fields)
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/profile`);
            const data = await response.json();
            if (data.error) return false;
            // Check if any profile fields are set (excluding telegram_id which is always present)
            const hasFirstName = data.first_name && data.first_name.trim().length > 0;
            const hasLastName = data.last_name && data.last_name.trim().length > 0;
            const hasUsername = data.username && data.username.trim().length > 0;
            const hasBio = data.bio && data.bio.trim().length > 0;
            const hasBirthday = data.birthday && (data.birthday.day || data.birthday.month);
            const hasProfilePhoto = data.profile_photo && data.profile_photo.length > 0;
            return hasFirstName || hasLastName || hasUsername || hasBio || hasBirthday || hasProfilePhoto;
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return false;
        }
        console.error(`Error checking content for agent ${agentName}, subtab ${subtabName}:`, error);
    }
    return false;
}

// Helper function to check if conversation partner has nontrivial content for a subtab
async function partnerHasContent(agentName, userId, subtabName) {
    try {
        if (subtabName === 'profile-conv') {
            return false;
        }
        if (subtabName === 'notes-conv') {
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/notes/${userId}`);
            const data = await response.json();
            if (data.error) return false;
            return data.notes && data.notes.length > 0;
        } else if (subtabName === 'conversation-parameters') {
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation-parameters/${userId}`);
            const data = await response.json();
            if (data.error) return false;
            const agentDefaultLLM = data.agent_default_llm;
            const hasLlmOverride = data.conversation_llm && data.conversation_llm !== agentDefaultLLM;
            // Prefer explicit override indicator if the API provides it; otherwise fall back
            // to the effective value (best-effort).
            const hasGaggedOverride = Object.prototype.hasOwnProperty.call(data, 'gagged_override')
                ? data.gagged_override !== null
                : (
                    // Back-compat: if server provides both, infer override from mismatch.
                    Object.prototype.hasOwnProperty.call(data, 'agent_is_gagged')
                        ? data.is_gagged !== data.agent_is_gagged
                        : false
                );
            return hasLlmOverride || hasGaggedOverride;
        } else if (subtabName === 'plans') {
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/plans/${userId}`);
            const data = await response.json();
            if (data.error) return false;
            return data.plans && data.plans.length > 0;
        } else if (subtabName === 'conversation') {
            // For conversation subtab, check summaries locally (no Telegram API call)
            // This is handled by the batch endpoint in loadConversationPartners
            // Fallback: check summaries endpoint (still no Telegram API call)
            const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/summaries/${userId}`);
            const data = await response.json();
            if (data.error) return false;
            return data.summaries && data.summaries.length > 0;
        }
        // XSend subtab doesn't need asterisk
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return false;
        }
        console.error(`Error checking content for partner ${userId}, subtab ${subtabName}:`, error);
    }
    return false;
}

async function toggleAgentDisabled(agentName, isDisabled) {
    // Get button reference and store original state
    const button = document.getElementById('toggle-agent-button');
    if (!button) {
        console.error('Toggle agent button not found');
        return;
    }
    
    const originalText = button.textContent;
    const originalBackground = button.style.background;
    const originalCursor = button.style.cursor;
    const originalOpacity = button.style.opacity;
    
    // Immediately disable button and show loading state
    button.disabled = true;
    button.style.cursor = 'not-allowed';
    button.style.opacity = '0.6';
    button.textContent = isDisabled ? 'Disabling...' : 'Enabling...';
    
    // Helper function to restore button state
    const restoreButton = () => {
        button.disabled = false;
        button.style.cursor = originalCursor || 'pointer';
        button.style.opacity = originalOpacity || '1';
        button.textContent = originalText;
    };
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/disabled`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_disabled: isDisabled })
        });
        const data = await response.json();
        if (data.success) {
            if (!isDisabled) {
                // If enabling, trigger login check
                await handleAgentLogin(agentName);
                // loadAgentConfiguration will be called by handleAgentLogin to update the button
            } else {
                // Refresh configuration to update UI
                loadAgentConfiguration(agentName);
                // Also refresh agent list to show/hide (disabled)
                loadAgents().then(() => {
                    document.getElementById('agents-agent-select').value = agentName;
                });
                // loadAgentConfiguration will update the button with correct state
            }
        } else {
            restoreButton();
            alert('Error updating agent status: ' + data.error);
        }
    } catch (error) {
        console.error('Error toggling agent status:', error);
        restoreButton();
        alert('Error toggling agent status');
    }
}

async function handleAgentLogin(agentName) {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/login`, {
            method: 'POST'
        });
        const data = await response.json();
        
        if (data.status === 'authenticated') {
            alert('Agent is authenticated and enabled.');
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        } else if (data.status === 'needs_code') {
            const code = prompt('Enter the Telegram verification code for this agent:');
            if (code) {
                await submitAgentLoginCode(agentName, code);
            } else {
                await cancelAgentLogin(agentName);
            }
        } else if (data.status === 'needs_password') {
            const password = prompt('Enter the 2FA password for this agent:');
            if (password) {
                await submitAgentLoginPassword(agentName, password);
            } else {
                await cancelAgentLogin(agentName);
            }
        } else if (data.error) {
            alert('Login error: ' + data.error);
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        } else {
            // Handle unexpected status values
            console.error('Unexpected login status:', data.status, data);
            alert('Unexpected response from login API. Status: ' + (data.status || 'unknown'));
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        }
    } catch (error) {
        console.error('Error during agent login:', error);
        alert('Error during agent login');
        loadAgentConfiguration(agentName);
        loadAgents().then(() => {
            document.getElementById('agents-agent-select').value = agentName;
        });
    }
}

async function submitAgentLoginCode(agentName, code) {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/login/code`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: code })
        });
        const data = await response.json();
        
        if (data.status === 'authenticated') {
            alert('Agent successfully authenticated!');
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        } else if (data.status === 'needs_password') {
            const password = prompt('Enter the 2FA password for this agent:');
            if (password) {
                await submitAgentLoginPassword(agentName, password);
            } else {
                await cancelAgentLogin(agentName);
            }
        } else if (data.error) {
            alert('Error: ' + data.error);
            await handleAgentLogin(agentName); // Let them try again
        } else {
            // Handle unexpected status values
            console.error('Unexpected login code status:', data.status, data);
            alert('Unexpected response from login code API. Status: ' + (data.status || 'unknown'));
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        }
    } catch (error) {
        console.error('Error submitting code:', error);
        alert('Error submitting code');
        loadAgentConfiguration(agentName);
        loadAgents().then(() => {
            document.getElementById('agents-agent-select').value = agentName;
        });
    }
}

async function submitAgentLoginPassword(agentName, password) {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/login/password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });
        const data = await response.json();
        
        if (data.status === 'authenticated') {
            alert('Agent successfully authenticated!');
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        } else if (data.error) {
            alert('Error: ' + data.error);
            await handleAgentLogin(agentName); // Let them try again
        } else {
            // Handle unexpected status values
            console.error('Unexpected login password status:', data.status, data);
            alert('Unexpected response from login password API. Status: ' + (data.status || 'unknown'));
            loadAgentConfiguration(agentName);
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
        }
    } catch (error) {
        console.error('Error submitting password:', error);
        alert('Error submitting password');
        loadAgentConfiguration(agentName);
        loadAgents().then(() => {
            document.getElementById('agents-agent-select').value = agentName;
        });
    }
}

async function cancelAgentLogin(agentName) {
    await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/login/cancel`, {
        method: 'POST'
    });
    loadAgentConfiguration(agentName);
    loadAgents().then(() => {
        document.getElementById('agents-agent-select').value = agentName;
    });
}

async function renameAgentConfig(agentName, newName) {
    if (!newName) {
        newName = prompt('Enter new config name (without .md):', agentName);
    }
    if (!newName || newName === agentName) {
        loadAgentConfiguration(agentName); // Refresh to revert input
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/rename`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_config_name: newName })
        });
        const data = await response.json();
        if (data.success) {
            const newConfigName = data.new_config_name;
            await loadAgents();
            document.getElementById('agents-agent-select').value = newConfigName;
            loadAgentConfiguration(newConfigName);
        } else {
            alert('Error renaming agent: ' + data.error);
            loadAgentConfiguration(agentName);
        }
    } catch (error) {
        console.error('Error renaming agent:', error);
        alert('Error renaming agent');
        loadAgentConfiguration(agentName);
    }
}

async function moveAgentConfigDirectory(agentName, newConfigDirectory, currentConfigDirectory) {
    // Reset dropdown to current value first (will be updated on success)
    const select = document.getElementById('agent-config-directory-select');
    if (select) {
        select.value = currentConfigDirectory;
    }

    if (!newConfigDirectory || newConfigDirectory === currentConfigDirectory) {
        return;
    }

    // Show confirmation dialog
    const confirmed = confirm(
        `Are you sure you want to move agent "${agentName}" from config directory "${currentConfigDirectory}" to "${newConfigDirectory}"?\n\n` +
        `This will move:\n` +
        `- ${currentConfigDirectory}/agents/${agentName}.md\n` +
        `- ${currentConfigDirectory}/agents/${agentName}/\n\n` +
        `to the new config directory.`
    );

    if (!confirmed) {
        // Reset dropdown to current value
        if (select) {
            select.value = currentConfigDirectory;
        }
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/move-directory`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config_directory: newConfigDirectory })
        });
        const data = await response.json();
        if (data.success) {
            alert('Agent config directory moved successfully.');
            // Reload agents list and configuration
            await loadAgents();
            loadAgentConfiguration(agentName);
        } else {
            alert('Error moving config directory: ' + data.error);
            // Reset dropdown to current value
            if (select) {
                select.value = currentConfigDirectory;
            }
            loadAgentConfiguration(agentName);
        }
    } catch (error) {
        console.error('Error moving config directory:', error);
        alert('Error moving config directory: ' + error);
        // Reset dropdown to current value
        if (select) {
            select.value = currentConfigDirectory;
        }
        loadAgentConfiguration(agentName);
    }
}

async function deleteAgent(agentName, displayName) {
    const confirmation = prompt(`To delete agent "${displayName}", type DELETE ${displayName}:`);
    if (confirmation !== `DELETE ${displayName}`) {
        if (confirmation !== null) alert('Incorrect confirmation string.');
        return;
    }

    try {
        const deleteButton = document.getElementById('delete-agent-button');
        const deleteStatus = document.getElementById('agent-delete-status');
        if (deleteButton) {
            deleteButton.disabled = true;
            deleteButton.style.opacity = '0.7';
            deleteButton.textContent = 'Deleting...';
        }
        if (deleteStatus) {
            deleteStatus.textContent = 'Delete request in progress...';
        }

        const deleteUrl = `${API_BASE}/agents/${encodeURIComponent(agentName)}/delete?confirmation=${encodeURIComponent(confirmation)}`;
        const response = await fetch(deleteUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmation: confirmation }),
            credentials: 'same-origin',
            cache: 'no-store'
        });
        const data = await response.json();
        if (response.ok && data.success) {
            alert('Agent deleted successfully.');
            await loadAgents();
            document.getElementById('agents-agent-select').value = '';
            document.getElementById('parameters-container').innerHTML = '<div class="loading">Select an agent to configure</div>';
        } else {
            alert('Error deleting agent: ' + (data.error || `HTTP ${response.status}`));
        }
    } catch (error) {
        alert('Error deleting agent');
    } finally {
        const deleteButton = document.getElementById('delete-agent-button');
        const deleteStatus = document.getElementById('agent-delete-status');
        if (deleteButton) {
            deleteButton.disabled = false;
            deleteButton.style.opacity = '';
            deleteButton.textContent = 'Delete Agent';
        }
        if (deleteStatus) {
            deleteStatus.textContent = '';
        }
    }
}
async function loadAllDestinationsForMove(selectId, currentConfigDir, currentAgentConfigName) {
    try {
        // Load config directories and agents in parallel
        const [configDirsResponse, agentsResponse] = await Promise.all([
            fetch('/admin/api/config-directories'),
            fetchWithAuth(`${API_BASE}/agents`)
        ]);
        
        const configDirsData = await configDirsResponse.json();
        const agentsData = await agentsResponse.json();
        
        const select = document.getElementById(selectId);
        if (!select) return;
        
        select.innerHTML = '<option value="">Move to...</option>';
        
        // Add global config directories
        configDirsData.directories.forEach(dir => {
            const option = document.createElement('option');
            option.value = `global|${dir.path}`;
            option.textContent = `Global: ${dir.display_path}`;
            // Mark current location if it's a global doc
            if (currentConfigDir === dir.path && !currentAgentConfigName) {
                option.selected = true;
                option.disabled = true;
                option.textContent += ' (current)';
            }
            select.appendChild(option);
        });
        
        // Add agents
        agentsData.agents.forEach(agent => {
            const option = document.createElement('option');
            const agentConfigDir = agent.config_directory || '';
            option.value = `agent|${agentConfigDir}|${agent.config_name}`;
            const displayName = agentConfigDir ? 
                `Agent: ${agent.name} (${agentConfigDir})` : 
                `Agent: ${agent.name}`;
            option.textContent = displayName;
            // Mark current location if it's this agent's doc
            const currentConfigDirNormalized = currentConfigDir || '';
            const agentConfigDirNormalized = agentConfigDir || '';
            if (currentAgentConfigName === agent.config_name && 
                currentConfigDirNormalized === agentConfigDirNormalized) {
                option.selected = true;
                option.disabled = true;
                option.textContent += ' (current)';
            }
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading destinations:', error);
    }
}

async function loadConfigDirectoriesForMove(selectId) {
    try {
        const response = await fetch('/admin/api/config-directories');
        const data = await response.json();
        
        const select = document.getElementById(selectId);
        if (!select) return;
        
        select.innerHTML = '<option value="">Select config directory...</option>';
        data.directories.forEach(dir => {
            select.appendChild(createOption(dir.path, dir.display_path));
        });
    } catch (error) {
        console.error('Error loading config directories:', error);
    }
}

async function loadAgentsForMove(selectId) {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents`);
        const data = await response.json();
        
        const select = document.getElementById(selectId);
        if (!select) return;
        
        select.innerHTML = '<option value="">Select agent...</option>';
        data.agents.forEach(agent => {
            select.appendChild(createOption(agent.config_name, agent.name));
        });
    } catch (error) {
        console.error('Error loading agents:', error);
    }
}

const LLM_COMBOBOX_SEPARATOR_VALUE = '__llm_all__';
const LLM_COMBOBOX_SEPARATOR_LABEL = '--- All models ---';

function buildLLMComboboxOptions(availableLLMs, currentValue) {
    const normalizedValue = (currentValue || '').trim();
    const options = [];
    const seen = new Set();

    if (normalizedValue) {
        const selected = availableLLMs.find(llm => (llm.value || '') === normalizedValue);
        if (selected) {
            options.push(selected);
            seen.add(selected.value);
        } else {
            options.push({ value: normalizedValue, label: normalizedValue });
            seen.add(normalizedValue);
        }

        const needle = normalizedValue.toLowerCase();
        availableLLMs.forEach(llm => {
            const value = llm.value || '';
            const label = llm.label || value;
            if (!seen.has(value) && (value.toLowerCase().includes(needle) || label.toLowerCase().includes(needle))) {
                options.push(llm);
                seen.add(value);
            }
        });

        options.push({
            value: LLM_COMBOBOX_SEPARATOR_VALUE,
            label: LLM_COMBOBOX_SEPARATOR_LABEL,
            is_separator: true,
        });
    }

    availableLLMs.forEach(llm => {
        const value = llm.value || '';
        if (!seen.has(value)) {
            options.push(llm);
            seen.add(value);
        }
    });

    return options;
}

function renderLLMComboboxOptions(dropdownEl, options, includeDefaultMarker, onSelect) {
    dropdownEl.innerHTML = '';
    options.forEach(llm => {
        const item = document.createElement('div');
        item.style.padding = '6px 10px';
        item.style.cursor = llm.is_separator ? 'default' : 'pointer';
        item.style.fontSize = '14px';
        item.style.color = llm.is_separator ? '#888' : '#333';
        item.style.borderTop = llm.is_separator ? '1px solid #eee' : 'none';
        item.style.marginTop = llm.is_separator ? '4px' : '0';
        item.style.paddingTop = llm.is_separator ? '8px' : '6px';

        let label = llm.label || llm.value || '';
        if (llm.is_separator) {
            label = LLM_COMBOBOX_SEPARATOR_LABEL;
        } else if (includeDefaultMarker && llm.is_default) {
            label = `${label} *`;
        }

        item.textContent = label;
        if (!llm.is_separator) {
            item.addEventListener('mousedown', event => {
                event.preventDefault();
                onSelect(llm.value || '');
            });
        }
        dropdownEl.appendChild(item);
    });
}

function setupLLMCombobox(inputEl, availableLLMs, options = {}) {
    if (!inputEl) return;

    if (inputEl._llmComboboxCleanup) {
        inputEl._llmComboboxCleanup();
    }

    const includeDefaultMarker = Boolean(options.includeDefaultMarker);
    const onChange = options.onChange;
    const controller = new AbortController();
    const { signal } = controller;
    let isDestroyed = false;

    const dropdownEl = document.createElement('div');
    dropdownEl.style.position = 'absolute';
    dropdownEl.style.background = '#fff';
    dropdownEl.style.border = '1px solid #ddd';
    dropdownEl.style.borderRadius = '6px';
    dropdownEl.style.boxShadow = '0 8px 20px rgba(0,0,0,0.12)';
    dropdownEl.style.maxHeight = '260px';
    dropdownEl.style.overflowY = 'auto';
    dropdownEl.style.zIndex = '9999';
    dropdownEl.style.display = 'none';
    document.body.appendChild(dropdownEl);

    const cleanup = () => {
        if (isDestroyed) return;
        isDestroyed = true;
        controller.abort();
        if (observer) observer.disconnect();
        if (dropdownEl.parentNode) {
            dropdownEl.parentNode.removeChild(dropdownEl);
        }
        if (inputEl._llmComboboxCleanup === cleanup) {
            delete inputEl._llmComboboxCleanup;
        }
    };

    inputEl._llmComboboxCleanup = cleanup;

    const positionDropdown = () => {
        const rect = inputEl.getBoundingClientRect();
        dropdownEl.style.minWidth = `${rect.width}px`;
        dropdownEl.style.left = `${rect.left + window.scrollX}px`;
        dropdownEl.style.top = `${rect.bottom + window.scrollY + 4}px`;
    };

    const refreshOptions = () => {
        if (!document.body.contains(inputEl)) {
            cleanup();
            return;
        }
        const comboboxOptions = buildLLMComboboxOptions(availableLLMs || [], inputEl.value);
        renderLLMComboboxOptions(dropdownEl, comboboxOptions, includeDefaultMarker, (value) => {
            inputEl.value = value;
            inputEl.dataset.lastValidValue = value;
            dropdownEl.style.display = 'none';
            if (onChange) onChange(value);
        });
        positionDropdown();
        dropdownEl.style.display = comboboxOptions.length ? 'block' : 'none';
    };

    const closeDropdown = (event) => {
        if (!event || (event.target !== inputEl && !dropdownEl.contains(event.target))) {
            dropdownEl.style.display = 'none';
        }
    };

    const handleInputChange = () => {
        inputEl.dataset.lastValidValue = inputEl.value;
        closeDropdown();
        if (onChange) onChange(inputEl.value);
    };

    inputEl.dataset.lastValidValue = inputEl.value;
    inputEl.addEventListener('focus', refreshOptions, { signal });
    inputEl.addEventListener('input', refreshOptions, { signal });
    inputEl.addEventListener('change', handleInputChange, { signal });
    inputEl.addEventListener('blur', () => {
        const lastValue = inputEl.dataset.lastValidValue || '';
        if (inputEl.value !== lastValue) {
            handleInputChange();
            return;
        }
        closeDropdown();
    }, { signal });
    document.addEventListener('mousedown', closeDropdown, { signal });
    window.addEventListener('resize', () => {
        if (dropdownEl.style.display !== 'none') positionDropdown();
    }, { signal });
    window.addEventListener('scroll', () => {
        if (dropdownEl.style.display !== 'none') positionDropdown();
    }, { signal, capture: true });

    const observer = new MutationObserver(() => {
        if (!document.body.contains(inputEl)) {
            cleanup();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });
}

// Generic debounce utility
// Returns a debounced version of the provided function
function debounce(func, delay = 500) {
    let timeoutId;
    return function(...args) {
        clearTimeout(timeoutId);
        timeoutId = setTimeout(() => func.apply(this, args), delay);
    };
}

// Option creation utilities
function createOption(value, text, selected = false) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = text;
    if (selected) option.selected = true;
    return option;
}

function populateSelect(selectElement, options, selectedValue = null) {
    if (typeof selectElement === 'string') {
        selectElement = document.getElementById(selectElement);
    }
    if (!selectElement) return;
    
    selectElement.innerHTML = '';
    options.forEach(opt => {
        const option = createOption(
            opt.value, 
            opt.label || opt.text || opt.value,
            opt.value === selectedValue || opt.selected
        );
        selectElement.appendChild(option);
    });
}

// Display toggle utilities
function show(element, displayType = 'block') {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (element) {
        element.style.display = displayType;
    }
}

function hide(element) {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (element) {
        element.style.display = 'none';
    }
}

function toggle(element, shouldShow, displayType = 'block') {
    if (shouldShow) {
        show(element, displayType);
    } else {
        hide(element);
    }
}

// Auto-save utilities
function scheduleAutoSave(uniqueId, saveFunction, delay = 1000) {
    // Clear existing timer for this textarea
    if (autoSaveTimers[uniqueId]) {
        clearTimeout(autoSaveTimers[uniqueId]);
    }
    
    // Set status to "Typing..."
    if (savingStates[uniqueId]) {
        savingStates[uniqueId].textContent = 'Typing...';
    }
    
    // Set new timer
    autoSaveTimers[uniqueId] = setTimeout(() => {
        saveFunction();
    }, delay);
}

function setSaveStatus(uniqueId, status) {
    if (savingStates[uniqueId]) {
        savingStates[uniqueId].textContent = status;
    }
}

function registerAutoSaveElement(uniqueId, statusElement) {
    savingStates[uniqueId] = statusElement;
}

// Error and success message utilities
function showError(element, message) {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (element) {
        element.innerHTML = `<div class="error">Error: ${escapeHtml(message)}</div>`;
    }
}

function showSuccess(element, message) {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (element) {
        element.innerHTML = `<div class="success">${escapeHtml(message)}</div>`;
    }
}

// Loading state management utilities
function showLoading(element, message = 'Loading...') {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (element) {
        element.innerHTML = `<div class="loading">${escapeHtml(message)}</div>`;
    }
}

function showLoadingSpinner(element, message = 'Loading...') {
    if (typeof element === 'string') {
        element = document.getElementById(element);
    }
    if (element) {
        element.innerHTML = `
            <div class="loading-spinner" style="text-align: center; padding: 60px 20px;">
                <div style="display: inline-block; width: 50px; height: 50px; border: 4px solid #f3f3f3; 
                     border-top: 4px solid #007bff; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                <div style="margin-top: 16px; color: #666; font-size: 14px;">${escapeHtml(message)}</div>
            </div>
            <style>@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }</style>
        `;
    }
}

// Format UTC timestamp to agent timezone for display
// Matches the format used in prompts: "YYYY-MM-DD HH:MM:SS TZ"
function formatTimestamp(utcTimestamp, timezone) {
    if (!utcTimestamp) return 'N/A';
    try {
        const date = new Date(utcTimestamp);
        // Use provided timezone or fall back to browser timezone
        const tz = timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
        const formatted = date.toLocaleString('en-US', {
            timeZone: tz,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false,
            timeZoneName: 'short'
        });
        // Convert from "MM/DD/YYYY, HH:MM:SS TZ" to "YYYY-MM-DD HH:MM:SS TZ"
        const parts = formatted.match(/(\d{2})\/(\d{2})\/(\d{4}),?\s+(\d{2}:\d{2}:\d{2})\s+(.+)/);
        if (parts) {
            return `${parts[3]}-${parts[1]}-${parts[2]} ${parts[4]} ${parts[5]}`;
        }
        return formatted;
    } catch (e) {
        return utcTimestamp;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function escJsAttr(str) {
    if (!str) return '';
    return str.replace(/\\/g, '\\\\')
              .replace(/\n/g, '\\n')
              .replace(/\r/g, '\\r')
              .replace(/'/g, "\\'")
              .replace(/"/g, '&quot;')
              .replace(/`/g, '\\`');
}

function escJsTemplate(str) {
    if (!str) return '';
    return str.replace(/\\/g, '\\\\')
              .replace(/`/g, '\\`')
              .replace(/\$/g, '\\$');
}

function getTextareaMinHeight(textarea) {
    const minHeight = parseFloat(window.getComputedStyle(textarea).minHeight);
    return Number.isFinite(minHeight) ? minHeight : 0;
}

function getTextareaHeight(textarea) {
    const height = parseFloat(window.getComputedStyle(textarea).height);
    return Number.isFinite(height) ? height : 0;
}

function resetTextareaHeight(textarea) {
    if (!textarea) return;
    textarea.style.height = '';
    textarea.style.overflowY = 'hidden';
}

function autoGrowTextarea(textarea) {
    if (!textarea) return;
    textarea.style.overflowY = 'hidden';
    const minHeight = getTextareaMinHeight(textarea);
    const overflow = textarea.scrollHeight - textarea.clientHeight;
    if (overflow > 1) {
        const nextHeight = Math.max(textarea.offsetHeight + overflow, minHeight);
        textarea.style.height = `${nextHeight}px`;
    }
}

function initializeTextareaAutoGrow(textarea) {
    if (!textarea || textarea.dataset.autogrowInitialized) return;
    textarea.dataset.autogrowInitialized = 'true';
    autoGrowTextarea(textarea);
}

function refreshAutoGrowTextareas(root = document) {
    const textareas = root.querySelectorAll('textarea');
    textareas.forEach(initializeTextareaAutoGrow);
    textareas.forEach(autoGrowTextarea);
}

function scheduleAutoGrowRefresh(root = document) {
    window.requestAnimationFrame(() => refreshAutoGrowTextareas(root));
}

document.addEventListener('input', (event) => {
    const target = event.target;
    if (target && target.tagName === 'TEXTAREA') {
        autoGrowTextarea(target);
    }
});

document.addEventListener('change', (event) => {
    const target = event.target;
    if (target && target.tagName === 'TEXTAREA') {
        autoGrowTextarea(target);
    }
});

document.addEventListener('focusin', (event) => {
    const target = event.target;
    if (target && target.tagName === 'TEXTAREA') {
        autoGrowTextarea(target);
    }
});

const autoGrowObserver = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
            if (node.nodeType !== Node.ELEMENT_NODE) return;
            if (node.tagName === 'TEXTAREA') {
                initializeTextareaAutoGrow(node);
            } else if (node.querySelectorAll) {
                node.querySelectorAll('textarea').forEach(initializeTextareaAutoGrow);
            }
        });
    });
});

autoGrowObserver.observe(document.body, { childList: true, subtree: true });

// Ensure subtab bars are visible for active tab panels on page load
document.addEventListener('DOMContentLoaded', function() {
    const activePanel = document.querySelector('.tab-panel.active');
    if (activePanel) {
        const subtabBar = activePanel.querySelector('.tab-bar');
        if (subtabBar) {
            subtabBar.style.display = 'flex';
            subtabBar.style.visibility = 'visible';
            subtabBar.style.opacity = '1';
        }
    }
    refreshAutoGrowTextareas();
});
