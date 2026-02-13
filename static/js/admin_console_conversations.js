// Admin Console Conversations - Conversation management, messages, and translation
// Copyright (c) 2025-2026 Cindy's World LLC and contributors
// Licensed under the MIT License. See LICENSE.md for details.

async function loadRecentConversations() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/recent-conversations`);
        const data = await response.json();
        if (data.error) {
            console.error('Error loading recent conversations:', data.error);
            return;
        }
        
        const conversations = data.conversations || [];
        const select = document.getElementById('recent-conversations-select');
        if (!select) {
            return;
        }
        
        // Check if we're on work-queue subtab
        const subtabName = getCurrentConversationsSubtab();
        const isWorkQueueSubtab = subtabName === 'work-queue';
        
        // Clear existing options except the first one
        select.innerHTML = '<option value="">Select a recent conversation...</option>';
        
        conversations.forEach(conv => {
            const option = document.createElement('option');
            // Format: "Agent Name / Channel Name (date)"
            const date = conv.last_send_time ? new Date(conv.last_send_time).toLocaleDateString() : '';
            let displayText = date 
                ? `${conv.agent_name} / ${conv.channel_name} (${date})`
                : `${conv.agent_name} / ${conv.channel_name}`;
            
            // Add asterisk if on work-queue subtab and conversation has work queue
            if (isWorkQueueSubtab && conv.has_work_queue) {
                displayText += ' *';
            }
            
            option.textContent = displayText;
            // Store data in value as JSON
            option.value = JSON.stringify({
                agent_config_name: conv.agent_config_name,
                channel_id: conv.channel_id
            });
            select.appendChild(option);
        });
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        console.error('Error loading recent conversations:', error);
    }
}

let newAgentFormInitialized = false;
let newAgentConfigNames = new Set();

async function initializeNewAgentForm() {
    if (newAgentFormInitialized) {
        await refreshNewAgentConfigNames();
        updateNewAgentCreateState();
        return;
    }
    newAgentFormInitialized = true;
    const configSelect = document.getElementById('new-agent-config-directory');
    const configNameInput = document.getElementById('new-agent-config-name');
    const displayNameInput = document.getElementById('new-agent-display-name');

    try {
        const [configResponse, agentsResponse] = await Promise.all([
            fetchWithAuth(`${API_BASE}/config-directories`),
            fetchWithAuth(`${API_BASE}/agents`)
        ]);
        const configData = await configResponse.json();
        const agentsData = await agentsResponse.json();
        newAgentConfigNames = new Set((agentsData.agents || []).map(agent => agent.config_name));

        if (configSelect) {
            configSelect.innerHTML = '<option value="">Choose a config directory...</option>';
            (configData.directories || []).forEach(dir => {
                const option = document.createElement('option');
                const path = (typeof dir === 'object') ? dir.path : dir;
                const display = (typeof dir === 'object') ? dir.display_path : dir;
                option.value = path;
                option.textContent = display;
                configSelect.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Error initializing new agent form:', error);
        alert('Failed to load config directories.');
    }

    const handleConfigChange = async () => {
        const configDir = configSelect?.value || '';
        if (!configDir) {
            updateNewAgentCreateState();
            return;
        }
        await loadNewAgentDefaults(configDir);
        updateNewAgentCreateState();
    };

    configSelect?.addEventListener('change', handleConfigChange);
    configNameInput?.addEventListener('input', updateNewAgentCreateState);
    displayNameInput?.addEventListener('input', updateNewAgentCreateState);
    updateNewAgentCreateState();
}

async function refreshNewAgentConfigNames() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents`);
        const data = await response.json();
        newAgentConfigNames = new Set((data.agents || []).map(agent => agent.config_name));
    } catch (error) {
        console.error('Error refreshing agent list for new agent form:', error);
    }
}

async function loadNewAgentDefaults(configDirectory) {
    try {
        const response = await fetchWithAuth(
            `${API_BASE}/agents/new-defaults?config_directory=${encodeURIComponent(configDirectory)}`
        );
        const data = await response.json();
        if (data.error) {
            console.error('Error loading new agent defaults:', data.error);
            return;
        }

        const defaults = data.defaults || {};
        document.getElementById('new-agent-config-name').value = defaults.config_name || 'Untitled';
        document.getElementById('new-agent-display-name').value = defaults.name || 'Untitled Agent';
        document.getElementById('new-agent-phone').value = defaults.phone || '+1234567890';
        document.getElementById('new-agent-prompt').value = defaults.instructions || '';
        document.getElementById('new-agent-role-prompts').value = (defaults.role_prompt_names || []).join('\n');
        document.getElementById('new-agent-sticker-sets').value = (defaults.sticker_set_names || []).join('\n');
        document.getElementById('new-agent-explicit-stickers').value = (defaults.explicit_stickers || []).join('\n');
        document.getElementById('new-agent-daily-schedule').value = defaults.daily_schedule_description || '';
        document.getElementById('new-agent-reset-context').checked = !!defaults.reset_context_on_first_message;
        document.getElementById('new-agent-gagged').checked = !!defaults.is_gagged;
        document.getElementById('new-agent-start-typing-delay').value = defaults.start_typing_delay ?? '';
        document.getElementById('new-agent-typing-speed').value = defaults.typing_speed ?? '';

        const llmInput = document.getElementById('new-agent-llm');
        if (llmInput) {
            llmInput.value = defaults.llm || '';
        }
        const llmOptions = document.getElementById('new-agent-llm-options');
        if (llmOptions) {
            llmOptions.innerHTML = '';
            (data.available_llms || []).forEach(llm => {
                const option = document.createElement('option');
                option.value = llm.value;
                llmOptions.appendChild(option);
            });
        }
        const timezoneSelect = document.getElementById('new-agent-timezone');
        if (timezoneSelect) {
            timezoneSelect.innerHTML = '<option value="">Default</option>';
            (data.available_timezones || []).forEach(tz => {
                const option = document.createElement('option');
                option.value = tz.value;
                option.textContent = tz.label;
                if (defaults.timezone && tz.value === defaults.timezone) {
                    option.selected = true;
                }
                timezoneSelect.appendChild(option);
            });
        }
    } catch (error) {
        console.error('Error loading new agent defaults:', error);
    }
}

function updateNewAgentCreateState() {
    const configDir = document.getElementById('new-agent-config-directory')?.value.trim();
    const configName = document.getElementById('new-agent-config-name')?.value.trim();
    const displayName = document.getElementById('new-agent-display-name')?.value.trim();
    const statusEl = document.getElementById('new-agent-config-name-status');
    const createBtn = document.getElementById('new-agent-create-btn');

    let statusText = '';
    let isValid = true;

    if (!configDir) {
        isValid = false;
    }

    if (!configName) {
        isValid = false;
        statusText = 'Config name is required.';
    } else if (!/^[A-Za-z0-9]+$/.test(configName)) {
        isValid = false;
        statusText = 'Config name must be alphanumeric.';
    } else if (newAgentConfigNames.has(configName)) {
        isValid = false;
        statusText = 'Config name already exists.';
    }

    if (!displayName) {
        isValid = false;
    }

    if (statusEl) {
        statusEl.textContent = statusText;
        statusEl.style.color = statusText ? '#dc3545' : '#666';
    }
    if (createBtn) {
        createBtn.disabled = !isValid;
        createBtn.style.opacity = isValid ? '1' : '0.6';
        createBtn.style.cursor = isValid ? 'pointer' : 'not-allowed';
    }
}

async function createNewAgentFromForm() {
    const createBtn = document.getElementById('new-agent-create-btn');
    const configDirectory = document.getElementById('new-agent-config-directory')?.value.trim();
    const configName = document.getElementById('new-agent-config-name')?.value.trim();
    const displayName = document.getElementById('new-agent-display-name')?.value.trim();

    if (!configDirectory || !configName || !displayName) {
        updateNewAgentCreateState();
        return;
    }

    try {
        if (createBtn) {
            createBtn.disabled = true;
            createBtn.textContent = 'Creating...';
        }

        const payload = {
            config_directory: configDirectory,
            config_name: configName,
            name: displayName,
            phone: document.getElementById('new-agent-phone')?.value.trim(),
            instructions: document.getElementById('new-agent-prompt')?.value,
            role_prompt_names: document.getElementById('new-agent-role-prompts')?.value,
            llm: document.getElementById('new-agent-llm')?.value.trim(),
            timezone: document.getElementById('new-agent-timezone')?.value.trim(),
            sticker_set_names: document.getElementById('new-agent-sticker-sets')?.value,
            explicit_stickers: document.getElementById('new-agent-explicit-stickers')?.value,
            daily_schedule_description: document.getElementById('new-agent-daily-schedule')?.value,
            reset_context_on_first_message: document.getElementById('new-agent-reset-context')?.checked,
            start_typing_delay: document.getElementById('new-agent-start-typing-delay')?.value,
            typing_speed: document.getElementById('new-agent-typing-speed')?.value,
            is_gagged: document.getElementById('new-agent-gagged')?.checked
        };

        const response = await fetchWithAuth(`${API_BASE}/agents/new`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (data.success) {
            const agentsTabButton = document.querySelector('.tab-button[data-tab="agents"]');
            if (agentsTabButton) {
                agentsTabButton.click();
            }
            await loadAgents();
            newAgentConfigNames.add(configName);
            const agentSelect = document.getElementById('agents-agent-select');
            if (agentSelect) {
                agentSelect.value = data.config_name;
            }
            switchSubtab('parameters');
            agentSelect?.dispatchEvent(new Event('change'));
        } else {
            alert(data.error || 'Failed to create agent.');
            if (data.error && data.error.includes('already exists')) {
                newAgentConfigNames.add(configName);
            }
        }
    } catch (error) {
        console.error('Error creating new agent:', error);
        alert('Failed to create agent.');
    } finally {
        if (createBtn) {
            createBtn.disabled = false;
            createBtn.textContent = 'Create';
        }
    }
}

// Load conversation partners
async function loadConversationPartners(agentName, subtab, forceRefresh = false) {
    try {
        const refreshParam = forceRefresh ? '?refresh=true' : '';
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation-partners${refreshParam}`);
        const data = await response.json();
        if (data.error) {
            console.error('Error loading conversation partners:', data.error);
            return;
        }
        
        const partners = data.partners || [];
        const select = document.getElementById('conversations-partner-select');
        if (select) {
            const currentValue = select.value;
            const subtabName = getCurrentConversationsSubtab();
            
            // Check content for each partner using batch endpoint
            const partnerContentChecks = {};
            const userIds = partners.map(p => p.user_id || p);
            
            // Map subtab names to content check keys
            const subtabToKey = {
                'notes-conv': 'notes',
                // "conversation parameters" asterisk marker is driven by DB-backed per-conversation overrides.
                'conversation-parameters': 'conversation_parameters',
                'plans': 'plans',
                'conversation': 'conversation',  // Special case, uses different endpoint
                'work-queue': 'work_queue'
            };
            
            if (subtabName === 'profile-conv') {
                userIds.forEach(userId => {
                    partnerContentChecks[userId] = false;
                });
            } else if (subtabName === 'conversation') {
                // For conversation subtab, use conversation-content-check endpoint
                try {
                    const batchResponse = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation-content-check`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ user_ids: userIds })
                    });
                    const batchData = await batchResponse.json();
                    if (batchData.content_checks) {
                        Object.assign(partnerContentChecks, batchData.content_checks);
                    }
                } catch (error) {
                    console.warn('Error checking conversation content batch:', error);
                    // Fall back to individual checks if batch fails
                    await Promise.all(partners.map(async (partner) => {
                        const userId = partner.user_id || partner;
                        partnerContentChecks[userId] = await partnerHasContent(agentName, userId, subtabName);
                    }));
                }
            } else {
                // For notes, conversation-parameters, plans, and work-queue, use partner-content-check endpoint
                try {
                    const batchResponse = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/partner-content-check`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({ user_ids: userIds })
                    });
                    const batchData = await batchResponse.json();
                    if (batchData.content_checks) {
                        // Map the batch response to the format expected by the frontend
                        const contentKey = subtabToKey[subtabName];
                        if (contentKey) {
                            for (const userId of userIds) {
                                const checks = batchData.content_checks[userId];
                                if (checks) {
                                    // Back-compat: older servers only returned "conversation_llm" for the
                                    // conversation-parameters subtab.
                                    if (subtabName === 'conversation-parameters') {
                                        partnerContentChecks[userId] = !!(
                                            checks.conversation_parameters || checks.conversation_llm
                                        );
                                    } else {
                                        partnerContentChecks[userId] = checks[contentKey] || false;
                                    }
                                } else {
                                    partnerContentChecks[userId] = false;
                                }
                            }
                        }
                    }
                } catch (error) {
                    console.warn('Error checking partner content batch:', error);
                    // Fall back to individual checks if batch fails
                    await Promise.all(partners.map(async (partner) => {
                        const userId = partner.user_id || partner;
                        partnerContentChecks[userId] = await partnerHasContent(agentName, userId, subtabName);
                    }));
                }
            }
            
            select.innerHTML = '<option value="">Choose a partner...</option>';
            partners.forEach(partner => {
                const option = document.createElement('option');
                const userId = partner.user_id || partner;
                option.value = userId;
                // Display format: "Name (user_id) [@username]" or "Name (user_id)" or just "user_id"
                // Check for both null/undefined and empty string
                const hasName = partner.name && partner.name.trim().length > 0;
                let displayName = hasName ? `${partner.name} (${userId})` : userId;
                
                // Add Telegram username if available
                if (partner.username) {
                    displayName += ` [@${partner.username}]`;
                }
                
                // Add asterisk if partner has content for current subtab
                if (partnerContentChecks[userId]) {
                    displayName += ' *';
                }
                option.textContent = displayName;
                select.appendChild(option);
            });
            // When setting value, strip asterisk if present for comparison
            if (currentValue) {
                const strippedValue = stripAsterisk(currentValue);
                select.value = strippedValue;
            }
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        console.error('Error loading conversation partners:', error);
    }
}

// Functions for Parameters subtabs
function loadNotesForPartner() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
    
    if (!agentName || !userId) {
        alert('Please select an agent and enter/select a conversation partner');
        return;
    }
    
    const container = document.getElementById('notes-conv-container');
    showLoading(container, 'Loading notes...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/notes/${userId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const notes = data.notes || [];
            let html = '<div style="margin-bottom: 16px;"><button onclick="createNewNoteForPartner(\'' + escJsAttr(agentName) + '\', \'' + escJsAttr(userId) + '\')" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">+ Add New Note</button></div>';
            
            if (notes.length === 0) {
                html += '<div class="placeholder-card">No notes found for this user.</div>';
                container.innerHTML = html;
                return;
            }
            
            html += notes.map(note => `
                <div class="memory-item" style="background: white; padding: 16px; margin-bottom: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                        <div>
                            <strong>ID:</strong> ${escapeHtml(note.id || 'N/A')}<br>
                            <strong>Created:</strong> ${escapeHtml(note.created || 'N/A')}
                        </div>
                        <button onclick="deleteNote('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(note.id)}')" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Delete</button>
                    </div>
                    <textarea 
                        id="note-params-${userId}-${note.id}" 
                        style="width: 100%; min-height: 100px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical; box-sizing: border-box;"
                        oninput="scheduleNoteAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(note.id)}')"
                    >${escapeHtml(note.content || '')}</textarea>
                    <div id="note-status-${userId}-${note.id}" style="margin-top: 8px; font-size: 12px; color: #28a745;">Saved</div>
                </div>
            `).join('');
            container.innerHTML = html;
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading notes: ${escapeHtml(error)}</div>`;
        });
}

function createNewNoteForPartner(agentName, userId) {
    if (!agentName || !userId) {
        alert('Please select an agent and conversation partner');
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/notes/${userId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content: 'New note entry' })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error creating note: ' + data.error);
        } else {
            loadNotesForPartner();
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error creating note: ' + error);
    });
}

function loadConversationParameters() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
    
    if (!agentName || !userId) {
        alert('Please select an agent and enter/select a conversation partner');
        return;
    }
    
    const container = document.getElementById('conversation-parameters-container');
    showLoading(container, 'Loading conversation parameters...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation-parameters/${userId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const conversationLLM = data.conversation_llm || null;
            const agentDefaultLLM = data.agent_default_llm || '';
            const availableLLMs = data.available_llms || [];
            const isMuted = data.is_muted || false;
            const isGagged = data.is_gagged || false;
            const isBlocked = data.is_blocked || false;
            const isDmConversation = data.is_dm !== false;
            const canSend = data.can_send !== undefined ? data.can_send : true;
            const agentBlockedUser = data.agent_blocked_user || false;
            const userBlockedAgent = data.user_blocked_agent || false;
            let blockedStatusDetail = 'Not blocked';
            if (agentBlockedUser && userBlockedAgent) {
                blockedStatusDetail = 'Blocked by both sides';
            } else if (agentBlockedUser) {
                blockedStatusDetail = 'Blocked by agent';
            } else if (userBlockedAgent) {
                blockedStatusDetail = 'Blocked by user';
            }
            
            container.innerHTML = `
                <div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <h3>Conversation Parameters</h3>
                    
                    <div style="margin-bottom: 24px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: bold;">LLM Model:</label>
                        <p style="margin: 0 0 8px 0; color: #666; font-size: 14px;">Current: ${escapeHtml(conversationLLM || agentDefaultLLM + ' (agent default)')}</p>
                        <input 
                            id="conversation-llm-select" 
                            type="text" 
                            style="padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; width: 100%; max-width: 100%; box-sizing: border-box;" 
                            value="${escapeHtml(conversationLLM || agentDefaultLLM)}"
                            placeholder="Type or select an LLM model...">
                    </div>
                    
                    <div style="margin-bottom: 24px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: bold;">Muted:</label>
                        <label style="display: flex; align-items: center; cursor: pointer;">
                            <input type="checkbox" id="conversation-muted-toggle" ${isMuted ? 'checked' : ''} onchange="updateConversationParameters('${escJsAttr(agentName)}', '${escJsAttr(userId)}')" style="margin-right: 8px; width: 18px; height: 18px;">
                            <span>Mute notifications for this conversation</span>
                        </label>
                    </div>
                    
                    <div style="margin-bottom: 24px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: bold;">Gagged:</label>
                        <label style="display: flex; align-items: center; cursor: pointer;">
                            <input type="checkbox" id="conversation-gagged-toggle" ${isGagged ? 'checked' : ''} onchange="updateConversationParameters('${escJsAttr(agentName)}', '${escJsAttr(userId)}')" style="margin-right: 8px; width: 18px; height: 18px;">
                            <span>Gag this conversation (read messages but don't create received tasks)</span>
                        </label>
                    </div>
                    ${isDmConversation ? `
                    <div style="margin-bottom: 24px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: bold;">Blocked:</label>
                        <label style="display: flex; align-items: center; cursor: pointer;">
                            <input type="checkbox" id="conversation-blocked-toggle" ${isBlocked ? 'checked' : ''} onchange="updateConversationParameters('${escJsAttr(agentName)}', '${escJsAttr(userId)}')" style="margin-right: 8px; width: 18px; height: 18px;">
                            <span>Block or unblock this conversation partner</span>
                        </label>
                        <div style="margin-top: 6px; color: #666; font-size: 12px;">${escapeHtml(blockedStatusDetail)}</div>
                    </div>
                    ` : `
                    <div style="margin-bottom: 24px;">
                        <label style="display: block; margin-bottom: 8px; font-weight: bold;">Can Send Messages:</label>
                        <div style="color: #666; font-size: 14px;">${canSend ? 'Yes' : 'No'}</div>
                    </div>
                    `}
                </div>
            `;

            const conversationLlmInput = document.getElementById('conversation-llm-select');
            setupLLMCombobox(conversationLlmInput, availableLLMs, {
                includeDefaultMarker: true,
                onChange: () => updateConversationParameters(agentName, userId),
            });
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading conversation parameters: ${escapeHtml(error)}</div>`;
        });
}

function updateConversationParameters(agentName, userId) {
    const llmSelect = document.getElementById('conversation-llm-select');
    const mutedToggle = document.getElementById('conversation-muted-toggle');
    const gaggedToggle = document.getElementById('conversation-gagged-toggle');
    const blockedToggle = document.getElementById('conversation-blocked-toggle');
    
    const llmName = llmSelect ? llmSelect.value : null;
    const isMuted = mutedToggle ? mutedToggle.checked : null;
    const isGagged = gaggedToggle ? gaggedToggle.checked : null;
    const isBlocked = blockedToggle ? blockedToggle.checked : null;
    
    const updateData = {};
    if (llmName !== null) updateData.llm_name = llmName;
    if (isMuted !== null) updateData.is_muted = isMuted;
    if (isGagged !== null) updateData.is_gagged = isGagged;
    if (isBlocked !== null) updateData.is_blocked = isBlocked;
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation-parameters/${userId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(updateData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error updating conversation parameters: ' + data.error);
            // Reload to sync UI with server state (some operations may have succeeded)
            loadConversationParameters();
        } else {
            if (isBlocked !== null) {
                loadConversationParameters();
            }
            // Success - no need to reload, UI is already updated
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error updating conversation parameters: ' + error);
        // Reload to restore previous state
        loadConversationParameters();
    });
}

function loadPlans() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
    
    if (!agentName || !userId) {
        alert('Please select an agent and enter/select a conversation partner');
        return;
    }
    
    const container = document.getElementById('plans-container');
    showLoading(container, 'Loading plans...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/plans/${userId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const plans = data.plans || [];
            let html = '<div style="margin-bottom: 16px;"><button onclick="createNewPlan(\'' + escJsAttr(agentName) + '\', \'' + escJsAttr(userId) + '\')" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">+ Add New Plan</button></div>';
            
            if (plans.length === 0) {
                html += '<div class="placeholder-card">No plans found for this conversation.</div>';
                container.innerHTML = html;
                return;
            }
            
            html += plans.map(plan => `
                <div class="memory-item" style="background: white; padding: 16px; margin-bottom: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                        <div>
                            <strong>ID:</strong> ${escapeHtml(plan.id || 'N/A')}<br>
                            <strong>Created:</strong> ${escapeHtml(plan.created || 'N/A')}
                        </div>
                        <button onclick="deletePlan('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(plan.id)}')" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Delete</button>
                    </div>
                    <textarea 
                        id="plan-${userId}-${plan.id}" 
                        style="width: 100%; min-height: 100px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical; box-sizing: border-box;"
                        oninput="schedulePlanAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(plan.id)}')"
                    >${escapeHtml(plan.content || '')}</textarea>
                    <div id="plan-status-${userId}-${plan.id}" style="margin-top: 8px; font-size: 12px; color: #28a745;">Saved</div>
                </div>
            `).join('');
            container.innerHTML = html;
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading plans: ${escapeHtml(error)}</div>`;
        });
}

function createNewPlan(agentName, userId) {
    if (!agentName || !userId) {
        alert('Please select an agent and conversation partner');
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/plans/${userId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content: 'New plan entry' })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error creating plan: ' + data.error);
        } else {
            loadPlans();
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error creating plan: ' + error);
    });
}

// Auto-save for plans
const planAutoSaveTimers = {};
function schedulePlanAutoSave(agentName, userId, planId) {
    const key = `${userId}-${planId}`;
    if (planAutoSaveTimers[key]) {
        clearTimeout(planAutoSaveTimers[key]);
    }
    
    const statusEl = document.getElementById(`plan-status-${userId}-${planId}`);
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    planAutoSaveTimers[key] = setTimeout(() => {
        const textarea = document.getElementById(`plan-${userId}-${planId}`);
        if (!textarea) {
            return; // Element no longer exists
        }
        const content = textarea.value.trim();
        
        const statusEl = document.getElementById(`plan-status-${userId}-${planId}`);
        if (statusEl) {
            statusEl.textContent = 'Saving...';
            statusEl.style.color = '#007bff';
        }
        
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/plans/${userId}/${planId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ content: content })
        })
        .then(response => response.json())
        .then(data => {
            const statusEl = document.getElementById(`plan-status-${userId}-${planId}`);
            if (statusEl) {
                if (data.error) {
                    statusEl.textContent = 'Error';
                    statusEl.style.color = '#dc3545';
                } else {
                    statusEl.textContent = 'Saved';
                    statusEl.style.color = '#28a745';
                }
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            const statusEl = document.getElementById(`plan-status-${userId}-${planId}`);
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        });
    }, 1000);
}

function deletePlan(agentName, userId, planId) {
    if (!confirm('Are you sure you want to delete this plan?')) {
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/plans/${userId}/${planId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error deleting plan: ' + data.error);
        } else {
            loadPlans();
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting plan: ' + error);
    });
}

// Auto-save for summaries
const summaryAutoSaveTimers = {};
function scheduleSummaryAutoSave(agentName, userId, summaryId) {
    const key = `${userId}-${summaryId}`;
    if (summaryAutoSaveTimers[key]) {
        clearTimeout(summaryAutoSaveTimers[key]);
    }
    
    const statusEl = document.getElementById(`summary-status-${userId}-${summaryId}`);
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    summaryAutoSaveTimers[key] = setTimeout(() => {
        const textarea = document.getElementById(`summary-${userId}-${summaryId}`);
        const minInput = document.getElementById(`summary-min-${userId}-${summaryId}`);
        const maxInput = document.getElementById(`summary-max-${userId}-${summaryId}`);
        const firstDateInput = document.getElementById(`summary-first-date-${userId}-${summaryId}`);
        const lastDateInput = document.getElementById(`summary-last-date-${userId}-${summaryId}`);
        
        if (!textarea || !minInput || !maxInput) {
            return; // Elements no longer exist
        }
        
        const content = textarea.value.trim();
        const minMessageId = parseInt(minInput.value) || null;
        const maxMessageId = parseInt(maxInput.value) || null;
        const firstMessageDate = firstDateInput ? firstDateInput.value.trim() || null : null;
        const lastMessageDate = lastDateInput ? lastDateInput.value.trim() || null : null;
        
        const statusEl = document.getElementById(`summary-status-${userId}-${summaryId}`);
        if (statusEl) {
            statusEl.textContent = 'Saving...';
            statusEl.style.color = '#007bff';
        }
        
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/summaries/${userId}/${summaryId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ 
                content: content,
                min_message_id: minMessageId,
                max_message_id: maxMessageId,
                first_message_date: firstMessageDate,
                last_message_date: lastMessageDate
            })
        })
        .then(response => response.json())
        .then(data => {
            const statusEl = document.getElementById(`summary-status-${userId}-${summaryId}`);
            if (statusEl) {
                if (data.error) {
                    statusEl.textContent = 'Error';
                    statusEl.style.color = '#dc3545';
                } else {
                    statusEl.textContent = 'Saved';
                    statusEl.style.color = '#28a745';
                }
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            const statusEl = document.getElementById(`summary-status-${userId}-${summaryId}`);
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        });
    }, 1000);
}

function deleteSummary(agentName, userId, summaryId, reloadConversation = true) {
    if (!confirm('Are you sure you want to delete this summary?')) {
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/summaries/${userId}/${summaryId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error deleting summary: ' + data.error);
        } else {
            // Always reload conversation since Summaries subtab is removed
            loadConversation();
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting summary: ' + error);
    });
}

let conversationMessages = [];
let conversationTranslations = {};
let showTranslation = false;
let conversationAgentTimezone = null;
let conversationTaskLogs = [];
let conversationSummaries = [];
let showLogInterleave = false;

function loadConversation() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
    
    if (!agentName || !userId) {
        alert('Please select an agent and enter/select a conversation partner');
        return Promise.resolve();
    }
    
    const container = document.getElementById('conversation-container');
    showLoading(container, 'Loading conversation...');
    
    return fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const summaries = data.summaries || [];
            const messages = data.messages || [];
            const taskLogs = data.task_logs || [];
            const agentTimezone = data.agent_timezone;
            const isBlocked = data.is_blocked || false;
            conversationMessages = messages;
            conversationTranslations = {};
            conversationAgentTimezone = agentTimezone;
            conversationTaskLogs = taskLogs;  // Store task logs globally
            conversationSummaries = summaries;  // Store summaries globally
            // Preserve showTranslation state instead of resetting it
            // showTranslation state is preserved
            
            renderConversation(agentName, userId, summaries, messages, agentTimezone, isBlocked);
            
            // Restore checkbox state after rendering if it was checked
            const checkbox = document.getElementById('translation-toggle');
            if (checkbox) {
                checkbox.checked = showTranslation;
            }
            
            // If translations were enabled, trigger the translation stream to fetch them
            // Only start if checkbox exists (i.e., there are unsummarized messages)
            if (showTranslation && checkbox) {
                startTranslationStream(agentName, userId, checkbox);
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading conversation: ${escapeHtml(error)}</div>`;
        });
}

function refreshConversation() {
    loadConversation().then(() => {
        // Wait for the next frame to ensure DOM has been updated
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                window.scrollTo(0, document.body.scrollHeight);
            });
        });
    });
}

function renderConversation(agentName, userId, summaries, messages, agentTimezone, isBlocked = false) {
    const container = document.getElementById('conversation-container');
    let html = '';
    
    // Display blocked status banner at the top if conversation is blocked
    if (isBlocked) {
        html += '<div style="background-color: #fff3cd; border: 1px solid #ffc107; border-radius: 4px; padding: 12px; margin-bottom: 16px; display: flex; align-items: center; gap: 8px;">';
        html += '<span style="font-size: 18px;">⚠️</span>';
        html += '<div><strong>This conversation is blocked.</strong> The agent cannot send messages to this conversation.</div>';
        html += '</div>';
    }
    // Use the formatTimestamp utility from core (now global)
    
    // Display summaries at the top (editable, styled like memories)
    if (summaries.length > 0) {
        html += '<div style="margin-bottom: 24px;"><h3 style="margin-bottom: 12px; font-size: 18px; font-weight: bold;">Conversation Summaries</h3>';
        html += summaries.map(summary => `
            <div class="memory-item" style="background: white; padding: 16px; margin-bottom: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                    <div>
                        <strong>ID:</strong> ${escapeHtml(summary.id || 'N/A')}<br>
                        <strong>Created:</strong> ${escapeHtml(summary.created || 'N/A')}
                    </div>
                    <button onclick="deleteSummary('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(summary.id)}', true)" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Delete</button>
                </div>
                <div style="margin-bottom: 8px;">
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <span style="font-weight: bold; font-size: 12px; white-space: nowrap;">Message IDs:</span>
                        <input type="number" id="summary-min-${userId}-${summary.id}" value="${escapeHtml(String(summary.min_message_id || ''))}" 
                            oninput="scheduleSummaryAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(summary.id)}')"
                            style="width: 100px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; font-size: 12px;">
                        <span style="color: #666; font-size: 12px;">-</span>
                        <input type="number" id="summary-max-${userId}-${summary.id}" value="${escapeHtml(String(summary.max_message_id || ''))}" 
                            oninput="scheduleSummaryAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(summary.id)}')"
                            style="width: 100px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; font-size: 12px;">
                    </div>
                </div>
                <div style="margin-bottom: 8px;">
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <span style="font-weight: bold; font-size: 12px; white-space: nowrap;">Dates:</span>
                        <input type="date" id="summary-first-date-${userId}-${summary.id}" value="${summary.first_message_date ? escapeHtml(summary.first_message_date) : ''}" 
                            oninput="scheduleSummaryAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(summary.id)}')"
                            style="width: 150px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; font-size: 12px;">
                        <span style="color: #666; font-size: 12px;">to</span>
                        <input type="date" id="summary-last-date-${userId}-${summary.id}" value="${summary.last_message_date ? escapeHtml(summary.last_message_date) : ''}" 
                            oninput="scheduleSummaryAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(summary.id)}')"
                            style="width: 150px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; font-size: 12px;">
                    </div>
                </div>
                <textarea 
                    id="summary-${userId}-${summary.id}" 
                    style="width: 100%; min-height: 100px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical; box-sizing: border-box;"
                    oninput="scheduleSummaryAutoSave('${escJsAttr(agentName)}', '${escJsAttr(userId)}', '${escJsAttr(summary.id)}')"
                >${escapeHtml(summary.content || '')}</textarea>
                <div id="summary-status-${userId}-${summary.id}" style="margin-top: 8px; font-size: 12px; color: #28a745;">Saved</div>
            </div>
        `).join('');
        html += '</div>';
    }
    
    // Display unsummarized messages
    if (messages.length === 0 && summaries.length === 0) {
        html += '<div class="placeholder-card">No conversation history found.</div>';
    } else {
        if (messages.length === 0) {
            // All messages summarized - show placeholder
            html += '<div class="placeholder-card" style="margin-top: 16px;">All messages have been summarized. Only unsummarized messages are shown here.</div>';
        } else {
            // Has unsummarized messages - show header with all controls
            html += '<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">';
            html += '<h3 style="margin: 0; font-size: 18px; font-weight: bold;">Unsummarized Messages</h3>';
            html += '<div style="display: flex; align-items: center; gap: 16px;">';
            html += '<label style="display: flex; align-items: center; cursor: pointer; font-size: 14px;">';
            html += `<input type="checkbox" id="translation-toggle" ${showTranslation ? 'checked' : ''} onchange="toggleTranslation('${escJsAttr(agentName)}', '${escJsAttr(userId)}')" style="margin-right: 8px;">`;
            html += 'Display Translation</label>';
            html += '<label style="display: flex; align-items: center; cursor: pointer; font-size: 14px;">';
            html += `<input type="checkbox" id="log-interleave-toggle" ${showLogInterleave ? 'checked' : ''} onchange="toggleLogInterleave('${escJsAttr(agentName)}', '${escJsAttr(userId)}')" style="margin-right: 8px;">`;
            html += 'Show Task Log</label>';
            html += `<button id="summarize-btn-${userId}" onclick="triggerSummarization('${escJsAttr(agentName)}', '${escJsAttr(userId)}', this)" style="padding: 6px 12px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">Summarize Conversation</button>`;
            html += `<button id="download-btn-${userId}" onclick="downloadConversation('${escJsAttr(agentName)}', '${escJsAttr(userId)}', this)" style="padding: 6px 12px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">Download Conversation</button>`;
            html += '</div>';
            html += '</div>';
        }
        
        // Interleave task logs with messages if toggle is enabled
        // Note: task logs are already filtered on the server side to only show logs
        // within 2 minutes before the first message or after
        let displayItems = [];
        if (showLogInterleave && conversationTaskLogs.length > 0) {
            // Add messages as items
            messages.forEach(msg => {
                displayItems.push({
                    type: 'message',
                    timestamp: new Date(msg.timestamp),
                    data: msg
                });
            });
            
            // Add logs as items (already filtered by server)
            conversationTaskLogs.forEach(log => {
                displayItems.push({
                    type: 'log',
                    timestamp: new Date(log.timestamp),
                    data: log
                });
            });
            
            // Sort by timestamp
            displayItems.sort((a, b) => a.timestamp - b.timestamp);
        } else {
            // No interleaving, just show messages
            messages.forEach(msg => {
                displayItems.push({
                    type: 'message',
                    timestamp: new Date(msg.timestamp),
                    data: msg
                });
            });
        }
        
        html += displayItems.map(item => {
            if (item.type === 'log') {
                // Render log item
                const log = item.data;
                let detailsText = '';
                try {
                    const details = JSON.parse(log.action_details || '{}');
                    // Format details based on action kind
                    if (log.action_kind === 'think' && details.text) {
                        detailsText = `<div style="font-style: italic;">${escapeHtml(details.text)}</div>`;
                    } else if (log.action_kind === 'send' && details.text) {
                        detailsText = `Text: ${escapeHtml(details.text)}`;
                    } else if ((log.action_kind === 'remember' || log.action_kind === 'note' || log.action_kind === 'plan' || log.action_kind === 'intend') && details.content) {
                        detailsText = escapeHtml(details.content);
                    } else if (log.action_kind === 'summarize') {
                        detailsText = 'Summarized conversation';
                    } else if (log.action_kind === 'react' && details.emoji) {
                        detailsText = `Emoji: ${escapeHtml(details.emoji)}`;
                    } else if (log.action_kind === 'sticker' && details.unique_id) {
                        detailsText = `Sticker: ${escapeHtml(details.unique_id)}${details.search_query ? ' (' + escapeHtml(details.search_query) + ')' : ''}`;
                    } else if (Object.keys(details).length > 0 && !details.action) {
                        // Generic display for any other parameters
                        const parts = [];
                        for (const [key, value] of Object.entries(details)) {
                            if (typeof value === 'string' || typeof value === 'number') {
                                parts.push(`${key}: ${escapeHtml(String(value))}`);
                            } else if (Array.isArray(value)) {
                                parts.push(`${key}: [${value.join(', ')}]`);
                            } else if (typeof value === 'boolean') {
                                parts.push(`${key}: ${value}`);
                            }
                        }
                        detailsText = parts.join(', ');
                    }
                } catch (e) {
                    detailsText = '';
                }
                
                return `
                    <div style="background: #ffe0f0; padding: 10px; margin-bottom: 8px; border-radius: 8px; border-left: 4px solid #d81b60;">
                        <div style="font-size: 11px; color: #666; margin-bottom: 4px;">
                            <strong>[LOG: ${escapeHtml(log.action_kind).toUpperCase()}]</strong>${log.task_identifier ? ` <span style="color: #888;">(${escapeHtml(log.task_identifier)})</span>` : ''} at ${formatTimestamp(log.timestamp, agentTimezone)}
                        </div>
                        ${detailsText ? `<div style="font-size: 13px; color: #333;">${detailsText}</div>` : ''}
                    </div>
                `;
            } else {
                // Render message (existing code)
                const msg = item.data;
            // Build content from parts if available (includes media/stickers)
            let contentHtml = '';
            let hasTextContent = false;
            
            if (msg.parts && Array.isArray(msg.parts) && msg.parts.length > 0) {
                // Use parts to show rich content
                msg.parts.forEach(part => {
                    if (part.kind === 'text' && part.text) {
                        hasTextContent = true;
                        // Text is already HTML (from backend)
                        const partText = showTranslation && conversationTranslations[msg.id] 
                            ? conversationTranslations[msg.id] 
                            : part.text;
                        // Use CSS to ensure emojis render consistently (don't apply font-weight/font-style to emojis)
                        contentHtml += `<div style="white-space: pre-wrap; margin-bottom: 4px;">${partText}</div>`;
                    } else if (part.kind === 'media') {
                        const mediaKind = part.media_kind || 'media';
                        const uniqueId = part.unique_id;
                        const messageId = part.message_id || msg.id;
                        const mediaUrl = `${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}/media/${messageId}/${uniqueId}`;
                        
                        // Render actual media based on type
                        // Check if sticker is animated (either via media_kind or is_animated flag)
                        const isAnimatedSticker = mediaKind === 'animated_sticker' || (mediaKind === 'sticker' && part.is_animated);
                        // Video stickers (webm) need <video> - they break when rendered in <img>
                        const mimeType = (part.mime_type || '').toLowerCase();
                        const isVideoSticker = mediaKind === 'sticker' && !isAnimatedSticker && mimeType.startsWith('video/');
                        
                        if (isVideoSticker) {
                            // Video stickers (e.g. webm from "OSAKA's video pack") - use video tag
                            contentHtml += `<div style="margin-bottom: 4px;"><video controls autoplay loop muted style="max-width: 300px; max-height: 300px; border-radius: 8px;"><source src="${mediaUrl}" type="${mimeType || 'video/webm'}"></video></div>`;
                        } else if (mediaKind === 'photo' || (mediaKind === 'sticker' && !isAnimatedSticker)) {
                            // Static images and regular stickers
                            contentHtml += `<div style="margin-bottom: 4px;"><img src="${mediaUrl}" alt="${part.sticker_name || uniqueId}" style="max-width: 300px; max-height: 300px; border-radius: 8px;"></div>`;
                        } else if (isAnimatedSticker) {
                            // Animated stickers (TGS) - use Lottie player like in media editor
                            contentHtml += `<div style="margin-bottom: 4px; position: relative; width: 200px; height: 200px; display: flex; align-items: center; justify-content: center;">
                                <div id="tgs-player-${uniqueId}" class="tgs-animation-container" data-message-id="${messageId}" data-unique-id="${uniqueId}" style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center;">
                                    <div style="text-align: center; color: #666;">
                                        <div style="font-size: 24px; margin-bottom: 10px;">🎭</div>
                                        <div style="font-size: 12px; margin-bottom: 10px;">Loading TGS animation...</div>
                                        <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none; font-size: 11px;">Download TGS</a>
                                    </div>
                                </div>
                            </div>`;
                        } else if (mediaKind === 'video' || mediaKind === 'animation' || mediaKind === 'gif') {
                            // Videos, animations, and GIFs
                            contentHtml += `<div style="margin-bottom: 4px;"><video controls autoplay loop muted style="max-width: 300px; max-height: 300px; border-radius: 8px;"><source src="${mediaUrl}"></video></div>`;
                        } else if (mediaKind === 'audio') {
                            contentHtml += `<div style="margin-bottom: 4px;"><audio controls style="width: 100%; max-width: 400px;"><source src="${mediaUrl}"></audio></div>`;
                        } else {
                            // Fallback: show description with download link
                            const renderedText = part.rendered_text || '';
                            const stickerInfo = part.sticker_set_name 
                                ? ` (${part.sticker_set_name}${part.sticker_name ? ` / ${part.sticker_name}` : ''})`
                                : '';
                            contentHtml += `<div style="color: #666; font-style: italic; margin-bottom: 4px;">${renderedText.replace(/</g, '&lt;').replace(/>/g, '&gt;')}${stickerInfo} <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none;">[Download]</a></div>`;
                        }
                        
                        // Add description text if available (below the media)
                        if (part.rendered_text) {
                            contentHtml += `<div style="color: #666; font-size: 11px; margin-top: 2px; margin-bottom: 4px; font-style: italic;">${part.rendered_text.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>`;
                        }
                    }
                });
            }
            
            // Fallback to text if no parts or if parts didn't include text
            if (!hasTextContent && msg.text) {
                // Text is already HTML (from backend)
                const displayText = showTranslation && conversationTranslations[msg.id] 
                    ? conversationTranslations[msg.id] 
                    : msg.text;
                contentHtml = `<div style="white-space: pre-wrap;">${displayText}</div>`;
            }
            
            // If still no content, show placeholder
            if (!contentHtml) {
                contentHtml = '<div style="color: #999; font-style: italic;">[No content]</div>';
            }
            
            // Build metadata line with sender name and ID
            let senderDisplay = '';
            if (msg.sender_name && msg.sender_id) {
                senderDisplay = `<strong>${escapeHtml(msg.sender_name || '')}</strong> (${escapeHtml(String(msg.sender_id))})`;
            } else if (msg.sender_id) {
                senderDisplay = `<strong>${msg.sender_id}</strong>`;
            } else {
                senderDisplay = msg.is_from_agent ? '<strong>Agent</strong>' : '<strong>User</strong>';
            }
            let metadataLine = `${senderDisplay} • ${formatTimestamp(msg.timestamp, agentTimezone)} • ID: ${msg.id}`;
            if (msg.reply_to_msg_id) {
                metadataLine += ` • Reply to: ${msg.reply_to_msg_id}`;
            }
            // Add read receipt checkmark for agent messages in DMs
            // is_read_by_partner is only set for agent messages in DMs (null/undefined otherwise)
            if (msg.is_read_by_partner === true) {
                metadataLine += ` ✔️`;
            }
            
            // Add reactions if present
            // Note: msg.reactions may contain HTML (img tags for custom emojis) added by the backend,
            // so we should NOT escape it - the backend intentionally provides HTML to render
            let reactionsHtml = '';
            if (msg.reactions) {
                reactionsHtml = `<div style="font-size: 11px; color: #888; margin-top: 4px; font-style: italic;">Reactions: ${msg.reactions}</div>`;
            }
            
            // Determine background color: grey if translation is requested but not available, otherwise normal
            let bgColor = msg.is_from_agent ? '#e3f2fd' : 'white';
            if (showTranslation && msg.text && !conversationTranslations[msg.id]) {
                bgColor = '#e0e0e0'; // Grey background for messages awaiting translation
            }
            
            return `
                <div id="message-${msg.id}" data-message-id="${msg.id}" style="background: ${bgColor}; padding: 12px; margin-bottom: 8px; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.1); border-left: 4px solid ${msg.is_from_agent ? '#2196f3' : '#4caf50'};">
                    <div style="font-size: 12px; color: #666; margin-bottom: 4px;">
                        ${metadataLine}
                    </div>
                    ${contentHtml}
                    ${reactionsHtml}
                </div>
            `;
            } // End of message type
        }).join('');
    }
    
    // Add refresh button at the bottom
    html += '<div style="margin-top: 16px; padding: 12px; text-align: center; border-top: 1px solid #ddd;">';
    html += `<button onclick="refreshConversation()" style="padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Refresh Conversation</button>`;
    html += '</div>';
    
    container.innerHTML = html;
    
    // Load TGS animations for animated stickers in conversation (same as media editor)
    setTimeout(() => {
        loadTGSAnimationsForConversation(agentName, userId);
        loadCustomEmojiAnimations(agentName);
    }, 100);
}

function toggleLogInterleave(agentName, userId) {
    showLogInterleave = !showLogInterleave;
    // Re-render the conversation with the new toggle state using stored summaries
    renderConversation(agentName, userId, conversationSummaries, conversationMessages, conversationAgentTimezone, false);
    
    // Restore checkbox state after rendering
    const checkbox = document.getElementById('log-interleave-toggle');
    if (checkbox) {
        checkbox.checked = showLogInterleave;
    }
}
async function loadCustomEmojiAnimations(agentName) {
    // Find all custom emoji images (both reactions and message text) and check if they're animated (TGS)
    // Only process images that haven't been processed yet (don't have the processed flag)
    // Query for both:
    // - .custom-emoji-reaction: used in reactions (standalone img)
    // - .custom-emoji-img: used in message text (inside .custom-emoji-container)
    // Scope to conversation container to avoid processing emojis in other parts of the page
    const conversationContainer = document.getElementById('conversation-container');
    if (!conversationContainer) {
        return;
    }
    
    const emojiImages = conversationContainer.querySelectorAll('.custom-emoji-reaction:not([data-lottie-processed]), .custom-emoji-img:not([data-lottie-processed])');
    
    // Calculate indices upfront from the original DOM state to avoid index shifts
    // when images are removed during processing. Create a map from img element to its index.
    const allEmojiImages = Array.from(conversationContainer.querySelectorAll('.custom-emoji-reaction, .custom-emoji-img'));
    const imgIndexMap = new Map();
    allEmojiImages.forEach((img, index) => {
        imgIndexMap.set(img, index);
    });
    
    for (const img of emojiImages) {
        // For .custom-emoji-img, data attributes may be on the parent container
        // For .custom-emoji-reaction, data attributes are on the img itself
        const container = img.closest('.custom-emoji-container');
        const emojiUrl = img.getAttribute('data-emoji-url') || 
                        (container ? container.getAttribute('data-emoji-url') : null) ||
                        img.getAttribute('src');
        const documentId = img.getAttribute('data-document-id') ||
                          (container ? container.getAttribute('data-document-id') : null);
        
        if (!emojiUrl || !documentId) {
            continue;
        }
        
        // Mark as processed immediately to avoid duplicate processing
        img.setAttribute('data-lottie-processed', 'true');
        
        try {
            // Fetch the emoji to check if it's animated (TGS)
            // The emojiUrl should already be a full path like /admin/api/agents/...
            const response = await fetchWithAuth(emojiUrl);
            const contentType = response.headers.get('content-type') || '';
            const isAnimated = response.headers.get('x-emoji-type') === 'animated' || 
                              contentType.includes('application/gzip') || 
                              contentType.includes('application/x-tgsticker');
            
            if (isAnimated) {
                // It's a TGS file - render with Lottie
                const tgsData = await response.arrayBuffer();
                // Use a unique player ID that includes the document ID and position to avoid conflicts
                // Get the index from the pre-calculated map (before any DOM removals)
                const imgIndex = imgIndexMap.get(img);
                const playerId = `emoji-lottie-${documentId}-${imgIndex}`;
                
                // Check if img is inside a container (message text) or standalone (reaction)
                const parent = img.parentElement;
                const isInContainer = container && container === parent;
                
                // Create Lottie player wrapper
                const wrapper = document.createElement('span');
                wrapper.style.display = 'inline-block';
                wrapper.style.width = '1.2em';
                wrapper.style.height = '1.2em';
                wrapper.style.verticalAlign = 'middle';
                wrapper.innerHTML = `<div id="${playerId}" style="width: 1.2em; height: 1.2em;"></div>`;
                
                if (isInContainer) {
                    // For message text emojis: replace the entire container
                    const containerParent = container.parentElement;
                    if (containerParent) {
                        containerParent.insertBefore(wrapper, container);
                        containerParent.removeChild(container);
                    } else {
                        container.replaceWith(wrapper);
                    }
                } else {
                    // For reaction emojis: replace just the img
                    if (parent) {
                        parent.insertBefore(wrapper, img);
                        parent.removeChild(img);
                    } else {
                        img.replaceWith(wrapper);
                    }
                }
                
                // Decompress and load TGS with Lottie
                const decompressed = pako.inflate(new Uint8Array(tgsData));
                const jsonData = JSON.parse(new TextDecoder().decode(decompressed));
                
                const player = lottie.loadAnimation({
                    container: document.getElementById(playerId),
                    renderer: 'svg',
                    loop: true,
                    autoplay: true,
                    animationData: jsonData
                });
            }
            // If not animated, the img tag will display normally
        } catch (error) {
            console.warn(`Failed to load custom emoji ${documentId}:`, error);
            // Remove the processed flag on error so it can be retried
            img.removeAttribute('data-lottie-processed');
        }
    }
}

async function loadTGSAnimationsForConversation(agentName, userId) {
    // Find all TGS player containers in conversation view
    const tgsContainers = document.querySelectorAll('#conversation-container [id^="tgs-player-"]');
    console.log(`Found ${tgsContainers.length} TGS containers in conversation to load`);

    for (const container of tgsContainers) {
        const uniqueId = container.getAttribute('data-unique-id') || container.id.replace('tgs-player-', '');
        const messageId = container.getAttribute('data-message-id');
        
        if (!messageId) {
            console.warn(`Could not find message ID for TGS ${uniqueId}, skipping`);
            continue;
        }
        
        const mediaUrl = `${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}/media/${messageId}/${uniqueId}`;
        console.log(`Loading TGS for ${uniqueId} from conversation: ${mediaUrl}`);

        try {
            // Fetch the TGS file
            const response = await fetchWithAuth(mediaUrl);
            if (!response.ok) {
                throw new Error(`Failed to fetch TGS file: ${response.status}`);
            }

            const tgsData = await response.arrayBuffer();

            // Decompress the gzipped Lottie data
            let lottieJson;

            // Try pako first since DecompressionStream is unreliable
            if (typeof pako !== 'undefined') {
                try {
                    console.log('Attempting pako decompression for conversation TGS...');
                    const decompressed = pako.inflate(new Uint8Array(tgsData), { to: 'string' });
                    lottieJson = JSON.parse(decompressed);
                    console.log('Pako decompression successful for conversation TGS');
                } catch (pakoError) {
                    console.error('Pako decompression failed:', pakoError);
                    throw new Error('Failed to decompress TGS file with pako');
                }
            } else {
                // Fallback to DecompressionStream if pako not available
                try {
                    console.log('Attempting DecompressionStream decompression for conversation TGS...');
                    const decompressedData = await decompressGzip(tgsData);
                    const jsonText = new TextDecoder().decode(decompressedData);
                    lottieJson = JSON.parse(jsonText);
                    console.log('DecompressionStream decompression successful for conversation TGS');
                } catch (decompError) {
                    console.error('DecompressionStream failed:', decompError.message);
                    throw new Error('Failed to decompress TGS file - no suitable decompression method available');
                }
            }

            // Clear the loading content and create a new container for Lottie
            container.innerHTML = '';
            const animationContainer = document.createElement('div');
            animationContainer.style.width = '100%';
            animationContainer.style.height = '100%';
            animationContainer.style.display = 'flex';
            animationContainer.style.alignItems = 'center';
            animationContainer.style.justifyContent = 'center';
            animationContainer.style.backgroundColor = 'transparent';
            container.appendChild(animationContainer);

            // Initialize Lottie animation
            console.log('Initializing Lottie animation for conversation TGS...');
            const animation = lottie.loadAnimation({
                container: animationContainer,
                renderer: 'svg',
                loop: true,
                autoplay: true,
                animationData: lottieJson
            });
            console.log('Lottie animation initialized successfully for conversation TGS');

            // Handle animation errors
                    animation.addEventListener('error', (error) => {
                        console.error('Lottie animation error:', error);
                        container.innerHTML = `
                            <div style="text-align: center; color: #dc3545;">
                                <div style="font-size: 16px; margin-bottom: 5px;">⚠️</div>
                                <div style="font-size: 11px;">Animation Error</div>
                                <div style="font-size: 10px; margin-top: 5px;">${escapeHtml(error.message || 'Unknown error')}</div>
                            </div>
                        `;
                    });

            // Handle successful loading
            animation.addEventListener('DOMLoaded', () => {
                console.log('Lottie animation DOM loaded successfully for conversation TGS');
            });

        } catch (error) {
            if (error && error.message === 'unauthorized') {
                return;
            }
            console.error(`Failed to load TGS animation for ${uniqueId} in conversation:`, error);
            container.innerHTML = `
                <div style="text-align: center; color: #dc3545;">
                    <div style="font-size: 16px; margin-bottom: 5px;">⚠️</div>
                    <div style="font-size: 11px;">Load Failed</div>
                    <div style="font-size: 10px; margin-top: 5px;">${escapeHtml(error.message || 'Unknown error')}</div>
                    <a href="${mediaUrl}" download style="color: #007bff; text-decoration: none; font-size: 10px; margin-top: 5px; display: block;">Download TGS</a>
                </div>
            `;
        }
    }
}

function triggerSummarization(agentName, userId, buttonElement) {
    if (!confirm('This will trigger summarization of the unsummarized messages. Continue?')) {
        return;
    }
    
    // Find the button's container
    const container = buttonElement.closest('div[style*="display: flex"]');
    
    const statusDiv = document.createElement('div');
    statusDiv.id = `summarize-status-${userId}`;
    statusDiv.style.cssText = 'margin-top: 8px; font-size: 14px; color: #007bff; width: 100%;';
    statusDiv.textContent = 'Triggering summarization...';
    
    // Insert status div after the container
    if (container && container.parentElement) {
        container.parentElement.insertBefore(statusDiv, container.nextSibling);
    }
    buttonElement.disabled = true;
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}/summarize`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        }
    })
    .then(response => response.json())
    .then(data => {
        const statusDiv = document.getElementById(`summarize-status-${userId}`);
        if (data.error) {
            if (statusDiv) {
                statusDiv.textContent = 'Error: ' + data.error;
                statusDiv.style.color = '#dc3545';
            }
            buttonElement.disabled = false;
        } else {
            if (statusDiv) {
                statusDiv.textContent = 'Summarization task created! The conversation will be summarized in the next tick.';
                statusDiv.style.color = '#28a745';
            }
            // Reload conversation after a short delay to show updated summaries
            setTimeout(() => {
                loadConversation();
            }, 2000);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        const statusDiv = document.getElementById(`summarize-status-${userId}`);
        if (statusDiv) {
            statusDiv.textContent = 'Error: ' + error;
            statusDiv.style.color = '#dc3545';
        }
        buttonElement.disabled = false;
    });
}

function downloadConversation(agentName, userId, buttonElement) {
    // Disable button and show status
    buttonElement.disabled = true;
    const originalText = buttonElement.textContent;
    buttonElement.textContent = 'Preparing download...';
    
    // Create or update status div
    let statusDiv = document.getElementById(`download-status-${userId}`);
    if (!statusDiv) {
        statusDiv = document.createElement('div');
        statusDiv.id = `download-status-${userId}`;
        statusDiv.style.marginTop = '8px';
        statusDiv.style.fontSize = '14px';
        buttonElement.parentElement.appendChild(statusDiv);
    }
    statusDiv.textContent = 'Preparing download...';
    statusDiv.style.color = '#007bff';
    
    // Get translation setting
    const translationCheckbox = document.getElementById('translation-toggle');
    const includeTranslations = translationCheckbox ? translationCheckbox.checked : false;
    
    // Get task log setting
    const taskLogCheckbox = document.getElementById('log-interleave-toggle');
    const includeTaskLogs = taskLogCheckbox ? taskLogCheckbox.checked : false;
    
    // Make request to download endpoint
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}/download`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            include_translations: includeTranslations,
            include_task_logs: includeTaskLogs
        })
    })
    .then(response => {
        if (!response.ok) {
            return response.json().then(data => {
                throw new Error(data.error || `HTTP ${response.status}`);
            });
        }
        return response.blob();
    })
    .then(blob => {
        // Create download link
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5);
        a.download = `conversation_${agentName}_${userId}_${timestamp}.zip`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        statusDiv.textContent = 'Download complete!';
        statusDiv.style.color = '#28a745';
        buttonElement.disabled = false;
        buttonElement.textContent = originalText;
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        statusDiv.textContent = 'Error: ' + (error.message || error);
        statusDiv.style.color = '#dc3545';
        buttonElement.disabled = false;
        buttonElement.textContent = originalText;
    });
}

// Helper function to update a single message with translation
function updateMessageTranslation(messageId, translatedText) {
    // Don't update if translation is disabled
    if (!showTranslation) return;
    
    const messageEl = document.getElementById(`message-${messageId}`);
    if (!messageEl) return;
    
    // Update the translation in our dictionary
    conversationTranslations[messageId] = translatedText;
    
    // Find all content divs with text (there might be multiple if message has parts)
    const contentDivs = messageEl.querySelectorAll('div[style*="white-space: pre-wrap"]');
    if (contentDivs.length > 0) {
        // Update the first text div (or all if there are multiple text parts)
        // For messages with parts, we typically only want to update the first text part
        // For simple messages, there's only one div
        contentDivs[0].innerHTML = translatedText;
    }
    
    // Update background color from grey to normal
    const msg = conversationMessages.find(m => String(m.id) === String(messageId));
    if (msg) {
        const bgColor = msg.is_from_agent ? '#e3f2fd' : 'white';
        messageEl.style.background = bgColor;
    }
}

// Track active translation stream for cancellation
let translationAbortController = null;
let translationStreamReader = null;
let translationStreamTimeout = null;

function toggleTranslation(agentName, userId) {
    const checkbox = document.getElementById('translation-toggle');
    if (!checkbox) return; // Checkbox not rendered (no unsummarized messages)
    showTranslation = checkbox.checked;
    
    // Cancel any pending timeout
    if (translationStreamTimeout) {
        clearTimeout(translationStreamTimeout);
        translationStreamTimeout = null;
    }
    
    // Cancel any active translation stream
    if (translationAbortController) {
        translationAbortController.abort();
        translationAbortController = null;
    }
    if (translationStreamReader) {
        translationStreamReader.cancel();
        translationStreamReader = null;
    }
    
    if (showTranslation) {
        // Check if all messages with text have translations
        const messagesWithText = conversationMessages.filter(msg => msg.text);
        const allHaveTranslations = messagesWithText.length > 0 && 
            messagesWithText.every(msg => conversationTranslations[msg.id]);
        
        // If all messages with text already have translations, just re-render
        if (allHaveTranslations) {
            fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}`)
                .then(response => response.json())
                .then(data => {
                    if (!data.error) {
                        if (data.agent_timezone) {
                            conversationAgentTimezone = data.agent_timezone;
                        }
                        const isBlocked = data.is_blocked || false;
                        renderConversation(agentName, userId, data.summaries || [], conversationMessages, conversationAgentTimezone, isBlocked);
                    }
                });
            return;
        }
        
        // Update background colors to grey for messages without translations
        // Do this without full re-render to avoid triggering image full-screen on mobile
        conversationMessages.forEach(msg => {
            if (msg.text && !conversationTranslations[msg.id]) {
                const messageEl = document.getElementById(`message-${msg.id}`);
                if (messageEl) {
                    messageEl.style.background = '#e0e0e0';
                }
            }
        });
        
        // Start streaming translations via SSE
        // Use setTimeout to break the connection between touch event and DOM updates
        // This prevents mobile browsers from interpreting the updates as image clicks
        translationStreamTimeout = setTimeout(() => {
            translationStreamTimeout = null;
            startTranslationStream(agentName, userId, checkbox);
        }, 100);
    } else {
        // Just re-render with current translation state (translations hidden)
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}`)
            .then(response => response.json())
            .then(data => {
                if (!data.error) {
                    if (data.agent_timezone) {
                        conversationAgentTimezone = data.agent_timezone;
                    }
                    const isBlocked = data.is_blocked || false;
                    renderConversation(agentName, userId, data.summaries || [], conversationMessages, conversationAgentTimezone, isBlocked);
                }
            });
    }
}

function startTranslationStream(agentName, userId, checkbox) {
    // Early return if translation was disabled before this function was called
    if (!showTranslation) {
        return;
    }
    
    // Cancel any existing stream first
    if (translationAbortController) {
        translationAbortController.abort();
    }
    if (translationStreamReader) {
        translationStreamReader.cancel();
    }
    
    // Create new AbortController for this stream
    translationAbortController = new AbortController();
    const abortSignal = translationAbortController.signal;
    
    // Build the URL for SSE endpoint
    const url = `${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}/translate`;
    
    // Create POST request body
    const requestBody = JSON.stringify({ messages: conversationMessages });
    
    // Use fetch with ReadableStream to handle SSE (EventSource doesn't support POST)
    fetchWithAuth(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: requestBody,
        signal: abortSignal
    })
    .then(response => {
        if (!response.ok) {
            // Try to parse error as JSON
            // Handle JSON parsing errors first, then handle successful parse
            return response.json()
                .catch(() => {
                    // JSON parsing failed, throw generic error
                    throw new Error(`HTTP error! status: ${response.status}`);
                })
                .then(data => {
                    // JSON parsing succeeded, throw with the error message from the server
                    throw new Error(data.error || `HTTP error! status: ${response.status}`);
                });
        }
        
        // Check if response is SSE (text/event-stream)
        const contentType = response.headers.get('content-type');
        if (contentType && contentType.includes('text/event-stream')) {
            // Handle SSE stream
            const reader = response.body.getReader();
            translationStreamReader = reader; // Store reader for cancellation
            const decoder = new TextDecoder();
            let buffer = '';
            
            function readStream() {
                // Check if translation was disabled before continuing
                if (!showTranslation || abortSignal.aborted) {
                    reader.cancel();
                    translationStreamReader = null;
                    translationAbortController = null;
                    return;
                }
                
                reader.read().then(({ done, value }) => {
                    if (done) {
                        // Stream completed - process any remaining buffer
                        if (buffer.trim() && showTranslation && !abortSignal.aborted) {
                            const events = buffer.split('\n\n');
                            for (const event of events) {
                                if (event.trim()) {
                                    const lines = event.split('\n');
                                    for (const line of lines) {
                                        if (line.startsWith('data: ')) {
                                            try {
                                                const data = JSON.parse(line.slice(6));
                                                handleTranslationEvent(data, checkbox);
                                            } catch (e) {
                                                console.error('Error parsing SSE data:', e, line);
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        translationStreamReader = null;
                        translationAbortController = null;
                        return;
                    }
                    
                    // Check again if translation was disabled
                    if (!showTranslation || abortSignal.aborted) {
                        reader.cancel();
                        translationStreamReader = null;
                        translationAbortController = null;
                        return;
                    }
                    
                    buffer += decoder.decode(value, { stream: true });
                    // SSE events are separated by double newlines
                    const events = buffer.split('\n\n');
                    // Keep the last incomplete event in buffer
                    buffer = events.pop() || '';
                    
                    for (const event of events) {
                        if (event.trim() && showTranslation && !abortSignal.aborted) {
                            // Parse SSE event (format: "data: {...}")
                            const lines = event.split('\n');
                            for (const line of lines) {
                                if (line.startsWith('data: ')) {
                                    try {
                                        const data = JSON.parse(line.slice(6));
                                        handleTranslationEvent(data, checkbox);
                                    } catch (e) {
                                        console.error('Error parsing SSE data:', e, line);
                                    }
                                }
                            }
                        }
                    }
                    
                    readStream();
                }).catch(error => {
                    // Clean up on error
                    translationStreamReader = null;
                    translationAbortController = null;
                    
                    console.error('Error reading stream:', error);
                    if (error && error.message !== 'unauthorized') {
                        // Only show alert for unexpected errors (not cancellation)
                        if (!error.message.includes('aborted') && !error.message.includes('canceled')) {
                            alert('Error receiving translations: ' + error);
                            if (checkbox) {
                                checkbox.checked = false;
                            }
                            showTranslation = false;
                        }
                    }
                });
            }
            
            readStream();
        } else {
            // Fallback: try to parse as JSON (for backward compatibility)
            return response.json();
        }
    })
    .then(data => {
        // Only reached if response was JSON (not SSE)
        if (data && data.error) {
            alert('Error translating messages: ' + data.error);
            if (checkbox) {
                checkbox.checked = false;
            }
            showTranslation = false;
        } else if (data && data.translations) {
            // Handle non-streaming response (backward compatibility)
            Object.assign(conversationTranslations, data.translations);
            // Re-render to show translations
            fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/conversation/${userId}`)
                .then(response => response.json())
                .then(data => {
                    if (!data.error) {
                        if (data.agent_timezone) {
                            conversationAgentTimezone = data.agent_timezone;
                        }
                        renderConversation(agentName, userId, data.summaries || [], conversationMessages, conversationAgentTimezone);
                    }
                });
        }
    })
    .catch(error => {
        // Clean up on error
        translationStreamReader = null;
        translationAbortController = null;
        
        if (error && error.message === 'unauthorized') {
            return;
        }
        console.error('Translation stream error:', error);
        // Show alert for fetch errors (not stream read errors or aborts)
        if (error.name !== 'AbortError' && error.message && !error.message.includes('aborted') && !error.message.includes('canceled')) {
            alert('Error translating messages: ' + error.message);
            if (checkbox) {
                checkbox.checked = false;
            }
            showTranslation = false;
        }
    });
}

function handleTranslationEvent(data, checkbox) {
    // Don't process events if translation is disabled
    if (!showTranslation) return;
    
    if (data.type === 'cached') {
        // Initial cached translations
        Object.assign(conversationTranslations, data.translations || {});
        // Update all cached translations in the UI
        for (const [messageId, translatedText] of Object.entries(data.translations || {})) {
            updateMessageTranslation(messageId, translatedText);
        }
    } else if (data.type === 'translation') {
        // New translations from a batch
        for (const [messageId, translatedText] of Object.entries(data.translations || {})) {
            updateMessageTranslation(messageId, translatedText);
        }
    } else if (data.type === 'complete') {
        // Translation complete
        console.log('Translation stream completed');
        translationStreamReader = null;
        translationAbortController = null;
    } else if (data.type === 'error') {
        // Error occurred
        translationStreamReader = null;
        translationAbortController = null;
        alert('Error translating messages: ' + (data.error || 'Unknown error'));
        if (checkbox) {
            checkbox.checked = false;
        }
        showTranslation = false;
    }
}

// XSend functionality
function sendXSend() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    const intentTextarea = document.getElementById('xsend-intent-textarea');
    const statusDiv = document.getElementById('xsend-status');
    
    const agentName = agentSelect?.value;
    const userId = userIdInput?.value.trim() || partnerSelect?.value;
    const intent = intentTextarea?.value.trim() || '';
    
    if (!agentName) {
        statusDiv.innerHTML = '<div style="color: #dc3545;">Please select an agent</div>';
        return;
    }
    
    if (!userId) {
        statusDiv.innerHTML = '<div style="color: #dc3545;">Please select or enter a conversation partner</div>';
        return;
    }
    
    if (!intent) {
        statusDiv.innerHTML = '<div style="color: #dc3545;">Please enter an intent message</div>';
        return;
    }
    
    statusDiv.innerHTML = '<div style="color: #007bff;">Sending...</div>';
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/xsend/${userId}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ intent: intent })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            statusDiv.innerHTML = `<div style="color: #dc3545;">Error: ${escapeHtml(data.error)}</div>`;
        } else {
            statusDiv.innerHTML = '<div style="color: #28a745;">XSend task created successfully!</div>';
            intentTextarea.value = '';
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        statusDiv.innerHTML = `<div style="color: #dc3545;">Error: ${escapeHtml(error)}</div>`;
    });
}

// ===== Work Queue Functions =====

/**
 * Namespace for Work Queue UI functions.
 * Provides read-only view of task graphs and management operations.
 */
const WorkQueueUI = {
    /**
     * Load and display the work queue for the currently selected agent and conversation partner.
     * Fetches work queue data from the API and renders it in the UI.
     */
    load: function() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
    
    if (!agentName || !userId) {
        const container = document.getElementById('work-queue-container');
        showLoading(container, 'Select an agent and conversation partner');
        return Promise.resolve();
    }
    
    const container = document.getElementById('work-queue-container');
    showLoading(container, 'Loading work queue...');
    
    return fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/work-queue/${userId}`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            WorkQueueUI.render(agentName, userId, data.work_queue);
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading work queue: ${escapeHtml(error)}</div>`;
        });
    },

    /**
     * Render the work queue data in the UI with formatted task display.
     * @param {string} agentName - The agent's configuration name
     * @param {string} userId - The user/channel ID
     * @param {Object|null} workQueue - The work queue data containing context and nodes
     */
    render: function(agentName, userId, workQueue) {
    const container = document.getElementById('work-queue-container');
    
    if (!workQueue) {
        container.innerHTML = `
            <div style="padding: 20px; text-align: center; color: #666;">
                <p style="font-size: 16px;">No work queue found for this conversation.</p>
            </div>
        `;
        return;
    }
    
    const context = workQueue.context || {};
    const nodes = workQueue.nodes || [];
    
    // Count tasks by status
    const statusCounts = {
        pending: 0,
        active: 0,
        done: 0,
        failed: 0,
        cancelled: 0
    };
    nodes.forEach(node => {
        const status = node.status || 'pending';
        if (statusCounts.hasOwnProperty(status)) {
            statusCounts[status]++;
        }
    });
    
    // Status color mapping
    const statusColors = {
        pending: '#ffc107',
        active: '#007bff',
        done: '#28a745',
        failed: '#dc3545',
        cancelled: '#6c757d'
    };
    
    // Status icons
    const statusIcons = {
        pending: '⏱️',
        active: '▶️',
        done: '✅',
        failed: '❌',
        cancelled: '⊘'
    };
    
    let html = `
        <div style="padding: 20px; background: #ffffff; border-radius: 8px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                <h3 style="margin: 0; font-size: 20px; color: #2c3e50;">Work Queue</h3>
                <button onclick="WorkQueueUI.delete()" style="padding: 8px 16px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Delete All Pending Tasks</button>
            </div>
            
            <div style="background: #f8f9fa; padding: 16px; border-radius: 6px; margin-bottom: 20px;">
                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 12px;">
                    <div><strong>Graph ID:</strong> <span style="background: #e9ecef; padding: 2px 6px; border-radius: 3px; font-size: 14px;">${escapeHtml(workQueue.id)}</span></div>
                    <div><strong>Type:</strong> ${context.is_group_chat ? 'Group' : 'DM'}</div>
                </div>
                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px;">
                    <div><strong>Agent:</strong> ${escapeHtml(context.agent_name || 'N/A')} (${context.agent_id || 'N/A'})</div>
                    <div><strong>Channel:</strong> ${escapeHtml(context.channel_name || 'N/A')} (${context.channel_id || 'N/A'})</div>
                </div>
            </div>
            
            <div style="margin-bottom: 20px;">
                <strong>Status Summary:</strong>
                <div style="display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap;">
    `;
    
    for (const [status, count] of Object.entries(statusCounts)) {
        html += `
            <div style="display: flex; align-items: center; gap: 6px;">
                <span style="display: inline-block; width: 12px; height: 12px; border-radius: 50%; background: ${statusColors[status]};"></span>
                <span style="text-transform: capitalize;">${status}:</span>
                <strong>${count}</strong>
            </div>
        `;
    }
    
    html += `
                </div>
            </div>
            
            <div>
                <h4 style="margin: 0 0 16px 0; font-size: 16px; color: #2c3e50;">Tasks (${nodes.length})</h4>
    `;
    
    if (nodes.length === 0) {
        html += '<p style="color: #666; font-style: italic;">No tasks in queue.</p>';
    } else {
        nodes.forEach((node, index) => {
            const status = node.status || 'pending';
            const statusColor = statusColors[status] || '#6c757d';
            const statusIcon = statusIcons[status] || '•';
            
            html += `
                <div style="border: 1px solid #e0e0e0; border-radius: 6px; padding: 16px; margin-bottom: 12px; background: #ffffff;">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 12px;">
                        <div style="flex: 1;">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                                <span style="font-size: 18px;">${statusIcon}</span>
                                <span style="font-size: 16px; font-weight: 600; color: #2c3e50;">${escapeHtml(node.id)}</span>
                                <span style="padding: 2px 8px; background: #e9ecef; border-radius: 4px; font-size: 12px; font-family: monospace;">${escapeHtml(node.type)}</span>
                            </div>
                        </div>
                        <div>
                            <span style="padding: 4px 12px; background: ${statusColor}; color: white; border-radius: 4px; font-size: 13px; font-weight: 500; text-transform: capitalize;">${escapeHtml(status)}</span>
                        </div>
                    </div>
            `;
            
            // Dependencies
            if (node.depends_on && node.depends_on.length > 0) {
                html += `
                    <div style="margin-bottom: 12px;">
                        <div style="font-size: 13px; font-weight: 600; color: #495057; margin-bottom: 4px;">Depends on:</div>
                        <div style="display: flex; flex-wrap: wrap; gap: 6px;">
                `;
                node.depends_on.forEach(dep => {
                    html += `<span style="background: #f8f9fa; padding: 2px 6px; border-radius: 3px; font-size: 12px;">${escapeHtml(dep)}</span>`;
                });
                html += `
                        </div>
                    </div>
                `;
            }
            
            // Parameters
            const params = node.params || {};
            const paramKeys = Object.keys(params);
            if (paramKeys.length > 0) {
                html += `
                    <div>
                        <strong style="font-size: 13px; color: #495057;">Parameters:</strong>
                        <div style="margin-top: 8px; background: #f8f9fa; padding: 12px; border-radius: 4px; font-size: 12px;">
                `;
                
                paramKeys.forEach(key => {
                    const value = params[key];
                    let displayValue;
                    let isLongText = false;
                    
                    if (typeof value === 'object' && value !== null) {
                        displayValue = JSON.stringify(value, null, 2);
                    } else if (typeof value === 'string') {
                        displayValue = escapeHtml(String(value));
                        // Check if this is a long text field (like message text)
                        if (value.length > 80 && (key === 'text' || key === 'message' || key === 'content')) {
                            isLongText = true;
                        }
                    } else {
                        displayValue = escapeHtml(String(value));
                    }
                    
                    if (isLongText) {
                        // Display long text on its own line with wrapping
                        html += `
                            <div style="margin-bottom: 12px;">
                                <div style="color: #007bff; font-weight: 500; margin-bottom: 4px;">${escapeHtml(key)}:</div>
                                <div style="color: #495057; white-space: pre-wrap; word-break: break-word; padding: 8px; background: white; border-radius: 3px; border: 1px solid #dee2e6;">${displayValue}</div>
                            </div>
                        `;
                    } else {
                        html += `
                            <div style="margin-bottom: 6px;">
                                <span style="color: #007bff; font-weight: 500;">${escapeHtml(key)}:</span>
                                <span style="color: #495057; margin-left: 8px;">${displayValue}</span>
                            </div>
                        `;
                    }
                });
                
                html += `
                        </div>
                    </div>
                `;
            }
            
            html += `
                </div>
            `;
        });
    }
    
    html += `
            </div>
        </div>
    `;
    
    // Add refresh button at the bottom
    html += '<div style="margin-top: 16px; padding: 12px; text-align: center; border-top: 1px solid #ddd;">';
    html += `<button onclick="WorkQueueUI.refresh()" style="padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Refresh Work Queue</button>`;
    html += '</div>';
    
    container.innerHTML = html;
    },

    /**
     * Delete all pending tasks in the work queue for the currently selected conversation.
     * Prompts for confirmation before performing the destructive operation.
     */
    delete: function() {
        const agentSelect = document.getElementById('conversations-agent-select');
        const partnerSelect = document.getElementById('conversations-partner-select');
        const userIdInput = document.getElementById('conversations-user-id');

        const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
        const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');

        if (!agentName || !userId) {
            alert('Please select an agent and conversation partner');
            return;
        }

        // Confirmation dialog for destructive operation
        if (!confirm('Are you sure you want to delete ALL pending tasks in this work queue? This action cannot be undone.')) {
            return;
        }

        const container = document.getElementById('work-queue-container');
        const originalContent = container.innerHTML;
        container.innerHTML = '<div class="loading">Deleting work queue...</div>';

        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/work-queue/${userId}`, {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            if (data.error || !data.success) {
                container.innerHTML = originalContent;
                alert(`Error: ${data.error || 'Unknown error occurred'}`);
            } else {
                // Reload to show the empty state
                WorkQueueUI.load();
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = originalContent;
            alert(`Error deleting work queue: ${error}`);
        });
    },

    /**
     * Refresh the work queue and scroll to the bottom.
     * This is called by the refresh button at the bottom of the work queue page.
     */
    refresh: function() {
        WorkQueueUI.load().then(() => {
            // Wait for the next frame to ensure DOM has been updated
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    window.scrollTo(0, document.body.scrollHeight);
                });
            });
        });
    }
};

// Initialize on page load
loadAgents();

// ===== Docs Management Functions =====

// Global docs state
let globalDocsConfigDir = '';
let globalDocsCurrentFilename = '';
// globalDocsSaveTimeout and globalDocsFilenameTimeout are declared in admin_console_core.js
let globalDocsContent = '';

// Agent docs state
let agentDocsCurrentAgent = '';
let agentDocsCurrentFilename = '';
// agentDocsSaveTimeout and agentDocsFilenameTimeout are declared in admin_console_core.js
let agentDocsContent = '';

// Load config directories for global docs
