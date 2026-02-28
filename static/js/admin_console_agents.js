// Admin Console Agents - Agent management, configuration, and profiles
// Copyright (c) 2025-2026 Cindy's World LLC and contributors
// Licensed under the MIT License. See LICENSE.md for details.

async function loadAgents() {
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents`);
        const data = await response.json();
        if (data.error) {
            console.error('Error loading agents:', data.error);
            return;
        }
        
        const agents = data.agents || [];
        
        // Determine which subtab to check content for
        const agentsTabActive = document.querySelector('.tab-panel[data-tab-panel="agents"]')?.classList.contains('active');
        const conversationsTabActive = document.querySelector('.tab-panel[data-tab-panel="conversations"]')?.classList.contains('active');
        
        // Get subtab name for the active tab
        let subtabName = null;
        if (agentsTabActive) {
            subtabName = getCurrentAgentsSubtab();
        } else if (conversationsTabActive) {
            subtabName = getCurrentConversationsSubtab();
        }
        
        // Check content for each agent if we're on agents or conversations tab
        const agentContentChecks = {};
        const shouldCheckContent = subtabName && subtabName !== 'profile';
        if (shouldCheckContent) {
            // For subtabs that have fields in the agents list, use those (already fetched)
            if (subtabName === 'plans') {
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_plans || false;
                });
            } else if (subtabName === 'events') {
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_events || false;
                });
            } else if (subtabName === 'memories') {
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_memories || false;
                });
            } else if (subtabName === 'intentions') {
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_intentions || false;
                });
            } else if (subtabName === 'notes-conv') {
                // Conversations tab: Notes subtab
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_notes || false;
                });
            } else if (subtabName === 'conversation-parameters') {
                // Conversations tab: Parameters subtab
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_conversation_llm || false;
                });
            } else if (subtabName === 'work-queue') {
                // Conversations tab: Work Queue subtab
                agents.forEach(agent => {
                    agentContentChecks[agent.config_name] = agent.has_work_queues || false;
                });
            } else {
                // For other subtabs, call agentHasContent
                await Promise.all(agents.map(async (agent) => {
                    agentContentChecks[agent.config_name] = await agentHasContent(agent.config_name, subtabName);
                }));
            }
        }
        
        // Populate all agent selects
        ['agents-agent-select', 'conversations-agent-select'].forEach(selectId => {
            const select = document.getElementById(selectId);
            if (select) {
                const currentValue = select.value;
                select.innerHTML = '<option value="">Choose an agent...</option>';
                
                agents.forEach(agent => {
                    const option = document.createElement('option');
                    option.value = agent.config_name;
                    // Display format: "Name (agent_id) [@username]" or "Name (agent_id)" or just "Name"
                    // Check explicitly for null/undefined to handle cases where agent_id is 0 (shouldn't happen for Telegram, but be safe)
                    let displayName = (agent.agent_id !== null && agent.agent_id !== undefined) 
                        ? `${agent.name} (${agent.agent_id})` 
                        : agent.name;
                    
                    // Add Telegram username if available
                    if (agent.telegram_username) {
                        displayName += ` [@${agent.telegram_username}]`;
                    }
                    
                    if (agent.is_disabled) {
                        displayName += ' (disabled)';
                        option.style.color = '#666';
                        option.style.fontStyle = 'italic';
                    }

                    // Add asterisk if agent has content for current subtab
                    // For agents tab: only show asterisk in agents-agent-select
                    // For conversations tab: only show asterisk in conversations-agent-select
                    if (subtabName && agentContentChecks[agent.config_name]) {
                        if ((selectId === 'agents-agent-select' && agentsTabActive) ||
                            (selectId === 'conversations-agent-select' && conversationsTabActive)) {
                            displayName += ' *';
                        }
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
        });
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        console.error('Error loading agents:', error);
    }
}

// Set up agent select change handlers with synchronization
document.getElementById('agents-agent-select')?.addEventListener('change', (e) => {
    const rawValue = e.target.value;
    const agentName = stripAsterisk(rawValue);
    
    // Synchronize with conversations agent select
    const conversationsSelect = document.getElementById('conversations-agent-select');
    const conversationsValue = conversationsSelect ? stripAsterisk(conversationsSelect.value) : '';
    if (conversationsSelect && conversationsValue !== agentName) {
        conversationsSelect.value = agentName;
        // Dispatch change event to trigger the change handler, which will clear containers and load partners
        conversationsSelect.dispatchEvent(new Event('change'));
    }
    
    if (agentName) {
        // Load data for the active subtab
        const activeSubtab = document.querySelector('.tab-panel[data-tab-panel="agents"] .tab-button.active');
        if (activeSubtab) {
            const subtabName = activeSubtab.getAttribute('data-subtab');
            if (subtabName === 'profile') {
                loadAgentProfile(agentName);
            } else if (subtabName === 'contacts') {
                loadAgentContacts(agentName);
            } else if (subtabName === 'parameters') {
                loadAgentConfiguration(agentName);
            } else if (subtabName === 'schedule') {
                loadSchedule(agentName);
            } else if (subtabName === 'memories') {
                loadMemories(agentName);
            } else if (subtabName === 'intentions') {
                loadIntentions(agentName);
            } else if (subtabName === 'documents-agent') {
                loadAgentDocs(agentName);
            } else if (subtabName === 'memberships') {
                loadMemberships(agentName);
            } else if (subtabName === 'media') {
                // Mark that Media Editor may need refresh due to Agent->Media changes
                window.mediaEditorNeedsRefresh = true;
                
                // Always reload to ensure fresh data if Media Editor made changes
                loadAgentMedia(agentName);
            } else if (subtabName === 'costs') {
                loadAgentCosts(agentName);
            }
        }
    } else {
        // Agent selection cleared - hide profile section if profile subtab is active
        const activeSubtab = document.querySelector('.tab-panel[data-tab-panel="agents"] .tab-button.active');
        if (activeSubtab) {
            const subtabName = activeSubtab.getAttribute('data-subtab');
            if (subtabName === 'profile') {
                loadAgentProfile('');
            } else if (subtabName === 'contacts') {
                loadAgentContacts('');
            } else if (subtabName === 'schedule') {
                const scheduleContainer = document.getElementById('schedule-container');
                if (scheduleContainer) {
                    if (window._scheduleClockInterval) {
                        clearInterval(window._scheduleClockInterval);
                        window._scheduleClockInterval = null;
                    }
                    scheduleContainer.innerHTML = '<div class="loading">Select an agent to manage schedule</div>';
                }
            } else if (subtabName === 'costs') {
                const costsContainer = document.getElementById('agent-costs-container');
                if (costsContainer) {
                    costsContainer.innerHTML = '<div class="loading">Select an agent to view costs</div>';
                }
            }
        }
    }
});

async function loadAgentCosts(agentName) {
    const container = document.getElementById('agent-costs-container');
    if (!container) return;

    if (!agentName) {
        container.innerHTML = '<div class="loading">Select an agent to view costs</div>';
        return;
    }

    showLoading(container, 'Loading costs...');
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/costs`);
        const data = await response.json();
        if (data.error) {
            showError(container, data.error);
            return;
        }

        const days = data.days || 7;
        const totalCost = Number(data.total_cost || 0);
        const logs = data.logs || [];

        let html = `
            <div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <h3 style="margin-top: 0;">Agent Costs (Last ${days} Days)</h3>
                <div style="font-size: 20px; font-weight: 600; margin-bottom: 12px;">Total: $${totalCost.toFixed(4)}</div>
        `;

        if (logs.length === 0) {
            html += '<div class="placeholder-card">No cost logs found for this period.</div>';
        } else {
            html += '<div style="overflow-x: auto;"><table style="width: 100%; border-collapse: collapse;">';
            html += '<thead><tr style="border-bottom: 1px solid #ddd; text-align: left;">';
            html += '<th style="padding: 8px;">Timestamp</th>';
            html += '<th style="padding: 8px;">Channel</th>';
            html += '<th style="padding: 8px;">Operation</th>';
            html += '<th style="padding: 8px;">Model</th>';
            html += '<th style="padding: 8px;">Input</th>';
            html += '<th style="padding: 8px;">Output</th>';
            html += '<th style="padding: 8px;">Cost</th>';
            html += '</tr></thead><tbody>';
            html += logs.map(log => `
                <tr style="border-bottom: 1px solid #f0f0f0;">
                    <td style="padding: 8px;">${escapeHtml(formatTimestamp(log.timestamp))}</td>
                    <td style="padding: 8px;">${escapeHtml(String(log.channel_telegram_id || ''))}</td>
                    <td style="padding: 8px;">${escapeHtml(log.operation || '')}</td>
                    <td style="padding: 8px;">${escapeHtml(log.model_name || '')}</td>
                    <td style="padding: 8px;">${escapeHtml(String(log.input_tokens ?? ''))}</td>
                    <td style="padding: 8px;">${escapeHtml(String(log.output_tokens ?? ''))}</td>
                    <td style="padding: 8px;">$${Number(log.cost || 0).toFixed(4)}</td>
                </tr>
            `).join('');
            html += '</tbody></table></div>';
        }

        html += '</div>';
        container.innerHTML = html;
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        container.innerHTML = `<div class="error">Error loading costs: ${escapeHtml(error.message || error)}</div>`;
    }
}

// Refresh recent conversations when dropdown is opened (background refresh)
const recentConversationsSelect = document.getElementById('recent-conversations-select');
if (recentConversationsSelect) {
    // Trigger refresh on mousedown (when user clicks to open dropdown)
    recentConversationsSelect.addEventListener('mousedown', () => {
        loadRecentConversations(); // Fire and forget - updates in background
    });
    
    // Also trigger on focus for keyboard navigation
    recentConversationsSelect.addEventListener('focus', () => {
        loadRecentConversations(); // Fire and forget - updates in background
    });
}

document.getElementById('recent-conversations-select')?.addEventListener('change', async (e) => {
    const value = e.target.value;
    if (!value) {
        return;
    }
    
    try {
        const convData = JSON.parse(value);
        const agentConfigName = convData.agent_config_name;
        const channelId = convData.channel_id;
        
        // Set agent select
        const agentSelect = document.getElementById('conversations-agent-select');
        if (agentSelect) {
            agentSelect.value = agentConfigName;
        }
        
        // Synchronize with agents agent select
        const agentsSelect = document.getElementById('agents-agent-select');
        if (agentsSelect) {
            agentsSelect.value = agentConfigName;
        }
        
        // Clear user-id input initially (will be set after partner selection)
        const userIdInput = document.getElementById('conversations-user-id');
        const partnerSelect = document.getElementById('conversations-partner-select');
        if (userIdInput) {
            userIdInput.value = '';
        }
        if (partnerSelect) {
            partnerSelect.value = '';
        }
        
        // Load conversation partners (this will populate the dropdown)
        if (agentConfigName) {
            await loadConversationPartners(agentConfigName, 'conversations');
            
            // After partners are loaded, select the matching conversation
            if (partnerSelect) {
                // Find the option with matching channelId (strip asterisks for comparison)
                const channelIdStr = String(channelId);
                for (let i = 0; i < partnerSelect.options.length; i++) {
                    const option = partnerSelect.options[i];
                    const optionValue = stripAsterisk(option.value);
                    if (optionValue === channelIdStr) {
                        partnerSelect.value = option.value;
                        // Trigger change event to ensure consistency with user interaction
                        partnerSelect.dispatchEvent(new Event('change', { bubbles: true }));
                        return; // Exit early since change event will call loadConversationData
                    }
                }
            }
        }
        
        // If no matching partner was found in dropdown, set channelId as fallback
        if (userIdInput && channelId) {
            userIdInput.value = channelId;
        }
        
        // Load the conversation data
        loadConversationData();
    } catch (error) {
        console.error('Error parsing recent conversation selection:', error);
    }
});

document.getElementById('conversations-agent-select')?.addEventListener('change', (e) => {
    const agentName = stripAsterisk(e.target.value);
    
    // Clear all conversation content containers when agent changes
    const conversationContainer = document.getElementById('conversation-container');
    if (conversationContainer) {
        conversationContainer.innerHTML = '<div class="loading">Select an agent and conversation partner</div>';
    }
    
    const notesContainer = document.getElementById('notes-conv-container');
    if (notesContainer) {
        notesContainer.innerHTML = '<div class="loading">Select an agent and conversation partner</div>';
    }
    
    const conversationParametersContainer = document.getElementById('conversation-parameters-container');
    if (conversationParametersContainer) {
        showLoading(conversationParametersContainer, 'Select an agent and conversation partner');
    }
    
    const plansContainer = document.getElementById('plans-container');
    if (plansContainer) {
        plansContainer.innerHTML = '';
    }
    
    const xsendContainer = document.getElementById('xsend-container');
    if (xsendContainer) {
        // Reset xsend container to initial state: show loading, hide content
        const loadingDiv = xsendContainer.querySelector('.loading');
        if (loadingDiv) {
            loadingDiv.style.display = 'block';
            const xsendContent = document.getElementById('xsend-content');
            if (xsendContent) {
                xsendContent.style.display = 'none';
            }
            // Clear textarea value and status div content
            const intentTextarea = document.getElementById('xsend-intent-textarea');
            if (intentTextarea) {
                intentTextarea.value = '';
            }
            const statusDiv = document.getElementById('xsend-status');
            if (statusDiv) {
                statusDiv.innerHTML = '';
            }
        } else {
            // If loading div doesn't exist (edge case), restore initial structure
            xsendContainer.innerHTML = '<div class="loading">Select an agent and conversation partner</div><div style="margin-top: 16px; display: none;" id="xsend-content"><div class="directory-selector"><label for="xsend-intent-textarea">Intent:</label><br><textarea id="xsend-intent-textarea" placeholder="Enter the intent message..." style="width: 100%; min-height: 150px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical; box-sizing: border-box;"></textarea></div><div style="margin-top: 16px;"><button onclick="sendXSend()" style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">XSend</button><div id="xsend-status" style="margin-top: 8px; font-size: 14px;"></div></div></div>';
        }
    }
    
    // Clear partner select and user ID input
    const partnerSelect = document.getElementById('conversations-partner-select');
    if (partnerSelect) {
        partnerSelect.innerHTML = '<option value="">Select Conversation</option>';
    }
    
    const userIdInput = document.getElementById('conversations-user-id');
    if (userIdInput) {
        userIdInput.value = '';
    }
    
    // Reset recent conversations select to ensure consistency (selection may point to different agent)
    const recentConversationsSelect = document.getElementById('recent-conversations-select');
    if (recentConversationsSelect) {
        recentConversationsSelect.value = '';
    }
    
    // Synchronize with agents agent select
    const agentsSelect = document.getElementById('agents-agent-select');
    const agentsValue = agentsSelect ? stripAsterisk(agentsSelect.value) : '';
    if (agentsSelect && agentsValue !== agentName) {
        agentsSelect.value = agentName;
    }
    
    if (agentName) {
        loadConversationPartners(agentName, 'conversations');
    }
});

document.getElementById('conversations-partner-select')?.addEventListener('change', (e) => {
    document.getElementById('conversations-user-id').value = '';
    // Automatically load when partner is selected (only if non-empty)
    const userId = stripAsterisk(e.target.value);
    if (userId) {
        loadConversationData();
    }
});

async function loadConversationData() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    let userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
    
    if (!agentName) {
        alert('Please select an agent');
        return;
    }
    
    if (!userId) {
        alert('Please enter/select a conversation partner');
        return;
    }
    
    // If userId is from text input and agent is selected, try to populate dropdowns
    const userIdFromInput = userIdInput?.value.trim();
    if (userIdFromInput && agentName) {
        // Ensure partners are loaded
        await loadConversationPartners(agentName, 'conversations');
        
        // Try to find matching conversation in dropdown
        // Check if userId is numeric (direct user ID match)
        const isNumeric = /^-?\d+$/.test(userIdFromInput);
        let foundMatch = false;
        if (isNumeric) {
            // Find the option with matching user ID (strip asterisks for comparison)
            for (let i = 0; i < partnerSelect.options.length; i++) {
                const option = partnerSelect.options[i];
                const optionValue = stripAsterisk(option.value);
                if (optionValue === userIdFromInput) {
                    partnerSelect.value = option.value;
                    userIdInput.value = ''; // Clear input since we selected from dropdown
                    userId = optionValue; // Update userId for loading
                    foundMatch = true;
                    // Trigger change event to ensure consistency with user interaction
                    partnerSelect.dispatchEvent(new Event('change', { bubbles: true }));
                    return; // Exit early since change event will call loadConversationData
                }
            }
        } else {
            // For username/phone, try to match by username in dropdown
            // Usernames in dropdown are in format "Name (user_id) [@username]"
            const usernameToMatch = userIdFromInput.startsWith('@') 
                ? userIdFromInput.substring(1) 
                : userIdFromInput;
            
            for (let i = 0; i < partnerSelect.options.length; i++) {
                const option = partnerSelect.options[i];
                const optionText = option.textContent;
                // Check if option text contains [@username] matching our input
                const usernameMatch = optionText.match(/\[@([^\]]+)\]/);
                if (usernameMatch && usernameMatch[1].toLowerCase() === usernameToMatch.toLowerCase()) {
                    partnerSelect.value = option.value;
                    userIdInput.value = ''; // Clear input since we selected from dropdown
                    userId = stripAsterisk(option.value); // Update userId for loading
                    foundMatch = true;
                    // Trigger change event to ensure consistency with user interaction
                    partnerSelect.dispatchEvent(new Event('change', { bubbles: true }));
                    return; // Exit early since change event will call loadConversationData
                }
            }
        }
    }
    
    // Load data for the active subtab (this always reloads the data)
    const activeSubtab = document.querySelector('.tab-panel[data-tab-panel="conversations"] .tab-button.active');
    if (activeSubtab) {
        const subtabName = activeSubtab.getAttribute('data-subtab');
        switchSubtab(subtabName);
    }
}

function prefetchConversationProfilePhotos(currentIndex) {
    const count = conversationProfilePhotoCount;
    if (count <= 0) return;
    const indices = [
        (currentIndex + 1) % count,
        (currentIndex + 2) % count,
        (currentIndex - 1 + count) % count
    ];
    indices.forEach((i) => ensureConversationProfilePhotoLoaded(i));
}

async function ensureConversationProfilePhotoLoaded(index) {
    if (conversationProfilePhotoCount <= 0 || index < 0 || index >= conversationProfilePhotoCount) return;
    if (conversationProfilePhotos[index]) return;
    const agentName = conversationProfileAgentName;
    const userId = conversationProfileUserId;
    if (!agentName || !userId) return;
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/partner-profile/${encodeURIComponent(userId)}/photo/${index}`);
        const data = await response.json();
        if (data.data_url) {
            conversationProfilePhotos[index] = data.data_url;
            updateConversationProfilePhotoDisplay();
            prefetchConversationProfilePhotos(index);
        }
    } catch (e) {
        if (e && e.message === 'unauthorized') return;
        console.debug('Failed to load conversation profile photo at index', index, e);
    }
}

function updateConversationProfilePhotoDisplay() {
    const photoImg = document.getElementById('conversation-profile-photo');
    const profileVideo = document.getElementById('conversation-profile-video');
    const fullImg = document.getElementById('conversation-profile-photo-fullscreen');
    const fullVideo = document.getElementById('conversation-profile-video-fullscreen');
    const indexLabel = document.getElementById('conversation-profile-photo-index');
    const prevBtn = document.getElementById('conversation-profile-photo-prev');
    const nextBtn = document.getElementById('conversation-profile-photo-next');
    const fullPrevBtn = document.getElementById('conversation-profile-photo-fullscreen-prev');
    const fullNextBtn = document.getElementById('conversation-profile-photo-fullscreen-next');
    const fullMetaIndex = document.getElementById('conversation-profile-photo-meta-index');
    const fullMetaType = document.getElementById('conversation-profile-photo-meta-type');
    const count = conversationProfilePhotoCount;
    const photos = conversationProfilePhotos;
    const hasPhotos = count > 0;
    const hasMultiple = count > 1;

    if (!hasPhotos) {
        if (photoImg) {
            photoImg.src = '';
            photoImg.style.display = 'none';
        }
        if (profileVideo) {
            profileVideo.pause();
            profileVideo.src = '';
            profileVideo.style.display = 'none';
        }
        if (fullVideo) {
            fullVideo.pause();
            fullVideo.src = '';
            fullVideo.style.display = 'none';
        }
        if (indexLabel) {
            indexLabel.textContent = '';
        }
        if (fullMetaIndex) {
            fullMetaIndex.innerHTML = '<strong>Item:</strong> 0 of 0';
        }
        if (fullMetaType) {
            fullMetaType.innerHTML = '<strong>Type:</strong> unknown';
        }
        [prevBtn, nextBtn, fullPrevBtn, fullNextBtn].forEach(btn => {
            if (btn) btn.style.display = 'none';
        });
        return;
    }

    const safeIndex = Math.min(Math.max(conversationProfilePhotoIndex, 0), count - 1);
    conversationProfilePhotoIndex = safeIndex;
    const src = photos[safeIndex];
    if (!src) {
        ensureConversationProfilePhotoLoaded(safeIndex);
        if (indexLabel) indexLabel.textContent = `${safeIndex + 1} of ${count}`;
        if (fullMetaIndex) fullMetaIndex.innerHTML = `<strong>Item:</strong> ${safeIndex + 1} of ${count}`;
        [prevBtn, nextBtn, fullPrevBtn, fullNextBtn].forEach(btn => {
            if (btn) btn.style.display = hasMultiple ? 'inline-block' : 'none';
        });
        prefetchConversationProfilePhotos(safeIndex);
        return;
    }
    const isVideo = String(src || '').startsWith('data:video/');
    if (isVideo) {
        if (photoImg) {
            photoImg.src = '';
            photoImg.style.display = 'none';
        }
        if (fullImg) {
            fullImg.src = '';
            fullImg.style.display = 'none';
        }
        if (profileVideo) {
            profileVideo.src = src;
            profileVideo.style.display = 'block';
            profileVideo.play().catch(() => {});
        }
        if (fullVideo) {
            fullVideo.src = src;
            fullVideo.style.display = 'block';
            fullVideo.play().catch(() => {});
        }
    } else {
        if (profileVideo) {
            profileVideo.pause();
            profileVideo.src = '';
            profileVideo.style.display = 'none';
        }
        if (fullVideo) {
            fullVideo.pause();
            fullVideo.src = '';
            fullVideo.style.display = 'none';
        }
        if (photoImg) {
            photoImg.src = src;
            photoImg.style.display = 'block';
        }
        if (fullImg) {
            fullImg.src = src;
            fullImg.style.display = 'block';
        }
    }
    if (indexLabel) {
        indexLabel.textContent = `${safeIndex + 1} of ${count}`;
    }
    if (fullMetaIndex) {
        fullMetaIndex.innerHTML = `<strong>Item:</strong> ${safeIndex + 1} of ${count}`;
    }
    if (fullMetaType) {
        fullMetaType.innerHTML = `<strong>Type:</strong> ${isVideo ? 'video' : 'image'}`;
    }
    [prevBtn, nextBtn, fullPrevBtn, fullNextBtn].forEach(btn => {
        if (btn) btn.style.display = hasMultiple ? 'inline-block' : 'none';
    });
    prefetchConversationProfilePhotos(safeIndex);
}

function showConversationProfilePhotoFullscreen() {
    const modal = document.getElementById('conversation-profile-photo-modal');
    if (!modal || conversationProfilePhotoCount === 0) {
        return;
    }
    updateConversationProfilePhotoDisplay();
    modal.style.display = 'block';
}

function closeConversationProfilePhotoFullscreen() {
    const modal = document.getElementById('conversation-profile-photo-modal');
    const fullVideo = document.getElementById('conversation-profile-video-fullscreen');
    if (modal) {
        modal.style.display = 'none';
    }
    if (fullVideo) {
        fullVideo.pause();
    }
}

function showPreviousConversationProfilePhoto(event, includeFullscreen = false) {
    if (event) event.stopPropagation();
    if (conversationProfilePhotoCount <= 1) return;
    conversationProfilePhotoIndex = (conversationProfilePhotoIndex - 1 + conversationProfilePhotoCount) % conversationProfilePhotoCount;
    updateConversationProfilePhotoDisplay();
    if (includeFullscreen) {
        showConversationProfilePhotoFullscreen();
    }
}

function showNextConversationProfilePhoto(event, includeFullscreen = false) {
    if (event) event.stopPropagation();
    if (conversationProfilePhotoCount <= 1) return;
    conversationProfilePhotoIndex = (conversationProfilePhotoIndex + 1) % conversationProfilePhotoCount;
    updateConversationProfilePhotoDisplay();
    if (includeFullscreen) {
        showConversationProfilePhotoFullscreen();
    }
}

function setConversationProfileEditable(isEditable) {
    const firstNameInput = document.getElementById('conversation-profile-first-name');
    const lastNameInput = document.getElementById('conversation-profile-last-name');
    const saveBtn = document.getElementById('conversation-profile-save-btn');

    [firstNameInput, lastNameInput].forEach(input => {
        if (!input) return;
        input.readOnly = !isEditable;
        if (isEditable) {
            input.style.background = '#ffffff';
            input.style.color = '#2c3e50';
        } else {
            input.style.background = '#f5f5f5';
            input.style.color = '#666';
        }
    });

    if (saveBtn && !isEditable) {
        saveBtn.disabled = true;
    }
}

function conversationProfileFieldChanged() {
    const checkbox = document.getElementById('conversation-profile-contact-checkbox');
    if (!checkbox || !checkbox.checked || !originalConversationProfile) {
        return;
    }

    const firstName = document.getElementById('conversation-profile-first-name').value.trim();
    const lastName = document.getElementById('conversation-profile-last-name').value.trim();
    const originalFirst = originalConversationProfile.first_name || '';
    const originalLast = originalConversationProfile.last_name || '';
    const hasChanges = firstName !== originalFirst || lastName !== originalLast;
    setConversationProfileNeedsSave(hasChanges);
    const cancelBtn = document.getElementById('conversation-profile-cancel-btn');
    if (cancelBtn) {
        toggle(cancelBtn, hasChanges, 'inline-block');
    }
}

function setConversationProfileNeedsSave(needsSave) {
    const saveBtn = document.getElementById('conversation-profile-save-btn');
    if (!saveBtn) return;
    saveBtn.disabled = !needsSave;
    if (needsSave) {
        saveBtn.style.background = '#ffc107';
        saveBtn.style.color = '#2c3e50';
        saveBtn.style.boxShadow = '0 0 0 2px rgba(255, 193, 7, 0.4)';
    } else {
        saveBtn.style.background = '#007bff';
        saveBtn.style.color = 'white';
        saveBtn.style.boxShadow = 'none';
    }
}

function cancelConversationProfileEdit() {
    if (!originalConversationProfile) {
        return;
    }
    document.getElementById('conversation-profile-first-name').value = originalConversationProfile.first_name || '';
    document.getElementById('conversation-profile-last-name').value = originalConversationProfile.last_name || '';
    setConversationProfileNeedsSave(false);
    const cancelBtn = document.getElementById('conversation-profile-cancel-btn');
    if (cancelBtn) {
        cancelBtn.style.display = 'none';
    }
}

function handleConversationContactToggle() {
    const checkbox = document.getElementById('conversation-profile-contact-checkbox');
    if (!checkbox || checkbox.disabled) {
        return;
    }

    const isContact = checkbox.checked;
    if (!isContact && originalConversationProfile) {
        document.getElementById('conversation-profile-first-name').value = originalConversationProfile.first_name || '';
        document.getElementById('conversation-profile-last-name').value = originalConversationProfile.last_name || '';
    }
    setConversationProfileEditable(isContact);
    setConversationProfileNeedsSave(true);
    const cancelBtn = document.getElementById('conversation-profile-cancel-btn');
    if (cancelBtn) {
        cancelBtn.style.display = 'none';
    }
    saveConversationProfile();
}

async function loadConversationProfile() {
    const profileSection = document.getElementById('conversation-profile-section');
    const profileContainer = document.getElementById('conversation-profile-container');
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');

    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');

    if (!agentName || !userId) {
        if (profileSection) {
            profileSection.style.display = 'none';
        }
        if (profileContainer) {
            profileContainer.style.display = 'block';
        }
        expectedConversationProfile = null;
        conversationProfilePhotos = [];
        conversationProfilePhotoCount = 0;
        conversationProfilePhotoIndex = 0;
        updateConversationProfilePhotoDisplay();
        return;
    }

    if (profileSection) {
        profileSection.style.display = 'block';
    }
    if (profileContainer) {
        profileContainer.style.display = 'none';
    }

    const requestKey = `${agentName}:${userId}`;
    expectedConversationProfile = requestKey;

    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/partner-profile/${encodeURIComponent(userId)}`);
        const data = await response.json();
        if (data.error) {
            console.error('Error loading conversation profile:', data.error);
            if (profileSection) {
                profileSection.style.display = 'none';
            }
            if (profileContainer) {
                profileContainer.style.display = 'block';
            }
        expectedConversationProfile = null;
        conversationProfilePhotos = [];
        conversationProfilePhotoCount = 0;
        conversationProfilePhotoIndex = 0;
        updateConversationProfilePhotoDisplay();
            return;
        }

        if (expectedConversationProfile !== requestKey) {
            return;
        }

        const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
        const currentUserId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');
        if (currentAgentName !== agentName || currentUserId !== userId) {
            return;
        }

        originalConversationProfile = JSON.parse(JSON.stringify(data));
        currentConversationProfile = data;

        // Determine if this is a group/channel or user
        const isUser = data.partner_type === 'user';
        const isGroup = data.partner_type && data.partner_type !== 'user';
        
        // Update field labels and visibility based on entity type
        const firstNameLabel = document.getElementById('conversation-profile-first-name-label');
        const lastNameContainer = document.getElementById('conversation-profile-last-name-container');
        const nameRow = document.getElementById('conversation-profile-name-row');
        const birthdayContainer = document.getElementById('conversation-profile-birthday-container');
        const memberCountContainer = document.getElementById('conversation-profile-member-count-container');
        const bioLabel = document.getElementById('conversation-profile-bio-label');

        if (isUser) {
            // User layout
            if (firstNameLabel) firstNameLabel.textContent = 'First Name:';
            if (lastNameContainer) lastNameContainer.style.display = 'block';
            if (nameRow) nameRow.style.gridTemplateColumns = '1fr 1fr';
            if (birthdayContainer) birthdayContainer.style.display = 'block';
            if (memberCountContainer) memberCountContainer.style.display = 'none';
            if (bioLabel) bioLabel.textContent = 'Bio:';
        } else {
            // Group/Channel layout
            if (firstNameLabel) firstNameLabel.textContent = 'Title:';
            if (lastNameContainer) lastNameContainer.style.display = 'none';
            if (nameRow) nameRow.style.gridTemplateColumns = '1fr';
            if (birthdayContainer) birthdayContainer.style.display = 'none';
            if (memberCountContainer) memberCountContainer.style.display = 'block';
            if (bioLabel) bioLabel.textContent = 'Description:';
        }

        document.getElementById('conversation-profile-first-name').value = data.first_name || '';
        document.getElementById('conversation-profile-last-name').value = data.last_name || '';
        document.getElementById('conversation-profile-username').value = data.username || '';
        document.getElementById('conversation-profile-telegram-id').value = data.telegram_id || '';
        
        // Set member count for groups/channels
        const memberCountInput = document.getElementById('conversation-profile-member-count');
        if (memberCountInput && data.participants_count !== null && data.participants_count !== undefined) {
            memberCountInput.value = data.participants_count.toLocaleString();
        } else if (memberCountInput) {
            memberCountInput.value = '';
        }

        const conversationBioTextarea = document.getElementById('conversation-profile-bio');
        resetTextareaHeight(conversationBioTextarea);
        conversationBioTextarea.value = data.bio || '';
        autoGrowTextarea(conversationBioTextarea);

        conversationProfilePhotoCount = Math.max(0, parseInt(data.profile_photo_count, 10) || 0);
        conversationProfilePhotos = new Array(conversationProfilePhotoCount);
        if (conversationProfilePhotoCount > 0 && data.profile_photo) {
            conversationProfilePhotos[0] = data.profile_photo;
        }
        conversationProfileAgentName = agentName;
        conversationProfileUserId = userId;
        conversationProfilePhotoIndex = 0;
        updateConversationProfilePhotoDisplay();
        prefetchConversationProfilePhotos(0);

        // Handle birthday (only for users)
        const monthSelect = document.getElementById('conversation-profile-birthday-month');
        const daySelect = document.getElementById('conversation-profile-birthday-day');
        const yearInput = document.getElementById('conversation-profile-birthday-year');
        monthSelect.value = '';
        daySelect.innerHTML = '<option value="">Day</option>';
        yearInput.value = '';

        if (data.birthday && isUser) {
            monthSelect.value = data.birthday.month || '';
            const days = data.birthday.month === 2
                ? 29
                : [4, 6, 9, 11].includes(data.birthday.month)
                    ? 30
                    : 31;
            for (let day = 1; day <= days; day++) {
                const option = document.createElement('option');
                option.value = day;
                option.textContent = day;
                daySelect.appendChild(option);
            }
            if (data.birthday.day) {
                daySelect.value = data.birthday.day;
            }
            if (data.birthday.year) {
                yearInput.value = data.birthday.year;
            }
        }

        const deletedIndicator = document.getElementById('conversation-profile-deleted');
        if (deletedIndicator) {
            toggle(deletedIndicator, data.is_deleted, 'block');
        }

        const contactCheckbox = document.getElementById('conversation-profile-contact-checkbox');
        if (contactCheckbox) {
            contactCheckbox.checked = !!data.is_contact;
            contactCheckbox.disabled = !data.can_edit_contact;
        }
        setConversationProfileEditable(!!data.is_contact && !!data.can_edit_contact);

        const saveBtn = document.getElementById('conversation-profile-save-btn');
        const statusDiv = document.getElementById('conversation-profile-save-status');
        setConversationProfileNeedsSave(false);
        if (statusDiv) statusDiv.textContent = '';
        const cancelBtn = document.getElementById('conversation-profile-cancel-btn');
        if (cancelBtn) {
            cancelBtn.style.display = 'none';
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        console.error('Error loading conversation profile:', error);
        if (profileSection) {
            profileSection.style.display = 'none';
        }
        if (profileContainer) {
            profileContainer.style.display = 'block';
        }
        expectedConversationProfile = null;
        conversationProfilePhotos = [];
        conversationProfilePhotoCount = 0;
        conversationProfilePhotoIndex = 0;
        updateConversationProfilePhotoDisplay();
    }
}

async function saveConversationProfile() {
    const agentSelect = document.getElementById('conversations-agent-select');
    const partnerSelect = document.getElementById('conversations-partner-select');
    const userIdInput = document.getElementById('conversations-user-id');
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    const userId = userIdInput?.value.trim() || (partnerSelect ? stripAsterisk(partnerSelect.value) : '');

    if (!agentName || !userId) {
        alert('Please select an agent and conversation partner');
        return;
    }

    const saveBtn = document.getElementById('conversation-profile-save-btn');
    const statusDiv = document.getElementById('conversation-profile-save-status');
    const contactCheckbox = document.getElementById('conversation-profile-contact-checkbox');
    if (!contactCheckbox || contactCheckbox.disabled) {
        return;
    }

    const firstName = document.getElementById('conversation-profile-first-name').value.trim();
    const lastName = document.getElementById('conversation-profile-last-name').value.trim();

    if (saveBtn) saveBtn.disabled = true;
    if (statusDiv) {
        statusDiv.textContent = 'Saving...';
        statusDiv.style.color = '#007bff';
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/partner-profile/${encodeURIComponent(userId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                is_contact: contactCheckbox.checked,
                first_name: firstName,
                last_name: lastName
            })
        });
        const data = await response.json();
        if (data.error) {
            if (statusDiv) {
                statusDiv.textContent = `Error: ${data.error}`;
                statusDiv.style.color = '#dc3545';
            }
            if (saveBtn) saveBtn.disabled = false;
            return;
        }

        originalConversationProfile = JSON.parse(JSON.stringify(data));
        currentConversationProfile = data;
        conversationProfilePhotoCount = Math.max(0, parseInt(data.profile_photo_count, 10) || 0);
        conversationProfilePhotos = new Array(conversationProfilePhotoCount);
        if (conversationProfilePhotoCount > 0 && data.profile_photo) {
            conversationProfilePhotos[0] = data.profile_photo;
        }
        conversationProfilePhotoIndex = 0;
        updateConversationProfilePhotoDisplay();
        prefetchConversationProfilePhotos(0);

        document.getElementById('conversation-profile-first-name').value = data.first_name || '';
        document.getElementById('conversation-profile-last-name').value = data.last_name || '';

        contactCheckbox.checked = !!data.is_contact;
        contactCheckbox.disabled = !data.can_edit_contact;
        setConversationProfileEditable(!!data.is_contact && !!data.can_edit_contact);

        const deletedIndicator = document.getElementById('conversation-profile-deleted');
        if (deletedIndicator) {
            toggle(deletedIndicator, data.is_deleted, 'block');
        }

        if (statusDiv) {
            statusDiv.textContent = 'Saved';
            statusDiv.style.color = '#28a745';
        }
        setConversationProfileNeedsSave(false);
        const cancelBtn = document.getElementById('conversation-profile-cancel-btn');
        if (cancelBtn) {
            cancelBtn.style.display = 'none';
        }
    } catch (error) {
        if (statusDiv) {
            statusDiv.textContent = `Error: ${error}`;
            statusDiv.style.color = '#dc3545';
        }
        if (saveBtn) saveBtn.disabled = false;
    }
}

async function loadAgentContacts(agentName) {
    const container = document.getElementById('agent-contacts-container');
    if (!container) return;

    if (!agentName) {
        contactAvatarLoadToken += 1;
        selectedAgentContacts = new Set();
        selectedAgentContactsAgent = null;
        currentAgentContactsUserIds = [];
        expectedAgentContacts = null;
        contactFullscreenPhotos = [];
        contactFullscreenPhotoIndex = 0;
        closeContactPhotoFullscreen();
        showLoading(container, 'Select an agent to view contacts');
        return;
    }

    const agentSelect = document.getElementById('agents-agent-select');
    const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    if (currentAgentName !== agentName) {
        return;
    }

    showLoading(container, 'Loading contacts...');
    const requestKey = agentName;
    expectedAgentContacts = requestKey;
    const avatarLoadToken = ++contactAvatarLoadToken;
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/contacts`);
        const data = await response.json();
        if (expectedAgentContacts !== requestKey) {
            return;
        }
        const refreshedAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
        if (refreshedAgentName !== agentName) {
            return;
        }
        if (data.error) {
            showError(container, data.error);
            return;
        }

        const contacts = data.contacts || [];
        if (contacts.length === 0) {
            selectedAgentContacts = new Set();
            selectedAgentContactsAgent = agentName;
            currentAgentContactsUserIds = [];
            contactFullscreenPhotos = [];
            contactFullscreenPhotoIndex = 0;
            closeContactPhotoFullscreen();
            container.innerHTML = '<div class="placeholder-card">No contacts found.</div>';
            return;
        }

        currentAgentContactsUserIds = contacts.map(contact => String(contact.user_id));
        if (selectedAgentContactsAgent !== agentName) {
            selectedAgentContactsAgent = agentName;
            selectedAgentContacts = new Set();
        } else {
            selectedAgentContacts = new Set(
                [...selectedAgentContacts].filter(id => currentAgentContactsUserIds.includes(id))
            );
        }

        const bulkControlsHtml = `
            <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap;">
                <label style="display: flex; align-items: center; gap: 6px; font-weight: 500; color: #2c3e50;">
                    <input type="checkbox" id="agent-contacts-select-all" onchange="toggleAgentContactsSelectAll(this.checked)">
                    Select all
                </label>
                <button onclick="bulkDeleteAgentContacts('${escJsAttr(agentName)}')" id="agent-contacts-delete-selected" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Delete selected</button>
                <div id="agent-contacts-selected-count" style="font-size: 12px; color: #666;">0 selected</div>
            </div>
        `;

        function getAvatarFallbackInitial(displayName, fallbackId) {
            const label = String(displayName || fallbackId || '').trim();
            if (!label) return '?';
            return label.charAt(0).toUpperCase();
        }

        function renderRoundAvatarButton(photoDataUrl, displayName, fallbackId, onClickJs, titleText = 'View profile photos', avatarMeta = null) {
            const initial = escapeHtml(getAvatarFallbackInitial(displayName, fallbackId));
            const dataAttrs = avatarMeta
                ? ` data-contact-avatar-pending="true" data-contact-user-id="${escapeHtml(String(avatarMeta.userId))}" data-contact-agent-name="${escapeHtml(String(avatarMeta.agentName))}"`
                : '';
            if (photoDataUrl) {
                const mediaHtml = String(photoDataUrl).startsWith('data:video/')
                    ? `<video src="${escapeHtml(photoDataUrl)}" aria-label="Avatar video" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; background: #f8f9fa;" autoplay muted loop playsinline></video>`
                    : `<img src="${escapeHtml(photoDataUrl)}" alt="Avatar" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; background: #f8f9fa;">`;
                return `
                    <button onclick="${onClickJs}" title="${escapeHtml(titleText)}"${dataAttrs} style="position: relative; width: 34px; height: 34px; border-radius: 999px; border: 1px solid #ced4da; padding: 0; cursor: pointer; background: #f8f9fa; overflow: hidden; flex: 0 0 34px;">
                        ${mediaHtml}
                    </button>
                `;
            }

            return `
                <button onclick="${onClickJs}" title="${escapeHtml(titleText)}"${dataAttrs} style="position: relative; width: 34px; height: 34px; border-radius: 999px; border: 1px solid #ced4da; padding: 0; cursor: pointer; background: #eef2f7; color: #2c3e50; display: inline-flex; align-items: center; justify-content: center; overflow: hidden; flex: 0 0 34px; font-weight: 700; font-size: 15px; line-height: 1;">
                    ${initial}
                </button>
            `;
        }

        const contactsHtml = contacts.map(contact => {
            const deletedBadge = contact.is_deleted
                ? '<span style="color: #dc3545; font-weight: 500; margin-left: 8px;">Deleted account</span>'
                : '';
            const blockedBadge = contact.is_blocked
                ? '<span style="color: #dc3545; font-weight: 500; margin-left: 8px;">Blocked</span>'
                : '';
            const usernameLine = contact.username ? `<div><strong>Username:</strong> @${escapeHtml(contact.username)}</div>` : '';
            const displayPhone = contact.phone ? (String(contact.phone).startsWith('+') ? contact.phone : '+' + contact.phone) : '';
            const phoneLine = displayPhone ? `<div><strong>Phone:</strong> ${escapeHtml(displayPhone)}</div>` : '';
            const userId = String(contact.user_id);
            const isChecked = selectedAgentContacts.has(userId) ? 'checked' : '';
            const escapedAgentName = escJsAttr(agentName);
            const escapedUserId = escJsAttr(contact.user_id);
            const escapedContactName = escapeHtml(contact.name || contact.user_id);
            const avatarHtml = renderRoundAvatarButton(
                contact.avatar_photo,
                contact.name,
                contact.user_id,
                `openContactPhotos('${escapedAgentName}', '${escapedUserId}'); return false;`,
                'View profile photos',
                (contact.has_photo && (!contact.avatar_photo || contact.avatar_needs_upgrade)) ? { userId, agentName } : null
            );
            return `
                <div class="memory-item" style="background: white; padding: 16px; margin-bottom: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="display: flex; align-items: start; gap: 12px;">
                            <label style="margin-top: 2px;">
                                <input type="checkbox" data-contact-checkbox="true" ${isChecked} onchange="toggleAgentContactSelection('${escJsAttr(userId)}', this.checked)">
                            </label>
                            ${avatarHtml}
                            <div style="min-width: 0;">
                            <div><strong>Name:</strong> <a href="#" onclick="openConversationFromContacts('${escapedAgentName}', '${escapedUserId}'); return false;">${escapedContactName}</a>${deletedBadge}${blockedBadge}</div>
                            <div><strong>ID:</strong> ${escapeHtml(contact.user_id)}</div>
                            ${usernameLine}
                            ${phoneLine}
                            </div>
                    </div>
                </div>
            `;
        }).join('');
        container.innerHTML = bulkControlsHtml + contactsHtml;
        updateAgentContactsSelectionUI();
        streamAgentContactAvatars(agentName, avatarLoadToken);
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        container.innerHTML = `<div class="error">Error loading contacts: ${escapeHtml(error)}</div>`;
    }
}

async function streamAgentContactAvatars(agentName, token) {
    const pending = Array.from(document.querySelectorAll('button[data-contact-avatar-pending="true"]'));
    if (pending.length === 0) {
        return;
    }

    const maxConcurrent = 1;
    let index = 0;
    let active = 0;

    return new Promise((resolve) => {
        const pump = () => {
            if (token !== contactAvatarLoadToken || expectedAgentContacts !== agentName) {
                resolve();
                return;
            }
            while (active < maxConcurrent && index < pending.length) {
                const button = pending[index++];
                active += 1;
                loadSingleContactAvatar(button, agentName, token)
                    .catch(() => {})
                    .finally(() => {
                        active -= 1;
                        pump();
                    });
            }
            if (active === 0 && index >= pending.length) {
                resolve();
            }
        };
        pump();
    });
}

async function loadSingleContactAvatar(button, agentName, token) {
    const userId = button?.dataset?.contactUserId;
    const buttonAgentName = button?.dataset?.contactAgentName;
    if (!button || !userId || !buttonAgentName || buttonAgentName !== agentName) {
        return;
    }
    if (token !== contactAvatarLoadToken || expectedAgentContacts !== agentName) {
        return;
    }
    if (button.dataset.contactAvatarPending !== 'true') {
        return;
    }

    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/contacts/${encodeURIComponent(userId)}/avatar`);
        const data = await response.json();
        if (token !== contactAvatarLoadToken || expectedAgentContacts !== agentName) {
            return;
        }
        if (!data || data.error || !data.avatar_photo) {
            return;
        }
        button.style.position = 'relative';
        button.style.overflow = 'hidden';
        button.style.padding = '0';
        if (String(data.avatar_photo).startsWith('data:video/')) {
            button.innerHTML = `<video src="${escapeHtml(data.avatar_photo)}" aria-label="Avatar video" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; background: #f8f9fa;" autoplay muted loop playsinline></video>`;
        } else {
            button.innerHTML = `<img src="${escapeHtml(data.avatar_photo)}" alt="Avatar" style="position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; display: block; background: #f8f9fa;">`;
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
    } finally {
        if (button) {
            delete button.dataset.contactAvatarPending;
        }
    }
}

async function deleteAgentContact(agentName, userId) {
    if (!agentName || !userId) {
        alert('Please select an agent and contact');
        return;
    }
    const confirmation = confirm(`Delete contact ${userId}?`);
    if (!confirmation) {
        return;
    }
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/contacts/${encodeURIComponent(userId)}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        if (data.error) {
            alert('Error deleting contact: ' + data.error);
        } else {
            selectedAgentContacts.delete(String(userId));
            loadAgentContacts(agentName);
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting contact: ' + error);
    }
}

function toggleAgentContactSelection(userId, isChecked) {
    if (!selectedAgentContactsAgent) {
        return;
    }
    const contactId = String(userId);
    if (isChecked) {
        selectedAgentContacts.add(contactId);
    } else {
        selectedAgentContacts.delete(contactId);
    }
    updateAgentContactsSelectionUI();
}

function toggleAgentContactsSelectAll(isChecked) {
    if (!selectedAgentContactsAgent) {
        return;
    }
    if (isChecked) {
        selectedAgentContacts = new Set(currentAgentContactsUserIds);
    } else {
        selectedAgentContacts.clear();
    }
    document.querySelectorAll('input[data-contact-checkbox="true"]').forEach(input => {
        input.checked = isChecked;
    });
    updateAgentContactsSelectionUI();
}

function updateAgentContactsSelectionUI() {
    const selectedCount = selectedAgentContacts.size;
    const totalCount = currentAgentContactsUserIds.length;
    const countEl = document.getElementById('agent-contacts-selected-count');
    const deleteBtn = document.getElementById('agent-contacts-delete-selected');
    const selectAll = document.getElementById('agent-contacts-select-all');

    if (countEl) {
        countEl.textContent = `${selectedCount} selected`;
    }
    if (deleteBtn) {
        deleteBtn.disabled = selectedCount === 0;
        deleteBtn.style.opacity = selectedCount === 0 ? '0.6' : '1';
        deleteBtn.style.cursor = selectedCount === 0 ? 'not-allowed' : 'pointer';
    }
    if (selectAll) {
        selectAll.checked = selectedCount > 0 && selectedCount === totalCount;
        selectAll.indeterminate = selectedCount > 0 && selectedCount < totalCount;
    }
}

async function bulkDeleteAgentContacts(agentName) {
    if (!agentName || selectedAgentContacts.size === 0) {
        return;
    }
    const userIds = Array.from(selectedAgentContacts);
    const confirmation = confirm(`Delete ${userIds.length} selected contact(s)?`);
    if (!confirmation) {
        return;
    }
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/contacts/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_ids: userIds })
        });
        const data = await response.json();
        if (data.error) {
            alert('Error deleting contacts: ' + data.error);
        } else {
            selectedAgentContacts = new Set();
            loadAgentContacts(agentName);
        }
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting contacts: ' + error);
    }
}

async function openConversationFromContacts(agentName, userId) {
    if (!agentName || !userId) return;
    const conversationsTab = document.querySelector('nav.tab-bar:first-of-type .tab-button[data-tab="conversations"]');
    if (conversationsTab) {
        conversationsTab.click();
    }

    const conversationsSelect = document.getElementById('conversations-agent-select');
    if (conversationsSelect) {
        conversationsSelect.value = agentName;
        conversationsSelect.dispatchEvent(new Event('change'));
    }

    const userIdInput = document.getElementById('conversations-user-id');
    if (userIdInput) {
        userIdInput.value = userId;
    }

    await loadConversationData();
}

async function openContactPhotos(agentName, userId) {
    return openEntityPhotos(agentName, userId, 'contact');
}

async function openMembershipPhotos(agentName, channelId) {
    return openEntityPhotos(agentName, channelId, 'group/channel');
}

async function openEntityPhotos(agentName, userId, entityLabel) {
    if (!agentName || !userId) return;
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/partner-profile/${encodeURIComponent(userId)}`);
        const data = await response.json();
        if (data.error) {
            alert(`Error loading ${entityLabel} photos: ` + data.error);
            return;
        }
        const count = Math.max(0, parseInt(data.profile_photo_count, 10) || 0);
        if (count === 0) {
            alert(`No profile photos available for this ${entityLabel}.`);
            return;
        }
        contactFullscreenPhotoCount = count;
        contactFullscreenPhotos = new Array(count);
        if (data.profile_photo) contactFullscreenPhotos[0] = data.profile_photo;
        contactFullscreenAgentName = agentName;
        contactFullscreenUserId = userId;
        contactFullscreenPhotoIndex = 0;
        showContactPhotoFullscreen();
    } catch (error) {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert(`Error loading ${entityLabel} photos: ` + error);
    }
}

// Load memories
function loadMemories(agentName) {
    const container = document.getElementById('memories-container');
    if (!container) return;
    
    // Validate that the agentName still matches the currently selected agent
    // This prevents stale data from being loaded if the user changed agents
    // during an async operation (e.g., loadAgents() updating the select)
    const agentSelect = document.getElementById('agents-agent-select');
    const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    if (currentAgentName !== agentName) {
        // Agent selection changed, don't load stale data
        return;
    }
    
    showLoading(container, 'Loading memories...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memories`)
        .then(response => response.json())
        .then(data => {
            // Validate again that agentName still matches the currently selected agent
            // This prevents updating UI with stale data if user changed agents during the API call
            const agentSelect = document.getElementById('agents-agent-select');
            const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
            if (currentAgentName !== agentName) {
                // Agent selection changed during API call, don't update UI with stale data
                return;
            }
            
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const memories = data.memories || [];
            let html = '<div style="margin-bottom: 16px;"><button onclick="createNewMemory(\'' + escJsAttr(agentName) + '\')" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">+ Add New Memory</button></div>';
            
            if (memories.length === 0) {
                html += '<div class="placeholder-card">No memories found.</div>';
                container.innerHTML = html;
                return;
            }
            
            html += memories.map(memory => {
                const metadata = [];
                if (memory.creation_channel) {
                    metadata.push(`<strong>Channel:</strong> ${memory.creation_channel}`);
                }
                if (memory.creation_channel_id) {
                    metadata.push(`<strong>Channel ID:</strong> ${memory.creation_channel_id}`);
                }
                if (memory.origin) {
                    metadata.push(`<strong>Origin:</strong> ${memory.origin}`);
                }
                const metadataHtml = metadata.length > 0 ? '<br>' + metadata.join('<br>') : '';
                
                return `
                <div class="memory-item" style="background: white; padding: 16px; margin-bottom: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                        <div>
                            <strong>ID:</strong> ${escapeHtml(memory.id || 'N/A')}<br>
                            <strong>Created:</strong> ${escapeHtml(memory.created || 'N/A')}${metadataHtml}
                        </div>
                        <button onclick="deleteMemory('${escJsAttr(agentName)}', '${escJsAttr(memory.id)}')" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Delete</button>
                    </div>
                    <textarea 
                        id="memory-${memory.id}" 
                        style="width: 100%; min-height: 100px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical; box-sizing: border-box;"
                        oninput="scheduleMemoryAutoSave('${escJsAttr(agentName)}', '${escJsAttr(memory.id)}')"
                    >${escapeHtml(memory.content || '')}</textarea>
                    <div id="memory-status-${memory.id}" style="margin-top: 8px; font-size: 12px; color: #28a745;">Saved</div>
                </div>
            `;
            }).join('');
            container.innerHTML = html;
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            // Validate that agentName still matches the currently selected agent
            // This prevents updating UI with stale error data if user changed agents during the API call
            const agentSelect = document.getElementById('agents-agent-select');
            const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
            if (currentAgentName !== agentName) {
                // Agent selection changed during API call, don't update UI with stale error data
                return;
            }
            container.innerHTML = `<div class="error">Error loading memories: ${escapeHtml(error)}</div>`;
        });
}

function createNewMemory(agentName) {
    if (!agentName) {
        alert('Please select an agent');
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memories`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content: 'New memory entry' })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error creating memory: ' + data.error);
        } else {
            loadMemories(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error creating memory: ' + error);
    });
}

// Auto-save for memories
const memoryAutoSaveTimers = {};
function scheduleMemoryAutoSave(agentName, memoryId) {
    if (memoryAutoSaveTimers[memoryId]) {
        clearTimeout(memoryAutoSaveTimers[memoryId]);
    }
    
    const statusEl = document.getElementById(`memory-status-${memoryId}`);
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    memoryAutoSaveTimers[memoryId] = setTimeout(() => {
        const textarea = document.getElementById(`memory-${memoryId}`);
        if (!textarea) {
            return; // Element no longer exists
        }
        const content = textarea.value.trim();
        
        const statusEl = document.getElementById(`memory-status-${memoryId}`);
        if (statusEl) {
            statusEl.textContent = 'Saving...';
            statusEl.style.color = '#007bff';
        }
        
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memories/${memoryId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ content: content })
        })
        .then(response => response.json())
        .then(data => {
            const statusEl = document.getElementById(`memory-status-${memoryId}`);
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
            const statusEl = document.getElementById(`memory-status-${memoryId}`);
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        });
    }, 1000);
}

function deleteMemory(agentName, memoryId) {
    if (!confirm('Are you sure you want to delete this memory?')) {
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memories/${memoryId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error deleting memory: ' + data.error);
        } else {
            loadMemories(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting memory: ' + error);
    });
}


// Auto-save for notes
const noteAutoSaveTimers = {};
function scheduleNoteAutoSave(agentName, userId, noteId) {
    const key = `${agentName}-${userId}-${noteId}`;
    if (noteAutoSaveTimers[key]) {
        clearTimeout(noteAutoSaveTimers[key]);
    }
    
    const statusEl = document.getElementById(`note-status-${userId}-${noteId}`);
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    noteAutoSaveTimers[key] = setTimeout(() => {
        // Try both possible textarea ID formats (Agents tab and Conversations tab)
        let textarea = document.getElementById(`note-${userId}-${noteId}`);
        if (!textarea) {
            textarea = document.getElementById(`note-params-${userId}-${noteId}`);
        }
        if (!textarea) {
            return; // Element no longer exists
        }
        const content = textarea.value.trim();
        
        const statusEl = document.getElementById(`note-status-${userId}-${noteId}`);
        if (statusEl) {
            statusEl.textContent = 'Saving...';
            statusEl.style.color = '#007bff';
        }
        
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/notes/${userId}/${noteId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ content: content })
        })
        .then(response => response.json())
        .then(data => {
            const statusEl = document.getElementById(`note-status-${userId}-${noteId}`);
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
            const statusEl = document.getElementById(`note-status-${userId}-${noteId}`);
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        });
    }, 1000);
}

function deleteNote(agentName, userId, noteId) {
    if (!confirm('Are you sure you want to delete this note?')) {
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/notes/${userId}/${noteId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error deleting note: ' + data.error);
        } else {
            loadNotesForPartner();
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting note: ' + error);
    });
}

// Load intentions (similar to memories)
function loadIntentions(agentName) {
    const container = document.getElementById('intentions-container');
    showLoading(container, 'Loading intentions...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/intentions`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const intentions = data.intentions || [];
            let html = '<div style="margin-bottom: 16px;"><button onclick="createNewIntention(\'' + escJsAttr(agentName) + '\')" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; font-weight: bold;">+ Add New Intention</button></div>';
            
            if (intentions.length === 0) {
                html += '<div class="placeholder-card">No intentions found.</div>';
                container.innerHTML = html;
                return;
            }
            
            html += intentions.map(intention => `
                <div class="memory-item" style="background: white; padding: 16px; margin-bottom: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 8px;">
                        <div>
                            <strong>ID:</strong> ${escapeHtml(intention.id || 'N/A')}<br>
                            <strong>Created:</strong> ${escapeHtml(intention.created || 'N/A')}
                        </div>
                        <button onclick="deleteIntention('${escJsAttr(agentName)}', '${escJsAttr(intention.id)}')" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Delete</button>
                    </div>
                    <textarea 
                        id="intention-${intention.id}" 
                        style="width: 100%; min-height: 100px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; resize: vertical; box-sizing: border-box;"
                        oninput="scheduleIntentionAutoSave('${escJsAttr(agentName)}', '${escJsAttr(intention.id)}')"
                    >${escapeHtml(intention.content || '')}</textarea>
                    <div id="intention-status-${intention.id}" style="margin-top: 8px; font-size: 12px; color: #28a745;">Saved</div>
                </div>
            `).join('');
            container.innerHTML = html;
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading intentions: ${escapeHtml(error)}</div>`;
        });
}

function createNewIntention(agentName) {
    if (!agentName) {
        alert('Please select an agent');
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/intentions`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content: 'New intention entry' })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error creating intention: ' + data.error);
        } else {
            loadIntentions(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error creating intention: ' + error);
    });
}

// Auto-save for intentions
const intentionAutoSaveTimers = {};
function scheduleIntentionAutoSave(agentName, intentionId) {
    if (intentionAutoSaveTimers[intentionId]) {
        clearTimeout(intentionAutoSaveTimers[intentionId]);
    }
    
    const statusEl = document.getElementById(`intention-status-${intentionId}`);
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    intentionAutoSaveTimers[intentionId] = setTimeout(() => {
        const textarea = document.getElementById(`intention-${intentionId}`);
        const content = textarea.value.trim();
        
        if (statusEl) {
            statusEl.textContent = 'Saving...';
            statusEl.style.color = '#007bff';
        }
        
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/intentions/${intentionId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ content: content })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                if (statusEl) {
                    statusEl.textContent = 'Error';
                    statusEl.style.color = '#dc3545';
                }
            } else {
                if (statusEl) {
                    statusEl.textContent = 'Saved';
                    statusEl.style.color = '#28a745';
                }
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        });
    }, 1000);
}

function deleteIntention(agentName, intentionId) {
    if (!confirm('Are you sure you want to delete this intention?')) {
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/intentions/${intentionId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error deleting intention: ' + data.error);
        } else {
            loadIntentions(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting intention: ' + error);
    });
}

async function openMembershipConversationProfile(agentName, channelId) {
    if (!agentName || !channelId) {
        return;
    }

    const conversationsTabButton = document.querySelector('nav.tab-bar:first-of-type .tab-button[data-tab="conversations"]');
    if (conversationsTabButton) {
        conversationsTabButton.click();
    }

    await loadAgents();

    const conversationsAgentSelect = document.getElementById('conversations-agent-select');
    if (conversationsAgentSelect) {
        conversationsAgentSelect.value = agentName;
        conversationsAgentSelect.dispatchEvent(new Event('change', { bubbles: true }));
    }

    const partnerSelect = document.getElementById('conversations-partner-select');
    if (partnerSelect) {
        partnerSelect.value = '';
    }

    const userIdInput = document.getElementById('conversations-user-id');
    if (userIdInput) {
        userIdInput.value = String(channelId);
    }

    switchSubtab('profile-conv');
    loadConversationData();
}

// Load memberships
function loadMemberships(agentName) {
    const container = document.getElementById('memberships-container');
    if (!container) return;
    
    // Validate that the agentName still matches the currently selected agent
    const agentSelect = document.getElementById('agents-agent-select');
    const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    if (currentAgentName !== agentName) {
        return;
    }
    
    if (!agentName) {
        showLoading(container, 'Select an agent to manage memberships');
        return;
    }
    
    showLoading(container, 'Loading memberships...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memberships`)
        .then(response => response.json())
        .then(data => {
            // Re-validate agent name after async operation
            const agentSelect = document.getElementById('agents-agent-select');
            const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
            if (currentAgentName !== agentName) {
                return;
            }
            
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const memberships = data.memberships || [];
            
            let html = '<div style="margin-bottom: 20px;">';
            html += '<div style="display: flex; gap: 8px; align-items: center; margin-bottom: 16px;">';
            html += '<input type="text" id="membership-identifier" placeholder="Group username, ID, or invitation link" style="flex: 1; padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" onkeypress="if(event.key===\'Enter\') subscribeToGroup(\'' + escJsAttr(agentName) + '\')">';
            html += '<button id="membership-subscribe-btn" onclick="subscribeToGroup(\'' + escJsAttr(agentName) + '\')" style="padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Subscribe</button>';
            html += '</div>';
            html += '</div>';
            
            if (memberships.length === 0) {
                html += '<div style="padding: 20px; text-align: center; color: #666;">No group memberships found</div>';
            } else {
                html += '<div style="display: grid; gap: 12px;">';
                memberships.forEach(membership => {
                    const name = membership.name || 'Unknown';
                    const channelId = membership.channel_id;
                    const username = membership.username ? `@${membership.username}` : '';
                    const isMuted = membership.is_muted || false;
                    const isGagged = membership.is_gagged || false;
                    const escapedAgentName = escJsAttr(agentName);
                    const escapedChannelId = escJsAttr(channelId);
                    const initialLabel = escapeHtml(String(name || channelId || '?').trim().charAt(0).toUpperCase() || '?');
                    let avatarHtml = '';
                    if (membership.profile_photo) {
                        avatarHtml =
                            '<button onclick="openMembershipPhotos(\'' + escapedAgentName + '\', \'' + escapedChannelId + '\'); return false;" title="View profile photos" ' +
                            'style="width: 34px; height: 34px; border-radius: 999px; border: 1px solid #ced4da; padding: 0; cursor: pointer; background: #f8f9fa; display: inline-flex; align-items: center; justify-content: center; overflow: hidden; flex: 0 0 34px;">' +
                            '<img src="' + escapeHtml(membership.profile_photo) + '" alt="Avatar" style="width: 100%; height: 100%; object-fit: contain; background: #f8f9fa;">' +
                            '</button>';
                    } else {
                        avatarHtml =
                            '<button onclick="openMembershipPhotos(\'' + escapedAgentName + '\', \'' + escapedChannelId + '\'); return false;" title="View profile photos" ' +
                            'style="width: 34px; height: 34px; border-radius: 999px; border: 1px solid #ced4da; cursor: pointer; background: #eef2f7; color: #2c3e50; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 34px; font-weight: 700; font-size: 15px; line-height: 1;">' +
                            initialLabel +
                            '</button>';
                    }
                    
                    html += '<div style="padding: 16px; background: #ffffff; border: 1px solid #e0e0e0; border-radius: 4px; display: flex; align-items: center; gap: 12px;">';
                    html += avatarHtml;
                    html += '<div style="flex: 1; min-width: 0;">';
                    const nameLabel = escapeHtml(name);
                    let nameHtml = nameLabel;
                    if (channelId) {
                        nameHtml = '<a href="#" onclick="openMembershipConversationProfile(\'' + escapedAgentName + '\', \'' + escapedChannelId + '\'); return false;" style="color: #007bff; text-decoration: underline;">' + nameLabel + '</a>';
                    }
                    html += '<div style="font-weight: 500; margin-bottom: 4px;">' + nameHtml + '</div>';
                    html += '<div style="font-size: 12px; color: #666;">';
                    html += 'ID: ' + escapeHtml(channelId);
                    if (username) {
                        html += ' • ' + escapeHtml(username);
                    }
                    html += '</div>';
                    html += '</div>';
                    html += '<div style="display: flex; align-items: center; gap: 8px;">';
                    html += '<label style="display: flex; align-items: center; gap: 4px; cursor: pointer;">';
                    html += '<input type="checkbox" ' + (isGagged ? 'checked' : '') + ' onchange="toggleGaggedMembership(\'' + escapedAgentName + '\', \'' + escapedChannelId + '\', this.checked)" style="cursor: pointer;">';
                    html += '<span style="font-size: 14px;">Gagged</span>';
                    html += '</label>';
                    html += '<label style="display: flex; align-items: center; gap: 4px; cursor: pointer;">';
                    html += '<input type="checkbox" ' + (isMuted ? 'checked' : '') + ' onchange="toggleMuteMembership(\'' + escapedAgentName + '\', \'' + escapedChannelId + '\', this.checked)" style="cursor: pointer;">';
                    html += '<span style="font-size: 14px;">Muted</span>';
                    html += '</label>';
                    html += '<button onclick="deleteMembership(\'' + escapedAgentName + '\', \'' + escapedChannelId + '\', \'' + escJsAttr(name) + '\')" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Delete</button>';
                    html += '</div>';
                    html += '</div>';
                });
                html += '</div>';
            }
            
            container.innerHTML = html;
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading memberships: ${escapeHtml(error)}</div>`;
        });
}

function subscribeToGroup(agentName) {
    const identifierInput = document.getElementById('membership-identifier');
    if (!identifierInput) return;
    
    const identifier = identifierInput.value.trim();
    if (!identifier) {
        alert('Please enter a group username, ID, or invitation link');
        return;
    }
    
    const button = document.getElementById('membership-subscribe-btn');
    if (button) {
        button.disabled = true;
        button.textContent = 'Subscribing...';
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memberships/subscribe`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ identifier: identifier })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error subscribing: ' + data.error);
        } else {
            // Clear input
            identifierInput.value = '';
            // Show warning if present (e.g., join succeeded but mute failed)
            if (data.warning) {
                alert('Warning: ' + data.warning);
            }
            // Reload memberships
            loadMemberships(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error subscribing: ' + error);
    })
    .finally(() => {
        if (button) {
            button.disabled = false;
            button.textContent = 'Subscribe';
        }
    });
}

function toggleGaggedMembership(agentName, channelId, isGagged) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memberships/${encodeURIComponent(channelId)}/gagged`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ is_gagged: isGagged })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error toggling gagged: ' + data.error);
            // Reload to restore previous state
            loadMemberships(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error toggling gagged: ' + error);
        // Reload to restore previous state
        loadMemberships(agentName);
    });
}

function toggleMuteMembership(agentName, channelId, isMuted) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memberships/${encodeURIComponent(channelId)}/mute`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ is_muted: isMuted })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error toggling mute: ' + data.error);
            // Reload to restore correct state
            loadMemberships(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error toggling mute: ' + error);
        // Reload to restore correct state
        loadMemberships(agentName);
    });
}

function deleteMembership(agentName, channelId, name) {
    const displayName = name || channelId;
    const escapedDisplayName = escJsTemplate(displayName);
    if (!confirm(`Are you sure you want to delete the subscription to "${escapedDisplayName}"?`)) {
        return;
    }
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/memberships/${encodeURIComponent(channelId)}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error deleting subscription: ' + data.error);
        } else {
            loadMemberships(agentName);
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error deleting subscription: ' + error);
    });
}

// Load agent configuration
// Profile management variables
let currentAgentProfile = null;
let originalAgentProfile = null;
let bioLimit = 70;
let expectedProfileAgent = null; // Track which agent we're expecting a profile response for
let currentConversationProfile = null;
let originalConversationProfile = null;
let expectedConversationProfile = null; // Track current conversation profile request
let agentProfilePhotos = [];
let agentProfilePhotoCount = 0;
let agentProfilePhotoIndex = 0;
let agentProfilePhotoAgentName = null;
let conversationProfilePhotos = [];
let conversationProfilePhotoCount = 0;
let conversationProfilePhotoIndex = 0;
let conversationProfileAgentName = null;
let conversationProfileUserId = null;
let contactFullscreenPhotos = [];
let contactFullscreenPhotoCount = 0;
let contactFullscreenPhotoIndex = 0;
let contactFullscreenAgentName = null;
let contactFullscreenUserId = null;
let selectedAgentContacts = new Set();
let selectedAgentContactsAgent = null;
let currentAgentContactsUserIds = [];
let expectedAgentContacts = null; // Track current agent contacts request
let contactAvatarLoadToken = 0;

// Profile photo fullscreen functions
function prefetchAgentProfilePhotos(currentIndex) {
    const count = agentProfilePhotoCount;
    if (count <= 0) return;
    const indices = [
        (currentIndex + 1) % count,
        (currentIndex + 2) % count,
        (currentIndex - 1 + count) % count
    ];
    indices.forEach((i) => ensureAgentProfilePhotoLoaded(i));
}

async function ensureAgentProfilePhotoLoaded(index) {
    if (agentProfilePhotoCount <= 0 || index < 0 || index >= agentProfilePhotoCount) return;
    if (agentProfilePhotos[index]) return;
    const agentName = agentProfilePhotoAgentName;
    if (!agentName) return;
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/profile/photo/${index}`);
        const data = await response.json();
        if (data.data_url) {
            agentProfilePhotos[index] = data.data_url;
            updateAgentProfilePhotoDisplay();
            prefetchAgentProfilePhotos(index);
        }
    } catch (e) {
        if (e && e.message === 'unauthorized') return;
        console.debug('Failed to load agent profile photo at index', index, e);
    }
}

function updateAgentProfilePhotoDisplay() {
    const photoImg = document.getElementById('agent-profile-photo');
    const profileVideo = document.getElementById('agent-profile-video');
    const fullImg = document.getElementById('profile-photo-fullscreen');
    const fullVideo = document.getElementById('profile-video-fullscreen');
    const indexLabel = document.getElementById('agent-profile-photo-index');
    const prevBtn = document.getElementById('agent-profile-photo-prev');
    const nextBtn = document.getElementById('agent-profile-photo-next');
    const fullPrevBtn = document.getElementById('profile-photo-fullscreen-prev');
    const fullNextBtn = document.getElementById('profile-photo-fullscreen-next');
    const fullMetaIndex = document.getElementById('profile-photo-meta-index');
    const fullMetaType = document.getElementById('profile-photo-meta-type');
    const count = agentProfilePhotoCount;
    const photos = agentProfilePhotos;
    const hasPhotos = count > 0;
    const hasMultiple = count > 1;

    if (!hasPhotos) {
        if (photoImg) {
            photoImg.src = '';
            photoImg.style.display = 'none';
        }
        if (profileVideo) {
            profileVideo.pause();
            profileVideo.src = '';
            profileVideo.style.display = 'none';
        }
        if (fullVideo) {
            fullVideo.pause();
            fullVideo.src = '';
            fullVideo.style.display = 'none';
        }
        if (indexLabel) {
            indexLabel.textContent = '';
        }
        if (fullMetaIndex) {
            fullMetaIndex.innerHTML = '<strong>Item:</strong> 0 of 0';
        }
        if (fullMetaType) {
            fullMetaType.innerHTML = '<strong>Type:</strong> unknown';
        }
        [prevBtn, nextBtn, fullPrevBtn, fullNextBtn].forEach(btn => {
            if (btn) btn.style.display = 'none';
        });
        return;
    }

    const safeIndex = Math.min(Math.max(agentProfilePhotoIndex, 0), count - 1);
    agentProfilePhotoIndex = safeIndex;
    const src = photos[safeIndex];
    if (!src) {
        ensureAgentProfilePhotoLoaded(safeIndex);
        if (indexLabel) indexLabel.textContent = `${safeIndex + 1} of ${count}`;
        if (fullMetaIndex) fullMetaIndex.innerHTML = `<strong>Item:</strong> ${safeIndex + 1} of ${count}`;
        [prevBtn, nextBtn, fullPrevBtn, fullNextBtn].forEach(btn => {
            if (btn) btn.style.display = hasMultiple ? 'inline-block' : 'none';
        });
        prefetchAgentProfilePhotos(safeIndex);
        return;
    }
    const isVideo = String(src || '').startsWith('data:video/');
    if (isVideo) {
        if (photoImg) {
            photoImg.src = '';
            photoImg.style.display = 'none';
        }
        if (fullImg) {
            fullImg.src = '';
            fullImg.style.display = 'none';
        }
        if (profileVideo) {
            profileVideo.src = src;
            profileVideo.style.display = 'block';
            profileVideo.play().catch(() => {});
        }
        if (fullVideo) {
            fullVideo.src = src;
            fullVideo.style.display = 'block';
            fullVideo.play().catch(() => {});
        }
    } else {
        if (profileVideo) {
            profileVideo.pause();
            profileVideo.src = '';
            profileVideo.style.display = 'none';
        }
        if (fullVideo) {
            fullVideo.pause();
            fullVideo.src = '';
            fullVideo.style.display = 'none';
        }
        if (photoImg) {
            photoImg.src = src;
            photoImg.style.display = 'block';
        }
        if (fullImg) {
            fullImg.src = src;
            fullImg.style.display = 'block';
        }
    }
    if (indexLabel) {
        indexLabel.textContent = `${safeIndex + 1} of ${count}`;
    }
    if (fullMetaIndex) {
        fullMetaIndex.innerHTML = `<strong>Item:</strong> ${safeIndex + 1} of ${count}`;
    }
    if (fullMetaType) {
        fullMetaType.innerHTML = `<strong>Type:</strong> ${isVideo ? 'video' : 'image'}`;
    }
    [prevBtn, nextBtn, fullPrevBtn, fullNextBtn].forEach(btn => {
        if (btn) btn.style.display = hasMultiple ? 'inline-block' : 'none';
    });
    prefetchAgentProfilePhotos(safeIndex);
}

function showProfilePhotoFullscreen() {
    const modal = document.getElementById('profile-photo-modal');
    if (!modal || agentProfilePhotoCount === 0) {
        return;
    }
    updateAgentProfilePhotoDisplay();
    modal.style.display = 'block';
}

function closeProfilePhotoFullscreen() {
    const modal = document.getElementById('profile-photo-modal');
    const fullVideo = document.getElementById('profile-video-fullscreen');
    if (modal) {
        modal.style.display = 'none';
    }
    if (fullVideo) {
        fullVideo.pause();
    }
}

function showPreviousAgentProfilePhoto(event, includeFullscreen = false) {
    if (event) event.stopPropagation();
    if (agentProfilePhotoCount <= 1) return;
    agentProfilePhotoIndex = (agentProfilePhotoIndex - 1 + agentProfilePhotoCount) % agentProfilePhotoCount;
    updateAgentProfilePhotoDisplay();
    if (includeFullscreen) {
        showProfilePhotoFullscreen();
    }
}

function showNextAgentProfilePhoto(event, includeFullscreen = false) {
    if (event) event.stopPropagation();
    if (agentProfilePhotoCount <= 1) return;
    agentProfilePhotoIndex = (agentProfilePhotoIndex + 1) % agentProfilePhotoCount;
    updateAgentProfilePhotoDisplay();
    if (includeFullscreen) {
        showProfilePhotoFullscreen();
    }
}

function prefetchContactFullscreenPhotos(currentIndex) {
    const count = contactFullscreenPhotoCount;
    if (count <= 0) return;
    const indices = [
        (currentIndex + 1) % count,
        (currentIndex + 2) % count,
        (currentIndex - 1 + count) % count
    ];
    indices.forEach((i) => ensureContactFullscreenPhotoLoaded(i));
}

async function ensureContactFullscreenPhotoLoaded(index) {
    if (contactFullscreenPhotoCount <= 0 || index < 0 || index >= contactFullscreenPhotoCount) return;
    if (contactFullscreenPhotos[index]) return;
    const agentName = contactFullscreenAgentName;
    const userId = contactFullscreenUserId;
    if (!agentName || !userId) return;
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/partner-profile/${encodeURIComponent(userId)}/photo/${index}`);
        const data = await response.json();
        if (data.data_url) {
            contactFullscreenPhotos[index] = data.data_url;
            updateContactFullscreenPhotoDisplay();
            prefetchContactFullscreenPhotos(index);
        }
    } catch (e) {
        if (e && e.message === 'unauthorized') return;
        console.debug('Failed to load contact fullscreen photo at index', index, e);
    }
}

function updateContactFullscreenPhotoDisplay() {
    const fullImg = document.getElementById('contacts-photo-fullscreen');
    const fullVideo = document.getElementById('contacts-video-fullscreen');
    const fullMetaIndex = document.getElementById('contacts-photo-meta-index');
    const fullMetaType = document.getElementById('contacts-photo-meta-type');
    const prevBtn = document.getElementById('contacts-photo-fullscreen-prev');
    const nextBtn = document.getElementById('contacts-photo-fullscreen-next');
    const count = contactFullscreenPhotoCount;
    const photos = contactFullscreenPhotos;
    const hasPhotos = count > 0;
    const hasMultiple = count > 1;

    if (!fullImg || !fullVideo) {
        return;
    }

    if (!hasPhotos) {
        fullImg.src = '';
        fullImg.style.display = 'none';
        fullVideo.pause();
        fullVideo.src = '';
        fullVideo.style.display = 'none';
        if (fullMetaIndex) {
            fullMetaIndex.innerHTML = '<strong>Item:</strong> 0 of 0';
        }
        if (fullMetaType) {
            fullMetaType.innerHTML = '<strong>Type:</strong> unknown';
        }
        [prevBtn, nextBtn].forEach(btn => {
            if (btn) btn.style.display = 'none';
        });
        return;
    }

    const safeIndex = Math.min(Math.max(contactFullscreenPhotoIndex, 0), count - 1);
    contactFullscreenPhotoIndex = safeIndex;
    const src = photos[safeIndex];
    if (!src) {
        ensureContactFullscreenPhotoLoaded(safeIndex);
        if (fullMetaIndex) fullMetaIndex.innerHTML = `<strong>Item:</strong> ${safeIndex + 1} of ${count}`;
        [prevBtn, nextBtn].forEach(btn => {
            if (btn) btn.style.display = hasMultiple ? 'inline-block' : 'none';
        });
        prefetchContactFullscreenPhotos(safeIndex);
        return;
    }
    const isVideo = String(src || '').startsWith('data:video/');
    if (isVideo) {
        fullImg.src = '';
        fullImg.style.display = 'none';
        fullVideo.src = src;
        fullVideo.style.display = 'block';
        fullVideo.play().catch(() => {});
    } else {
        fullVideo.pause();
        fullVideo.src = '';
        fullVideo.style.display = 'none';
        fullImg.src = src;
        fullImg.style.display = 'block';
    }
    if (fullMetaIndex) {
        fullMetaIndex.innerHTML = `<strong>Item:</strong> ${safeIndex + 1} of ${count}`;
    }
    if (fullMetaType) {
        fullMetaType.innerHTML = `<strong>Type:</strong> ${isVideo ? 'video' : 'image'}`;
    }
    [prevBtn, nextBtn].forEach(btn => {
        if (btn) btn.style.display = hasMultiple ? 'inline-block' : 'none';
    });
    prefetchContactFullscreenPhotos(safeIndex);
}

function showContactPhotoFullscreen(photoList) {
    const modal = document.getElementById('contacts-photo-modal');
    if (!modal) return;
    // The modal is declared under the Contacts subtab in the template.
    // Re-parent it to <body> so it can open from Memberships too.
    if (modal.parentElement !== document.body) {
        document.body.appendChild(modal);
    }
    if (photoList !== undefined) {
        contactFullscreenPhotos = Array.isArray(photoList) ? photoList.filter(Boolean) : [];
        contactFullscreenPhotoCount = contactFullscreenPhotos.length;
        contactFullscreenAgentName = null;
        contactFullscreenUserId = null;
    }
    contactFullscreenPhotoIndex = 0;
    if (contactFullscreenPhotoCount === 0) {
        return;
    }
    updateContactFullscreenPhotoDisplay();
    prefetchContactFullscreenPhotos(0);
    modal.style.display = 'block';
}

function closeContactPhotoFullscreen() {
    const modal = document.getElementById('contacts-photo-modal');
    const fullVideo = document.getElementById('contacts-video-fullscreen');
    if (modal) {
        modal.style.display = 'none';
    }
    if (fullVideo) {
        fullVideo.pause();
    }
}

function showPreviousContactPhoto(event) {
    if (event) event.stopPropagation();
    if (contactFullscreenPhotoCount <= 1) return;
    contactFullscreenPhotoIndex = (contactFullscreenPhotoIndex - 1 + contactFullscreenPhotoCount) % contactFullscreenPhotoCount;
    updateContactFullscreenPhotoDisplay();
}

function showNextContactPhoto(event) {
    if (event) event.stopPropagation();
    if (contactFullscreenPhotoCount <= 1) return;
    contactFullscreenPhotoIndex = (contactFullscreenPhotoIndex + 1) % contactFullscreenPhotoCount;
    updateContactFullscreenPhotoDisplay();
}

// Escape key and arrows for photo viewers
document.addEventListener('keydown', (e) => {
    const profileModalOpen = document.getElementById('profile-photo-modal')?.style.display === 'block';
    const conversationModalOpen = document.getElementById('conversation-profile-photo-modal')?.style.display === 'block';
    const contactsModalOpen = document.getElementById('contacts-photo-modal')?.style.display === 'block';

    if (e.key === 'Escape') {
        closeProfilePhotoFullscreen();
        closeConversationProfilePhotoFullscreen();
        closeContactPhotoFullscreen();
        return;
    }
    if (e.key === 'ArrowLeft') {
        if (profileModalOpen) showPreviousAgentProfilePhoto(null, true);
        if (conversationModalOpen) showPreviousConversationProfilePhoto(null, true);
        if (contactsModalOpen) showPreviousContactPhoto();
        return;
    }
    if (e.key === 'ArrowRight') {
        if (profileModalOpen) showNextAgentProfilePhoto(null, true);
        if (conversationModalOpen) showNextConversationProfilePhoto(null, true);
        if (contactsModalOpen) showNextContactPhoto();
    }
});

// Update birthday day options based on selected month
function updateBirthdayDays() {
    const monthSelect = document.getElementById('agent-profile-birthday-month');
    const daySelect = document.getElementById('agent-profile-birthday-day');
    const month = monthSelect.value ? parseInt(monthSelect.value) : null;
    
    // Preserve current day selection if it exists
    const currentDay = daySelect.value ? parseInt(daySelect.value) : null;
    
    // Clear existing options
    daySelect.innerHTML = '<option value="">Day</option>';
    
    // If month is cleared, also clear day selection and show cancel button
    if (!month) {
        daySelect.value = '';
        // Enable cancel button since we made a change
        document.getElementById('agent-profile-cancel-wrap').style.display = 'inline-block';
        return;
    }
    
    // Days in each month (using 29 for February to handle leap years)
    const daysInMonth = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    const days = daysInMonth[month - 1];
    
    for (let i = 1; i <= days; i++) {
        const option = document.createElement('option');
        option.value = i;
        option.textContent = i;
        daySelect.appendChild(option);
    }
    
    // Restore day selection if it's valid for the new month
    // If the previous day is too high for the new month (e.g., 31 -> February),
    // clamp it to the maximum valid day
    if (currentDay !== null) {
        if (currentDay <= days) {
            daySelect.value = currentDay;
        } else {
            // Day is too high for new month, clamp to max valid day
            daySelect.value = days;
        }
    }
    
    // Enable cancel button since we made a change
    document.getElementById('agent-profile-cancel-wrap').style.display = 'inline-block';
}

// Validate bio character count
function validateBio() {
    const bioTextarea = document.getElementById('agent-profile-bio');
    const statusDiv = document.getElementById('agent-profile-bio-status');
    const saveBtn = document.getElementById('agent-profile-save-btn');
    const bio = bioTextarea.value;
    const currentLength = bio.length;
    
    if (currentLength > bioLimit) {
        statusDiv.textContent = `Bio exceeds limit by ${currentLength - bioLimit} characters (max ${bioLimit})`;
        statusDiv.style.color = '#dc3545';
        bioTextarea.style.borderColor = '#dc3545';
        saveBtn.disabled = true;
    } else {
        const remaining = bioLimit - currentLength;
        statusDiv.textContent = `${remaining} characters remaining (max ${bioLimit})`;
        statusDiv.style.color = remaining < 20 ? '#ffc107' : '#28a745';
        bioTextarea.style.borderColor = '#28a745';
        saveBtn.disabled = false;
    }
}

function setAgentProfileNeedsSave(needsSave) {
    const saveBtn = document.getElementById('agent-profile-save-btn');
    if (!saveBtn) return;
    if (needsSave) {
        saveBtn.style.background = '#ffc107';
        saveBtn.style.color = '#2c3e50';
        saveBtn.style.boxShadow = '0 0 0 2px rgba(255, 193, 7, 0.4)';
    } else {
        saveBtn.style.background = '#007bff';
        saveBtn.style.color = 'white';
        saveBtn.style.boxShadow = 'none';
    }
}

// Load agent profile
async function loadAgentProfile(agentName) {
    const profileSection = document.getElementById('agent-profile-section');
    const profileContainer = document.getElementById('profile-container');
    
    if (!agentName) {
        if (profileSection) {
            profileSection.style.display = 'none';
        }
        if (profileContainer) {
            profileContainer.style.display = 'block';
        }
        expectedProfileAgent = null;
        agentProfilePhotos = [];
        agentProfilePhotoCount = 0;
        agentProfilePhotoIndex = 0;
        updateAgentProfilePhotoDisplay();
        return;
    }

    // Show profile section (this function is only called when profile subtab is active)
    if (profileSection) {
        profileSection.style.display = 'block';
    }
    // Hide placeholder container when loading profile
    if (profileContainer) {
        profileContainer.style.display = 'none';
    }
    
    // Track which agent we're loading for
    expectedProfileAgent = agentName;
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/profile`);
        const data = await response.json();
        
        if (data.error) {
            // Only handle error if this request is still relevant
            if (expectedProfileAgent !== agentName) {
                return;
            }
            if (response.status === 400 && data.error.includes('not authenticated')) {
                // Agent not authenticated - hide profile section or show message
                if (profileSection) {
                    profileSection.style.display = 'none';
                }
                if (profileContainer) {
                    profileContainer.style.display = 'block';
                }
                expectedProfileAgent = null;
                agentProfilePhotos = [];
                agentProfilePhotoCount = 0;
                agentProfilePhotoIndex = 0;
                updateAgentProfilePhotoDisplay();
                return;
            }
            console.error('Error loading profile:', data.error);
            if (profileSection) {
                profileSection.style.display = 'none';
            }
            if (profileContainer) {
                profileContainer.style.display = 'block';
            }
            expectedProfileAgent = null;
            agentProfilePhotos = [];
            agentProfilePhotoCount = 0;
            agentProfilePhotoIndex = 0;
            updateAgentProfilePhotoDisplay();
            return;
        }
        
        // Verify this response is still relevant (user may have switched agents)
        // Check both expectedProfileAgent and the currently selected agent
        if (expectedProfileAgent !== agentName) {
            // This response is for a different agent, ignore it
            return;
        }
        const agentSelect = document.getElementById('agents-agent-select');
        const currentSelectedAgent = agentSelect ? stripAsterisk(agentSelect.value) : null;
        if (currentSelectedAgent !== agentName) {
            // Agent changed while loading, ignore this response
            return;
        }
        
        // Store original and current profile
        originalAgentProfile = JSON.parse(JSON.stringify(data));
        currentAgentProfile = data;
        bioLimit = data.bio_limit || 70;
        
        // Populate form fields
        document.getElementById('agent-profile-first-name').value = data.first_name || '';
        document.getElementById('agent-profile-last-name').value = data.last_name || '';
        document.getElementById('agent-profile-username').value = data.username || '';
        document.getElementById('agent-profile-telegram-id').value = data.telegram_id || '';
        const agentBioTextarea = document.getElementById('agent-profile-bio');
        resetTextareaHeight(agentBioTextarea);
        agentBioTextarea.value = data.bio || '';
        autoGrowTextarea(agentBioTextarea);
        
        // Set profile photo list
        agentProfilePhotoCount = Math.max(0, parseInt(data.profile_photo_count, 10) || 0);
        agentProfilePhotos = new Array(agentProfilePhotoCount);
        if (agentProfilePhotoCount > 0 && data.profile_photo) {
            agentProfilePhotos[0] = data.profile_photo;
        }
        agentProfilePhotoAgentName = agentName;
        agentProfilePhotoIndex = 0;
        updateAgentProfilePhotoDisplay();
        prefetchAgentProfilePhotos(0);
        
        // Set birthday
        const monthSelect = document.getElementById('agent-profile-birthday-month');
        const daySelect = document.getElementById('agent-profile-birthday-day');
        const yearInput = document.getElementById('agent-profile-birthday-year');
        
        monthSelect.value = '';
        daySelect.innerHTML = '<option value="">Day</option>';
        yearInput.value = '';
        
        if (data.birthday) {
            monthSelect.value = data.birthday.month || '';
            updateBirthdayDays();
            if (data.birthday.day) {
                daySelect.value = data.birthday.day;
            }
            if (data.birthday.year) {
                yearInput.value = data.birthday.year;
            }
        }
        
        // Validate bio
        validateBio();
        
        // Hide cancel button
        document.getElementById('agent-profile-cancel-wrap').style.display = 'none';
        document.getElementById('agent-profile-save-status').textContent = '';
        setAgentProfileNeedsSave(false);
        
    } catch (error) {
        // Only log error if this request is still relevant
        if (expectedProfileAgent === agentName) {
            console.error('Error loading agent profile:', error);
            // Restore UI state on network/parsing errors
            if (profileSection) {
                profileSection.style.display = 'none';
            }
            if (profileContainer) {
                profileContainer.style.display = 'block';
            }
            expectedProfileAgent = null;
            agentProfilePhotos = [];
            agentProfilePhotoCount = 0;
            agentProfilePhotoIndex = 0;
            updateAgentProfilePhotoDisplay();
        }
    }
}

// Save agent profile
async function saveAgentProfile() {
    const agentSelect = document.getElementById('agents-agent-select');
    const agentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    
    if (!agentName) {
        alert('No agent selected');
        return;
    }
    
    // Verify the profile data matches the selected agent
    // This prevents saving stale data from a previously selected agent
    if (expectedProfileAgent !== null && expectedProfileAgent !== agentName) {
        alert('Agent selection changed. Please reload the profile and try again.');
        return;
    }
    
    const saveBtn = document.getElementById('agent-profile-save-btn');
    const statusDiv = document.getElementById('agent-profile-save-status');
    const cancelBtn = document.getElementById('agent-profile-cancel-btn');
    
    saveBtn.disabled = true;
    statusDiv.textContent = 'Saving...';
    statusDiv.style.color = '#007bff';
    
    try {
        // Collect form data
        const first_name = document.getElementById('agent-profile-first-name').value.trim();
        const last_name = document.getElementById('agent-profile-last-name').value.trim();
        const username = document.getElementById('agent-profile-username').value.trim().replace(/^@/, '');
        const bio = document.getElementById('agent-profile-bio').value;
        
        // Collect birthday
        const monthSelect = document.getElementById('agent-profile-birthday-month');
        const daySelect = document.getElementById('agent-profile-birthday-day');
        const yearInput = document.getElementById('agent-profile-birthday-year');
        
        const monthValue = monthSelect.value.trim();
        const dayValue = daySelect.value.trim();
        const yearValue = yearInput.value.trim();
        
        // Validate birthday: if month is selected, day is required
        if (monthValue && !dayValue) {
            statusDiv.textContent = 'Error: Please select a day when a month is selected';
            statusDiv.style.color = '#dc3545';
            saveBtn.disabled = false;
            // Highlight the day field
            daySelect.style.borderColor = '#dc3545';
            // Clear highlight after 3 seconds
            setTimeout(() => {
                daySelect.style.borderColor = '#ddd';
            }, 3000);
            return;
        }
        
        // If month is empty, birthday should be null (removed)
        // Otherwise, both month and day are required (validated above)
        let birthday = null;
        if (monthValue && dayValue) {
            const month = parseInt(monthValue);
            const day = parseInt(dayValue);
            const year = yearValue ? parseInt(yearValue) : null;
            
            birthday = {
                day: day,
                month: month,
                year: year  // Can be null (optional)
            };
        }
        // If month is empty, birthday remains null (birthday will be removed)
        
        // Validate bio length
        if (bio.length > bioLimit) {
            statusDiv.textContent = `Error: Bio exceeds limit of ${bioLimit} characters`;
            statusDiv.style.color = '#dc3545';
            saveBtn.disabled = false;
            return;
        }
        
        const updateData = {
            first_name: first_name,
            last_name: last_name,
            username: username,
            bio: bio,
            birthday: birthday
        };
        
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/profile`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(updateData)
        });
        
        const data = await response.json();
        
        if (data.error) {
            statusDiv.textContent = `Error: ${data.error}`;
            statusDiv.style.color = '#dc3545';
            saveBtn.disabled = false;
            return;
        }
        
        // Success - reload profile to get updated data (especially profile photo)
        await loadAgentProfile(agentName);
        
        // Refresh agent list to update username in pulldown selectors
        await loadAgents();
        
        statusDiv.textContent = 'Profile saved successfully';
        statusDiv.style.color = '#28a745';
        cancelBtn.style.display = 'none';
        setAgentProfileNeedsSave(false);
        
        // Clear status message after 3 seconds
        setTimeout(() => {
            statusDiv.textContent = '';
        }, 3000);
        
    } catch (error) {
        console.error('Error saving profile:', error);
        statusDiv.textContent = `Error: ${error.message}`;
        statusDiv.style.color = '#dc3545';
    } finally {
        saveBtn.disabled = false;
    }
}

// Cancel profile edit
function cancelAgentProfileEdit() {
    if (originalAgentProfile) {
        // Restore original values
        document.getElementById('agent-profile-first-name').value = originalAgentProfile.first_name || '';
        document.getElementById('agent-profile-last-name').value = originalAgentProfile.last_name || '';
        document.getElementById('agent-profile-username').value = originalAgentProfile.username || '';
        const agentBioTextarea = document.getElementById('agent-profile-bio');
        agentBioTextarea.value = originalAgentProfile.bio || '';
        autoGrowTextarea(agentBioTextarea);
        
        // Restore birthday
        const monthSelect = document.getElementById('agent-profile-birthday-month');
        const daySelect = document.getElementById('agent-profile-birthday-day');
        const yearInput = document.getElementById('agent-profile-birthday-year');
        
        monthSelect.value = '';
        daySelect.innerHTML = '<option value="">Day</option>';
        yearInput.value = '';
        
        if (originalAgentProfile.birthday) {
            monthSelect.value = originalAgentProfile.birthday.month || '';
            updateBirthdayDays();
            if (originalAgentProfile.birthday.day) {
                daySelect.value = originalAgentProfile.birthday.day;
            }
            if (originalAgentProfile.birthday.year) {
                yearInput.value = originalAgentProfile.birthday.year;
            }
        }
        
        validateBio();
        document.getElementById('agent-profile-cancel-wrap').style.display = 'none';
        document.getElementById('agent-profile-save-status').textContent = '';
        setAgentProfileNeedsSave(false);
    }
}

// Set up birthday month change handler
document.addEventListener('DOMContentLoaded', () => {
    const monthSelect = document.getElementById('agent-profile-birthday-month');
    if (monthSelect) {
        monthSelect.addEventListener('change', updateBirthdayDays);
    }
    
    // Track changes to enable cancel button
    const profileFields = [
        'agent-profile-first-name',
        'agent-profile-last-name',
        'agent-profile-username',
        'agent-profile-bio',
        'agent-profile-birthday-month',
        'agent-profile-birthday-day',
        'agent-profile-birthday-year'
    ];
    
    profileFields.forEach(fieldId => {
        const field = document.getElementById(fieldId);
        if (field) {
            field.addEventListener('input', () => {
                document.getElementById('agent-profile-cancel-wrap').style.display = 'inline-block';
                setAgentProfileNeedsSave(true);
            });
            field.addEventListener('change', () => {
                document.getElementById('agent-profile-cancel-wrap').style.display = 'inline-block';
                setAgentProfileNeedsSave(true);
            });
        }
    });
});

function loadAgentConfiguration(agentName) {
    const container = document.getElementById('parameters-container');
    showLoading(container, 'Loading configuration...');
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration`)
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showError(container, data.error);
                return;
            }
            
            const currentLLM = data.llm || '';
            const availableLLMs = data.available_llms || [];
            const prompt = data.prompt || '';
            const currentTimezone = data.timezone || '';
            const availableTimezones = data.available_timezones || [];
            const isDisabled = data.is_disabled || false;
            const isGagged = data.is_gagged || false;
            
            // New fields
            const phone = data.phone || '';
            const rolePromptNames = data.role_prompt_names || [];
            const availableRolePrompts = data.available_role_prompts || [];
            const stickerSetNames = data.sticker_set_names || [];
            const dailyScheduleDescription = data.daily_schedule_description;
            const resetContextOnFirstMessage = data.reset_context_on_first_message || false;
            const clearSummariesOnFirstMessage = data.clear_summaries_on_first_message || false;
            const startTypingDelay = data.start_typing_delay !== undefined ? data.start_typing_delay : null;
            const typingSpeed = data.typing_speed !== undefined ? data.typing_speed : null;
            const configDirectory = data.config_directory || '';
            const availableConfigDirectories = data.available_config_directories || [];
            
            container.innerHTML = `
                <div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <div class="agent-status-header">
                        <div>
                            <h3 style="margin: 0;">Agent Status</h3>
                            <div class="agent-status-actions">
                                <button id="toggle-agent-button" onclick="toggleAgentDisabled('${escJsAttr(agentName)}', ${!isDisabled})" style="padding: 8px 16px; background: ${isDisabled ? '#28a745' : '#6c757d'}; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">
                                    ${isDisabled ? 'Enable Agent' : 'Disable Agent'}
                                </button>
                                ${isDisabled ? `
                                    <button id="delete-agent-button" onclick="deleteAgent('${escJsAttr(agentName)}', '${escJsAttr(data.name || agentName)}')" style="padding: 8px 16px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer;">
                                        Delete Agent
                                    </button>
                                ` : ''}
                            </div>
                        </div>
                        <div class="agent-status-info">
                            <div style="font-size: 12px; color: #666;">
                                Config Name (filename):
                                ${isDisabled ? 
                                    `<input type="text" value="${escapeHtml(agentName)}" 
                                        style="font-size: 12px; padding: 2px 4px; border: 1px solid #ccc; border-radius: 4px; width: 150px;"
                                        onchange="renameAgentConfig('${escJsAttr(agentName)}', this.value)">` : 
                                    `<strong>${escapeHtml(agentName)}</strong>`
                                }
                            </div>
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">
                                Config Directory:
                                ${isDisabled ? 
                                    `<select id="agent-config-directory-select" 
                                        style="font-size: 12px; padding: 2px 4px; border: 1px solid #ccc; border-radius: 4px; width: 200px; margin-left: 4px;"
                                        onchange="moveAgentConfigDirectory('${escJsAttr(agentName)}', this.value, '${escJsAttr(configDirectory)}')">
                                        ${availableConfigDirectories.map(dir => 
                                            `<option value="${escapeHtml(dir.value)}" ${dir.value === configDirectory ? 'selected' : ''}>${escapeHtml(dir.label)}</option>`
                                        ).join('')}
                                    </select>` : 
                                    `<strong>${escapeHtml(configDirectory || 'Unknown')}</strong>`
                                }
                            </div>
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">Status: <span style="color: ${isDisabled ? '#dc3545' : '#28a745'}; font-weight: bold;">${isDisabled ? 'Disabled' : 'Enabled'}</span></div>
                        </div>
                    </div>

                    <div id="agent-delete-status" style="margin-top: 8px; font-size: 13px; color: #666;"></div>

                    <div class="agent-param-grid">
                        <div class="agent-param-section">
                            <h3>Agent Name (display)${tooltipIconHtml('Display name used in the console')}</h3>
                            <input id="agent-name-input" type="text" class="agent-param-input" value="${escapeHtml(data.name || '')}" ${!isDisabled ? 'disabled' : ''} 
                                onchange="updateAgentName('${escJsAttr(agentName)}', this.value)">
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">Display name used in the console</div>
                        </div>
                        <div class="agent-param-section">
                            <h3>Agent Phone${tooltipIconHtml("E.164 format, e.g., +1234567890")}</h3>
                            <input id="agent-phone-input" type="text" class="agent-param-input" value="${escapeHtml(phone)}" ${!isDisabled ? 'disabled' : ''} 
                                onchange="updateAgentPhone('${escJsAttr(agentName)}', this.value)">
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">E.164 format, e.g., +1234567890</div>
                        </div>
                    </div>
                    
                    <div class="agent-param-grid">
                        <div class="agent-param-section">
<h3>Agent LLM${tooltipIconHtml('LLM model for this agent; leave empty to use global default')}</h3>
                            <input
                                id="agent-llm-select"
                                type="text" 
                                class="agent-param-input" 
                                value="${escapeHtml(currentLLM || '')}"
                                placeholder="Type or select an LLM model...">
                        </div>
                    </div>

                    <div class="agent-param-section">
                        <h3>Role Prompts${tooltipIconHtml("Global role prompts to include in the agent system prompt")}</h3>
                        <div id="role-prompts-list" class="role-prompts-container">
                            ${rolePromptNames.length > 0 ? rolePromptNames.map(name => `
                                <div class="role-prompt-tag">
                                    <span>${escapeHtml(name)}</span>
                                    ${isDisabled ? `<button onclick="removeRolePrompt('${escJsAttr(agentName)}', '${escJsAttr(name)}')">&times;</button>` : ''}
                                </div>
                            `).join('') : '<div style="color: #666; font-style: italic; font-size: 14px;">No role prompts selected</div>'}
                        </div>
                        <select id="available-role-prompts-select" ${!isDisabled ? 'disabled' : ''} 
                            onchange="if(this.value) addRolePrompt('${escJsAttr(agentName)}', this.value); this.value='';" 
                            class="agent-param-input" style="min-width: 200px; width: auto;">
                            <option value="">Add role prompt...</option>
                            ${availableRolePrompts.filter(p => !rolePromptNames.includes(p)).map(p => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join('')}
                        </select>
                    </div>

                    <div class="agent-param-section">
                        <h3>Sticker Sets${tooltipIconHtml('One set name per line (e.g. WendyDancer). Stickers in Saved Messages are also available.')}</h3>
                        <textarea id="agent-sticker-sets-textarea" ${!isDisabled ? 'disabled' : ''} 
                            onchange="updateAgentStickers('${escJsAttr(agentName)}')" 
                            class="agent-param-textarea" style="min-height: 80px;"
                            placeholder="One set name per line, e.g.\nWendyDancer\nCindyAI"
                        >${escapeHtml(stickerSetNames.join('\n'))}</textarea>
                    </div>

                    <div class="agent-param-section">
                        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
                            <h3 style="margin: 0;">Daily Schedule${tooltipIconHtml('Freeform English description of when the agent is active')}</h3>
                            <label class="agent-param-checkbox-label">
                                <input type="checkbox" id="daily-schedule-enabled" ${dailyScheduleDescription !== null ? 'checked' : ''} ${!isDisabled ? 'disabled' : ''} 
                                    onchange="updateAgentDailySchedule('${escJsAttr(agentName)}')">
                                Enabled
                            </label>
                        </div>
                        <textarea id="daily-schedule-textarea" ${!isDisabled ? 'disabled' : ''} 
                            onchange="updateAgentDailySchedule('${escJsAttr(agentName)}')" 
                            class="agent-param-textarea" style="min-height: 60px; ${dailyScheduleDescription === null ? 'background: #f8f9fa; color: #999;' : ''}"
                            placeholder="Freeform English description of the daily schedule..."
                        >${escapeHtml(dailyScheduleDescription || '')}</textarea>
                    </div>

                    <div class="agent-param-grid">
                        <div class="agent-param-section">
                            <h3>Agent Timezone${tooltipIconHtml('IANA timezone for schedule and time-based behavior')}</h3>
                            <select id="agent-timezone-select" class="agent-param-input" onchange="updateAgentTimezone('${escJsAttr(agentName)}', this.value)">
                                <option value="">Server Default</option>
                                ${availableTimezones.map(tz => {
                                    return `<option value="${escapeHtml(tz.value)}" ${tz.value === currentTimezone ? 'selected' : ''}>${escapeHtml(tz.label)}</option>`;
                                }).join('')}
                            </select>
                        </div>
                        <div class="agent-param-section">
                            <h3>Context Reset${tooltipIconHtml('Clear conversation context when user sends first message in a new session')}</h3>
                            <label class="agent-param-checkbox-label" style="margin-top: 8px;">
                                <input type="checkbox" id="reset-context-toggle" ${resetContextOnFirstMessage ? 'checked' : ''} ${!isDisabled ? 'disabled' : ''} 
                                    onchange="updateAgentResetContext('${escJsAttr(agentName)}', this.checked)">
                                Reset Context On First Message
                            </label>
                        </div>
                        <div class="agent-param-section">
                            <h3>Clear Summaries${tooltipIconHtml('Clear stored summaries when user sends first message in a new session')}</h3>
                            <label class="agent-param-checkbox-label" style="margin-top: 8px;">
                                <input type="checkbox" id="clear-summaries-toggle" ${clearSummariesOnFirstMessage ? 'checked' : ''} ${!isDisabled ? 'disabled' : ''} 
                                    onchange="updateAgentClearSummaries('${escJsAttr(agentName)}', this.checked)">
                                Clear Summaries On First Message
                            </label>
                        </div>
                        <div class="agent-param-section">
                            <h3>Global Gagged${tooltipIconHtml('When gagged, messages are read but no received tasks are created; can be overridden per conversation')}</h3>
                            <label class="agent-param-checkbox-label" style="margin-top: 8px;">
                                <input type="checkbox" id="gagged-toggle" ${isGagged ? 'checked' : ''} 
                                    onchange="updateAgentGagged('${escJsAttr(agentName)}', this.checked)">
                                Gag all conversations by default (can be overridden per conversation)
                            </label>
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">When gagged, messages are read but no received tasks are created</div>
                        </div>
                    </div>

                    <div class="agent-param-grid">
                        <div class="agent-param-section">
                            <h3>Start Typing Delay Override${tooltipIconHtml('Seconds before typing indicator (leave empty for global default, range 1-3600)')}</h3>
                            <input id="start-typing-delay-input" type="number" step="0.1" min="1" max="3600" class="agent-param-input" 
                                value="${startTypingDelay !== null ? escapeHtml(String(startTypingDelay)) : ''}" 
                                placeholder="Use global default"
                                onchange="updateAgentStartTypingDelay('${escJsAttr(agentName)}', this.value)">
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">Seconds (leave empty for global default, range: 1-3600)</div>
                        </div>
                        <div class="agent-param-section">
                            <h3>Typing Speed Override${tooltipIconHtml('Characters per second (leave empty for global default, range 1–1000)')}</h3>
                            <input id="typing-speed-input" type="number" step="0.1" min="1" max="1000" class="agent-param-input" 
                                value="${typingSpeed !== null ? escapeHtml(String(typingSpeed)) : ''}" 
                                placeholder="Use global default"
                                onchange="updateAgentTypingSpeed('${escJsAttr(agentName)}', this.value)">
                            <div style="font-size: 12px; color: #666; margin-top: 4px;">Characters per second (leave empty for global default, range: 1-1000)</div>
                        </div>
                    </div>
                    
                    <div class="agent-param-section">
                        <h3>Agent Instructions${tooltipIconHtml('System prompt or instructions for the agent behavior')}</h3>
                        <textarea 
                            id="agent-prompt-textarea" 
                            class="agent-param-textarea" style="min-height: 300px;"
                            oninput="scheduleAgentPromptAutoSave('${escJsAttr(agentName)}')"
                        >${escapeHtml(prompt)}</textarea>
                        <div id="agent-prompt-status" style="margin-top: 8px; font-size: 12px; color: #28a745;">Saved</div>
                    </div>
                </div>
            `;

            const agentLlmInput = document.getElementById('agent-llm-select');
            setupLLMCombobox(agentLlmInput, availableLLMs, {
                includeDefaultMarker: true,
                onChange: (value) => updateAgentLLM(agentName, value),
            });
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            container.innerHTML = `<div class="error">Error loading configuration: ${escapeHtml(error)}</div>`;
        });
}

function updateAgentPhone(agentName, phone) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/phone`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phone })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating phone: ' + data.error);
        else alert('Phone number updated successfully');
        loadAgentConfiguration(agentName);
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating phone: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentName(agentName, name) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/name`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating name: ' + data.error);
        else {
            alert('Agent name updated successfully');
            loadAgents().then(() => {
                document.getElementById('agents-agent-select').value = agentName;
            });
            loadAgentConfiguration(agentName);
        }
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating name: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function addRolePrompt(agentName, roleName) {
    // Get current prompts and add new one
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration`)
        .then(r => r.json())
        .then(data => {
            const prompts = data.role_prompt_names || [];
            if (!prompts.includes(roleName)) {
                prompts.push(roleName);
                saveRolePrompts(agentName, prompts);
            }
        });
}

function removeRolePrompt(agentName, roleName) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration`)
        .then(r => r.json())
        .then(data => {
            const prompts = (data.role_prompt_names || []).filter(p => p !== roleName);
            saveRolePrompts(agentName, prompts);
        });
}

function saveRolePrompts(agentName, prompts) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/role-prompts`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_prompt_names: prompts })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating role prompts: ' + data.error);
        loadAgentConfiguration(agentName);
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating role prompts: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentStickers(agentName) {
    const sets = document.getElementById('agent-sticker-sets-textarea').value.split('\n').map(s => s.trim()).filter(s => s);

    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/stickers`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sticker_set_names: sets })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating stickers: ' + data.error);
        loadAgentConfiguration(agentName);
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating stickers: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentDailySchedule(agentName) {
    const enabled = document.getElementById('daily-schedule-enabled').checked;
    const description = document.getElementById('daily-schedule-textarea').value.trim();
    
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/daily-schedule`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: enabled, description: description })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating daily schedule: ' + data.error);
        loadAgentConfiguration(agentName);
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating daily schedule: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentResetContext(agentName, enabled) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/reset-context`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reset_context_on_first_message: enabled })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating reset context: ' + data.error);
        loadAgentConfiguration(agentName);
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating reset context: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentClearSummaries(agentName, enabled) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/clear-summaries`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clear_summaries_on_first_message: enabled })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) alert('Error updating clear summaries: ' + data.error);
        loadAgentConfiguration(agentName);
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') alert('Error updating clear summaries: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentGagged(agentName, isGagged) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/gagged`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_gagged: isGagged })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error updating gagged status: ' + data.error);
            loadAgentConfiguration(agentName);
        }
    })
    .catch(error => {
        if (error && error.message !== 'unauthorized') {
            alert('Error updating gagged status: ' + error);
        }
        loadAgentConfiguration(agentName);
    });
}

function updateAgentLLM(agentName, llmName) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/llm`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ llm_name: llmName })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error updating LLM: ' + data.error);
            // Reload to restore previous value
            loadAgentConfiguration(agentName);
        } else {
            alert('LLM updated successfully');
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error updating LLM: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentTimezone(agentName, timezone) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/timezone`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ timezone: timezone })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error updating timezone: ' + data.error);
            // Reload to restore previous value
            loadAgentConfiguration(agentName);
        } else {
            alert('Timezone updated successfully');
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error updating timezone: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentStartTypingDelay(agentName, value) {
    const startTypingDelay = value.trim() === '' ? '' : value.trim();
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/start-typing-delay`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ start_typing_delay: startTypingDelay })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error updating start typing delay: ' + data.error);
            // Reload to restore previous value
            loadAgentConfiguration(agentName);
        } else {
            alert('Start typing delay updated successfully');
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error updating start typing delay: ' + error);
        loadAgentConfiguration(agentName);
    });
}

function updateAgentTypingSpeed(agentName, value) {
    const typingSpeed = value.trim() === '' ? '' : value.trim();
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/typing-speed`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ typing_speed: typingSpeed })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error updating typing speed: ' + data.error);
            // Reload to restore previous value
            loadAgentConfiguration(agentName);
        } else {
            alert('Typing speed updated successfully');
        }
    })
    .catch(error => {
        if (error && error.message === 'unauthorized') {
            return;
        }
        alert('Error updating typing speed: ' + error);
        loadAgentConfiguration(agentName);
    });
}

// Auto-save for agent prompt
let agentPromptAutoSaveTimer = null;
function scheduleAgentPromptAutoSave(agentName) {
    if (agentPromptAutoSaveTimer) {
        clearTimeout(agentPromptAutoSaveTimer);
    }
    
    const statusEl = document.getElementById('agent-prompt-status');
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    agentPromptAutoSaveTimer = setTimeout(() => {
        const textarea = document.getElementById('agent-prompt-textarea');
        const prompt = textarea.value.trim();
        
        if (statusEl) {
            statusEl.textContent = 'Saving...';
            statusEl.style.color = '#007bff';
        }
        
        fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/configuration/prompt`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ prompt: prompt })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                if (statusEl) {
                    statusEl.textContent = 'Error';
                    statusEl.style.color = '#dc3545';
                }
            } else {
                if (statusEl) {
                    statusEl.textContent = 'Saved';
                    statusEl.style.color = '#28a745';
                }
            }
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') {
                return;
            }
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        });
    }, 1000);
}

// Load recent conversations dropdown

// ============================================================================
// Media Management Functions
// ============================================================================

async function loadAgentMedia(agentName) {
    const container = document.getElementById('agents-media-list');
    if (!container) return;
    
    container.innerHTML = '<div class="loading">Loading media...</div>';
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media`);
        const data = await response.json();
        
        if (data.error) {
            container.innerHTML = `<div class="error">Error: ${data.error}</div>`;
            return;
        }
        
        const media = data.media || [];
        
        if (media.length === 0) {
            container.innerHTML = '<div class="loading">No media found. Upload media or save from conversations.</div>';
            return;
        }
        
        container.innerHTML = '';
        media.forEach(item => {
            container.appendChild(renderMediaItem(agentName, item));
        });
        loadAgentTGSAnimations(agentName);
        
    } catch (error) {
        console.error('Error loading media:', error);
        container.innerHTML = `<div class="error">Error loading media</div>`;
    }
}

function renderMediaItem(agentName, mediaItem) {
    // Skip items without unique_id
    if (!mediaItem || !mediaItem.unique_id) {
        console.error('Media item missing unique_id:', mediaItem);
        return document.createElement('div'); // Return empty div
    }
    
    const div = document.createElement('div');
    div.className = 'media-item';
    div.dataset.uniqueId = mediaItem.unique_id;
    
    // Preview container (shared with Global Media Editor - .media-preview)
    const thumbnailDiv = document.createElement('div');
    thumbnailDiv.className = 'media-preview';
    
    const mediaUrl = `${API_BASE}/agents/${encodeURIComponent(agentName)}/media/${encodeURIComponent(mediaItem.unique_id)}/file`;
    const mimeType = (mediaItem.mime_type || '').toLowerCase();
    const isTgs = mimeType.includes('tgsticker') || mimeType === 'application/gzip' || mediaItem.media_kind === 'animated_sticker';
    const isVideo = mimeType.startsWith('video/') || mediaItem.media_kind === 'video';

    if (isTgs) {
        const tgsContainer = document.createElement('div');
        tgsContainer.id = `agent-tgs-player-${mediaItem.unique_id}`;
        tgsContainer.className = 'tgs-animation-container';
        tgsContainer.dataset.mediaUrl = mediaUrl;
        tgsContainer.style.width = '100%';
        tgsContainer.style.height = '100%';
        tgsContainer.style.display = 'flex';
        tgsContainer.style.alignItems = 'center';
        tgsContainer.style.justifyContent = 'center';
        tgsContainer.innerHTML = `
            <div style="text-align: center; color: #666;">
                <div style="font-size: 24px; margin-bottom: 10px;">🎭</div>
                <div style="font-size: 12px;">Loading animated sticker...</div>
            </div>
        `;
        thumbnailDiv.appendChild(tgsContainer);
    } else if (isVideo) {
        const video = document.createElement('video');
        video.controls = true;
        video.preload = 'metadata';
        video.style.width = '100%';
        video.style.height = '100%';
        video.style.cursor = 'pointer';
        video.onclick = (e) => { e.preventDefault(); e.stopPropagation(); showMediaFullscreen(mediaUrl, 'video'); };
        const source = document.createElement('source');
        source.src = mediaUrl;
        source.type = mimeType || 'video/mp4';
        video.appendChild(source);
        thumbnailDiv.appendChild(video);
    } else {
        const img = document.createElement('img');
        img.src = mediaUrl;
        img.alt = 'Media';
        img.style.cursor = 'pointer';
        img.onclick = (e) => { e.preventDefault(); e.stopPropagation(); showMediaFullscreen(mediaUrl, 'image'); };
        img.onerror = function() {
            console.error('Failed to load media:', mediaItem.unique_id);
            this.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="150" height="150"><rect width="150" height="150" fill="%23f5f5f5"/><text x="50%" y="50%" text-anchor="middle" dy=".3em" fill="%23999" font-family="sans-serif" font-size="14">Failed to load</text></svg>';
        };
        thumbnailDiv.appendChild(img);
    }

    div.appendChild(thumbnailDiv);
    
    // Content container
    const contentDiv = document.createElement('div');
    contentDiv.className = 'media-item-content';
    
    // Title with filename and unique_id
    const title = document.createElement('h3');
    title.style.marginTop = '0';
    title.style.marginBottom = '10px';
    title.style.fontSize = '16px';
    // Show unique_id if no filename, or filename + unique_id if both
    if (mediaItem.file_name) {
        title.textContent = `${mediaItem.file_name} (${mediaItem.unique_id})`;
    } else {
        title.textContent = mediaItem.unique_id;
    }
    contentDiv.appendChild(title);
    
    // Type
    const typePara = document.createElement('p');
    typePara.style.margin = '4px 0';
    typePara.innerHTML = `<strong>Type:</strong> ${mediaItem.media_kind || 'unknown'}`;
    contentDiv.appendChild(typePara);
    
    // Status
    const statusPara = document.createElement('p');
    statusPara.id = `agent-media-status-display-${mediaItem.unique_id}`;
    statusPara.style.margin = '4px 0';
    statusPara.innerHTML = `<strong>Status:</strong> ${mediaItem.status || 'unknown'}`;
    contentDiv.appendChild(statusPara);
    
    // Description textarea (like media editor)
    const textarea = document.createElement('textarea');
    textarea.id = `agent-media-desc-${mediaItem.unique_id}`;
    textarea.placeholder = 'Enter description...';
    textarea.value = mediaItem.description || '';
    textarea.style.width = '100%';
    textarea.style.minHeight = '80px';
    textarea.style.padding = '8px';
    textarea.style.border = '1px solid #ddd';
    textarea.style.borderRadius = '4px';
    textarea.style.fontFamily = 'inherit';
    textarea.style.fontSize = '14px';
    textarea.style.resize = 'vertical';
    textarea.style.boxSizing = 'border-box';
    textarea.style.marginTop = '10px';
    textarea.oninput = () => scheduleMediaDescriptionSave(agentName, mediaItem.unique_id);
    contentDiv.appendChild(textarea);
    
    // Controls row (status + buttons)
    const controlsDiv = document.createElement('div');
    controlsDiv.style.display = 'flex';
    controlsDiv.style.alignItems = 'center';
    controlsDiv.style.marginTop = '8px';
    controlsDiv.style.gap = '10px';
    controlsDiv.style.flexWrap = 'wrap';
    
    // Save status indicator
    const statusSpan = document.createElement('span');
    statusSpan.id = `agent-media-status-${mediaItem.unique_id}`;
    statusSpan.style.fontSize = '12px';
    statusSpan.style.color = '#28a745';
    statusSpan.textContent = 'Saved';
    controlsDiv.appendChild(statusSpan);
    
    // Refresh from AI button
    const refreshBtn = document.createElement('button');
    refreshBtn.textContent = 'Refresh from AI';
    refreshBtn.style.padding = '4px 8px';
    refreshBtn.style.fontSize = '11px';
    refreshBtn.style.background = '#6c757d';
    refreshBtn.style.color = 'white';
    refreshBtn.style.border = 'none';
    refreshBtn.style.borderRadius = '3px';
    refreshBtn.style.cursor = 'pointer';
    refreshBtn.onclick = () => refreshMediaDescription(agentName, mediaItem.unique_id);
    controlsDiv.appendChild(refreshBtn);
    
    // Profile Picture checkbox
    const checkboxLabel = document.createElement('label');
    checkboxLabel.style.display = 'flex';
    checkboxLabel.style.alignItems = 'center';
    checkboxLabel.style.gap = '6px';
    checkboxLabel.style.fontSize = '11px';
    checkboxLabel.style.cursor = mediaItem.can_be_profile_photo ? 'pointer' : 'not-allowed';
    if (!mediaItem.can_be_profile_photo) {
        checkboxLabel.style.opacity = '0.5';
        checkboxLabel.title = 'This media type cannot be used as a profile picture';
    }
    
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = mediaItem.is_profile_photo || false;
    checkbox.disabled = !mediaItem.can_be_profile_photo;
    checkbox.onchange = () => toggleProfilePhoto(agentName, mediaItem.unique_id, checkbox.checked);
    
    const checkboxText = document.createTextNode('Profile');
    checkboxLabel.appendChild(checkbox);
    checkboxLabel.appendChild(checkboxText);
    controlsDiv.appendChild(checkboxLabel);
    
    // Delete button
    const deleteBtn = document.createElement('button');
    deleteBtn.textContent = 'Delete';
    deleteBtn.style.padding = '4px 8px';
    deleteBtn.style.fontSize = '11px';
    deleteBtn.style.background = '#dc3545';
    deleteBtn.style.color = 'white';
    deleteBtn.style.border = 'none';
    deleteBtn.style.borderRadius = '3px';
    deleteBtn.style.cursor = 'pointer';
    deleteBtn.onclick = () => deleteAgentMedia(agentName, mediaItem.unique_id);
    controlsDiv.appendChild(deleteBtn);
    
    contentDiv.appendChild(controlsDiv);
    div.appendChild(contentDiv);
    
    return div;
}

async function loadAgentTGSAnimations(agentName) {
    void agentName; // Reserved for future route variants.
    await loadTGSAnimationsShared({
        selector: '[id^="agent-tgs-player-"]',
        getMediaUrl: (container) => container.dataset.mediaUrl,
        loadingLabel: 'Loading animated sticker...',
        errorLabel: 'Animated sticker preview unavailable',
        downloadLabel: 'Download',
    });
}

// Auto-save timers for media descriptions
const mediaDescriptionSaveTimers = {};

function scheduleMediaDescriptionSave(agentName, uniqueId) {
    // Clear existing timer
    if (mediaDescriptionSaveTimers[uniqueId]) {
        clearTimeout(mediaDescriptionSaveTimers[uniqueId]);
    }
    
    // Update status to "typing..."
    const statusEl = document.getElementById(`agent-media-status-${uniqueId}`);
    if (statusEl) {
        statusEl.textContent = 'Typing...';
        statusEl.style.color = '#007bff';
    }
    
    // Set new timer for 1 second delay
    mediaDescriptionSaveTimers[uniqueId] = setTimeout(() => {
        saveMediaDescription(agentName, uniqueId);
    }, 1000);
}

async function saveMediaDescription(agentName, uniqueId) {
    const textarea = document.getElementById(`agent-media-desc-${uniqueId}`);
    const statusEl = document.getElementById(`agent-media-status-${uniqueId}`);
    
    if (!textarea) return;
    
    const description = textarea.value.trim();
    
    if (statusEl) {
        statusEl.textContent = 'Saving...';
        statusEl.style.color = '#007bff';
    }
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media/${encodeURIComponent(uniqueId)}/description`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ description: description })
        });
        
        const data = await response.json();
        if (data.error) {
            if (statusEl) {
                statusEl.textContent = 'Error';
                statusEl.style.color = '#dc3545';
            }
        } else {
            if (statusEl) {
                statusEl.textContent = 'Saved';
                statusEl.style.color = '#28a745';
            }

            // Mark Media Editor stale so it reloads when navigating back
            window.mediaEditorNeedsRefresh = true;
            
            // Update status display if returned
            if (data.status) {
                const statusPara = document.getElementById(`agent-media-status-display-${uniqueId}`);
                if (statusPara) {
                    statusPara.innerHTML = `<strong>Status:</strong> ${data.status}`;
                }
            }
        }
    } catch (error) {
        console.error('Error saving description:', error);
        if (statusEl) {
            statusEl.textContent = 'Error';
            statusEl.style.color = '#dc3545';
        }
    }
}

function editMediaDescription(agentName, uniqueId, descDiv) {
    // This function is no longer used - descriptions are now always editable via textarea
    // Kept for compatibility but can be removed
}

async function refreshMediaDescription(agentName, uniqueId) {
    const textarea = document.getElementById(`agent-media-desc-${uniqueId}`);
    
    if (!textarea) {
        console.error(`Textarea not found for ${uniqueId}`);
        return;
    }
    
    // Find the button by looking for the parent container and finding the refresh button
    const mediaItemContainer = textarea.closest('.media-item');
    if (!mediaItemContainer) {
        console.error(`Media item container not found for ${uniqueId}`);
        return;
    }
    
    // Find the refresh button within this media item
    const buttons = mediaItemContainer.querySelectorAll('button');
    let button = null;
    for (const btn of buttons) {
        if (btn.textContent.includes('Refresh from AI') || btn.textContent.includes('Generating...')) {
            button = btn;
            break;
        }
    }
    
    if (!button) {
        console.error(`Refresh button not found for ${uniqueId}`);
        return;
    }
    
    // Disable button and show loading state
    button.disabled = true;
    button.textContent = 'Generating...';
    button.style.background = '#007bff';
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media/${encodeURIComponent(uniqueId)}/refresh-description`, {
            method: 'POST'
        });
        
        const data = await response.json();
        if (data.error) {
            alert('Error refreshing from AI: ' + data.error);
            button.textContent = 'Refresh from AI';
            button.style.background = '#6c757d';
            button.disabled = false;
            return;
        }
        
        // Update the textarea with the new AI-generated description
        textarea.value = data.description || '';
        
        // Update status display if returned
        if (data.status) {
            const statusPara = document.getElementById(`agent-media-status-display-${uniqueId}`);
            if (statusPara) {
                statusPara.innerHTML = `<strong>Status:</strong> ${data.status}`;
            }
        }

        // Mark Media Editor stale so it reloads when navigating back
        window.mediaEditorNeedsRefresh = true;
        
        // Reset button
        button.textContent = 'Refresh from AI';
        button.style.background = '#6c757d';
        button.disabled = false;
        
    } catch (error) {
        console.error('Error refreshing description:', error);
        alert('Error refreshing description');
        button.textContent = 'Refresh from AI';
        button.style.background = '#6c757d';
        button.disabled = false;
    }
}

async function toggleProfilePhoto(agentName, uniqueId, isChecked) {
    try {
        let response;
        if (isChecked) {
            // Set as profile photo
            response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media/${encodeURIComponent(uniqueId)}/set-profile-photo`, {
                method: 'POST'
            });
        } else {
            // Remove from profile photos
            response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media/${encodeURIComponent(uniqueId)}/profile-photo`, {
                method: 'DELETE'
            });
        }
        
        const data = await response.json();
        if (data.error) {
            alert('Error: ' + data.error);
            // Reload to revert checkbox
            loadAgentMedia(agentName);
            return;
        }
        
        // Success - reload media list
        loadAgentMedia(agentName);
        
    } catch (error) {
        console.error('Error toggling profile photo:', error);
        alert('Error updating profile photo');
        loadAgentMedia(agentName);
    }
}

async function deleteAgentMedia(agentName, uniqueId) {
    if (!confirm('Delete this media from Saved Messages?')) {
        return;
    }
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media/${encodeURIComponent(uniqueId)}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        if (data.error) {
            alert('Error deleting media: ' + data.error);
            return;
        }
        
        // Remove from UI
        const item = document.querySelector(`.media-item[data-unique-id="${uniqueId}"]`);
        if (item) {
            item.remove();
        }
        
        // Check if empty
        const container = document.getElementById('agents-media-list');
        if (container && container.children.length === 0) {
            container.innerHTML = '<div class="loading">No media found. Upload media or save from conversations.</div>';
        }
        
    } catch (error) {
        console.error('Error deleting media:', error);
        alert('Error deleting media');
    }
}

async function uploadMediaFile(agentName, file) {
    const container = document.getElementById('agents-media-list');
    if (!container) return;
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/media/upload`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        if (data.error) {
            alert('Error uploading: ' + data.error);
            return;
        }
        
        // Reload media list
        loadAgentMedia(agentName);
        
    } catch (error) {
        console.error('Error uploading media:', error);
        alert('Error uploading media');
    }
}

function setupMediaDropZone() {
    const dropZone = document.getElementById('agents-media-drop-zone');
    if (!dropZone) return;
    
    // Prevent default drag behaviors
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            e.stopPropagation();
        }, false);
    });
    
    // Highlight on drag over
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
            dropZone.classList.add('drag-over');
        }, false);
    });
    
    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => {
            dropZone.classList.remove('drag-over');
        }, false);
    });
    
    // Handle dropped files
    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        
        const agentName = document.getElementById('agents-agent-select')?.value;
        if (!agentName) {
            alert('Please select an agent first');
            return;
        }
        
        if (files.length > 0) {
            uploadMediaFile(agentName, files[0]);
        }
    }, false);
    
    // Make it clickable
    dropZone.addEventListener('click', () => {
        document.getElementById('agents-media-upload-file')?.click();
    });
}

function setupMediaUploadButton() {
    const uploadBtn = document.getElementById('agents-media-upload-file');
    if (!uploadBtn) return;
    
    // Create hidden file input
    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = 'image/*,video/*,audio/*';
    fileInput.style.display = 'none';
    fileInput.onchange = () => {
        const agentName = document.getElementById('agents-agent-select')?.value;
        if (!agentName) {
            alert('Please select an agent first');
            return;
        }
        
        if (fileInput.files.length > 0) {
            uploadMediaFile(agentName, fileInput.files[0]);
        }
    };
    
    uploadBtn.parentElement.appendChild(fileInput);
    uploadBtn.onclick = () => fileInput.click();
}

// --- Schedule (calendar) maintenance ---
function formatScheduleDateTime(isoStr, timeZone) {
    if (!isoStr) return '';
    try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return isoStr;
        const opts = { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' };
        if (timeZone) {
            opts.timeZone = timeZone;
        }
        return new Intl.DateTimeFormat(undefined, opts).format(d);
    } catch (_) {
        return isoStr;
    }
}

/** Format start_time for display: date + time, no timezone (e.g. "2026-02-24 19:21"). */
function formatScheduleStartForInput(iso) {
    if (!iso) return '';
    const m = iso.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
    return m ? m[1] + ' ' + m[2] + ':' + m[3] : iso;
}

/** Format end_time for display: time only (e.g. "21:00"). */
function formatScheduleEndForInput(iso) {
    if (!iso) return '';
    const m = iso.match(/T(\d{2}):(\d{2})/);
    return m ? m[1] + ':' + m[2] : iso;
}

/** Get timezone offset suffix from an ISO string (e.g. "+01:00" or "Z"). */
function getScheduleOffsetFromISO(iso) {
    if (!iso) return '+00:00';
    const m = iso.match(/([+-]\d{2}:\d{2}|Z)$/);
    return m ? (m[1] === 'Z' ? '+00:00' : m[1]) : '+00:00';
}

/** Parse start input "YYYY-MM-DD HH:mm" back to full ISO using existing offset. */
function parseScheduleStartFromInput(val, existingISO) {
    const offset = getScheduleOffsetFromISO(existingISO || '');
    const m = String(val).trim().match(/^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}):(\d{2})/);
    if (!m) return val;
    const date = m[1], h = m[2].padStart(2, '0'), min = m[3];
    return date + 'T' + h + ':' + min + ':00' + offset;
}

/** Parse end input "HH:mm" back to full ISO using date and offset from existing end_time. */
function parseScheduleEndFromInput(val, existingISO) {
    const offset = getScheduleOffsetFromISO(existingISO || '');
    const m = String(val).trim().match(/^(\d{1,2}):(\d{2})/);
    if (!m) return existingISO || '';
    const datePart = existingISO ? existingISO.match(/^(\d{4}-\d{2}-\d{2})/) : null;
    const date = datePart ? datePart[1] : '2026-01-01';
    const h = m[1].padStart(2, '0'), min = m[2];
    return date + 'T' + h + ':' + min + ':00' + offset;
}

function scheduleUpdateAgentClock() {
    const span = document.getElementById('schedule-agent-time-value');
    const tz = window._scheduleClockTimezone;
    if (!span || !tz) return;
    try {
        const now = new Date();
        const fmt = new Intl.DateTimeFormat(undefined, {
            timeZone: tz,
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: true,
            timeZoneName: 'short'
        });
        span.textContent = fmt.format(now);
    } catch (_) {
        span.textContent = '--';
    }
}

function scheduleMarkDirty() {
    window._scheduleDirty = true;
    const bar = document.getElementById('schedule-unsaved-bar');
    if (bar) bar.style.display = 'flex';
}

function scheduleMarkClean() {
    window._scheduleDirty = false;
    const bar = document.getElementById('schedule-unsaved-bar');
    if (bar) bar.style.display = 'none';
}

function scheduleUpdateActivityField(index, field, value) {
    if (!window._scheduleActivities || !window._scheduleActivities[index]) return;
    window._scheduleActivities[index] = { ...window._scheduleActivities[index], [field]: value };
    scheduleMarkDirty();
}

function scheduleUpdateStartInput(index) {
    const act = window._scheduleActivities && window._scheduleActivities[index];
    if (!act) return;
    const input = document.querySelector('.schedule-field-start[data-index="' + index + '"]');
    if (!input) return;
    const parsed = parseScheduleStartFromInput(input.value, act.start_time);
    scheduleUpdateActivityField(index, 'start_time', parsed);
}

function scheduleUpdateEndInput(index) {
    const act = window._scheduleActivities && window._scheduleActivities[index];
    if (!act) return;
    const input = document.querySelector('.schedule-field-end[data-index="' + index + '"]');
    if (!input) return;
    const parsed = parseScheduleEndFromInput(input.value, act.end_time);
    scheduleUpdateActivityField(index, 'end_time', parsed);
}

function scheduleToggleExpand(index) {
    if (!window._scheduleExpandedIndices) window._scheduleExpandedIndices = new Set();
    if (window._scheduleExpandedIndices.has(index)) {
        window._scheduleExpandedIndices.delete(index);
    } else {
        window._scheduleExpandedIndices.add(index);
    }
    const container = document.getElementById('schedule-container');
    const agentName = window._scheduleAgentName;
    const timeZone = window._scheduleTimezone;
    if (container && agentName !== undefined) {
        renderScheduleContent(container, agentName, window._scheduleActivities, timeZone);
    }
}

function renderScheduleContent(container, agentName, activities, timeZone) {
    if (window._scheduleClockInterval) {
        clearInterval(window._scheduleClockInterval);
        window._scheduleClockInterval = null;
    }
    window._scheduleClockTimezone = timeZone || null;
    window._scheduleAgentName = agentName;
    window._scheduleActivities = (activities || []).map(a => ({ ...a }));
    if (!window._scheduleExpandedIndices) window._scheduleExpandedIndices = new Set();
    const isDirty = !!window._scheduleDirty;
    const clockRow = timeZone
        ? '<div id="schedule-clock-row" style="margin-bottom: 16px; padding: 10px 14px; background: #f8f9fa; border-radius: 6px; font-size: 15px;"><strong>Current time (agent\'s time zone):</strong> <span id="schedule-agent-time-value">--:--:--</span></div>'
        : '';
    const saveBar = '<div id="schedule-unsaved-bar" style="display: ' + (isDirty ? 'flex' : 'none') + '; align-items: center; gap: 12px; margin-bottom: 16px; padding: 10px 14px; background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px;"><span>You have unsaved changes.</span><button type="button" id="schedule-save-all-btn" style="padding: 6px 14px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Save</button></div>';
    const btnRow = '<div style="margin-bottom: 16px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center;">' +
        '<button type="button" id="schedule-add-activity-btn" onclick="scheduleAddActivity(\'' + escJsAttr(agentName) + '\', null, null, null)" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">+ Add activity</button>' +
        '<button type="button" id="schedule-extend-btn" onclick="scheduleExtend(\'' + escJsAttr(agentName) + '\')" style="padding: 8px 16px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Extend schedule</button>' +
        '<button type="button" id="schedule-delete-all-btn" onclick="scheduleDeleteAll(\'' + escJsAttr(agentName) + '\')" style="padding: 8px 16px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 14px;">Delete all</button>' +
        '</div>';
    if (!activities || activities.length === 0) {
        container.innerHTML = clockRow + saveBar + btnRow + '<div class="placeholder-card">No activities. Add one or extend the schedule to generate more.</div>';
        const saveAllBtn = document.getElementById('schedule-save-all-btn');
        if (saveAllBtn) {
            saveAllBtn.onclick = () => {
                const acts = window._scheduleActivities;
                const tz = window._scheduleTimezone;
                const agent = window._scheduleAgentName;
                if (!agent || acts === undefined) return;
                scheduleSave(agent, acts || [], tz, undefined, (err) => {
                    if (err) { alert('Failed to save schedule: ' + err); return; }
                    scheduleMarkClean();
                    loadSchedule(agent);
                });
            };
        }
        if (timeZone) {
            scheduleUpdateAgentClock();
            window._scheduleClockInterval = setInterval(scheduleUpdateAgentClock, 1000);
        }
        return;
    }
    let html = clockRow + saveBar + btnRow + '<div id="schedule-activity-list" style="display: flex; flex-direction: column; gap: 8px;">';
    const now = new Date();
    let currentIndex = -1;
    for (let i = 0; i < activities.length; i++) {
        const act = activities[i];
        try {
            const start = new Date(act.start_time);
            const end = new Date(act.end_time);
            if (!isNaN(start.getTime()) && !isNaN(end.getTime()) && start <= now && now <= end) {
                currentIndex = i;
                break;
            }
        } catch (_) { /* skip invalid dates */ }
    }
    activities.forEach((act, idx) => {
        const startVal = escapeHtml(formatScheduleStartForInput(act.start_time));
        const endVal = escapeHtml(formatScheduleEndForInput(act.end_time));
        const nameVal = escapeHtml(act.activity_name || '');
        const descVal = escapeHtml(act.description || '');
        const respVal = act.responsiveness !== undefined && act.responsiveness !== '' ? Number(act.responsiveness) : '';
        const expanded = window._scheduleExpandedIndices.has(idx);
        const agentEsc = escJsAttr(agentName);
        const borderStyle = (idx === currentIndex) ? '2px solid #000' : '1px solid #ddd';
        html += '<div class="schedule-activity-item" data-index="' + idx + '" style="border: ' + borderStyle + '; border-radius: 8px; background: #fff; overflow: hidden;">';
        html += '<div class="schedule-activity-header" style="display: flex; align-items: center; gap: 8px; padding: 8px 12px; flex-wrap: wrap;">';
        html += '<button type="button" class="schedule-toggle-btn" onclick="scheduleToggleExpand(' + idx + '); event.stopPropagation();" style="background: none; border: none; padding: 0 4px; cursor: pointer; font-size: 10px; color: #666;">' + (expanded ? '&#9660;' : '&#9654;') + '</button>';
        html += '<input type="text" class="schedule-field-start" data-index="' + idx + '" value="' + startVal + '" placeholder="Date time" style="width: 140px; max-width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;" onchange="scheduleUpdateStartInput(' + idx + ')">';
        html += '<input type="text" class="schedule-field-end" data-index="' + idx + '" value="' + endVal + '" placeholder="Time" style="width: 70px; max-width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;" onchange="scheduleUpdateEndInput(' + idx + ')">';
        html += '<input type="text" class="schedule-field-name" data-index="' + idx + '" value="' + nameVal + '" placeholder="Activity" style="flex: 1; min-width: 120px; box-sizing: border-box; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;" onchange="scheduleUpdateActivityField(' + idx + ', \'activity_name\', this.value)">';
        html += '</div>';
        html += '<div class="schedule-activity-body" style="display: ' + (expanded ? 'block' : 'none') + '; padding: 0 12px 12px 12px;">';
        html += '<div style="margin-bottom: 8px;"><label style="display: block; font-size: 12px; color: #666; margin-bottom: 4px;">Description</label><textarea class="schedule-field-desc" data-index="' + idx + '" rows="3" style="width: 100%; box-sizing: border-box; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; resize: vertical;" onchange="scheduleUpdateActivityField(' + idx + ', \'description\', this.value)">' + descVal + '</textarea></div>';
        html += '<div style="margin-bottom: 8px;"><label style="display: block; font-size: 12px; color: #666; margin-bottom: 4px;">Responsiveness (0–100)</label><input type="number" class="schedule-field-resp" data-index="' + idx + '" min="0" max="100" value="' + respVal + '" style="width: 80px; box-sizing: border-box; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px;" onchange="scheduleUpdateActivityField(' + idx + ', \'responsiveness\', parseInt(this.value, 10))"></div>';
        html += '<div style="display: flex; gap: 8px; flex-wrap: wrap;">';
        html += '<button type="button" onclick="scheduleAddAbove(\'' + agentEsc + '\', ' + idx + ')" style="padding: 4px 10px; font-size: 12px; cursor: pointer;">Add above</button>';
        html += '<button type="button" onclick="scheduleAddBelow(\'' + agentEsc + '\', ' + idx + ')" style="padding: 4px 10px; font-size: 12px; cursor: pointer;">Add below</button>';
        if (idx < activities.length - 1) {
            html += '<button type="button" onclick="scheduleMergeWithNext(\'' + agentEsc + '\', ' + idx + ')" style="padding: 4px 10px; font-size: 12px; cursor: pointer;">Merge with next</button>';
        }
        html += '<button type="button" class="schedule-delete-btn" onclick="scheduleDelete(\'' + agentEsc + '\', ' + idx + ')" style="padding: 2px 8px; background: none; border: none; cursor: pointer; font-size: 16px; color: #999;" title="Delete">&#215;</button>';
        html += '</div></div></div>';
    });
    html += '</div>';
    container.innerHTML = html;
    const saveAllBtn = document.getElementById('schedule-save-all-btn');
    if (saveAllBtn) {
        saveAllBtn.onclick = () => {
            const acts = window._scheduleActivities;
            const tz = window._scheduleTimezone;
            const agent = window._scheduleAgentName;
            if (!agent || !acts) return;
            scheduleSave(agent, acts, tz, undefined, (err) => {
                if (err) { alert('Failed to save schedule: ' + err); return; }
                scheduleMarkClean();
                loadSchedule(agent);
            });
        };
    }
    if (timeZone) {
        scheduleUpdateAgentClock();
        window._scheduleClockInterval = setInterval(scheduleUpdateAgentClock, 1000);
    }
}

function loadSchedule(agentName) {
    const container = document.getElementById('schedule-container');
    if (!container) return;
    const agentSelect = document.getElementById('agents-agent-select');
    const currentAgentName = agentSelect ? stripAsterisk(agentSelect.value) : null;
    if (currentAgentName !== agentName) return;
    container.innerHTML = '<div class="loading">Loading schedule...</div>';
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/schedule`)
        .then(response => response.json())
        .then(data => {
            const agentSelect2 = document.getElementById('agents-agent-select');
            if (agentSelect2 && stripAsterisk(agentSelect2.value) !== agentName) return;
            if (data.error) {
                container.innerHTML = `<div class="error">${escapeHtml(data.error)}</div>`;
                return;
            }
            window._scheduleDirty = false;
            window._scheduleExpandedIndices = new Set();
            renderScheduleContent(container, agentName, data.activities || [], data.timezone || null);
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') return;
            const agentSelect2 = document.getElementById('agents-agent-select');
            if (agentSelect2 && stripAsterisk(agentSelect2.value) !== agentName) return;
            container.innerHTML = '<div class="error">Error loading schedule: ' + escapeHtml(String(error)) + '</div>';
        });
}

function scheduleExtend(agentName) {
    const container = document.getElementById('schedule-container');
    const extendBtn = document.getElementById('schedule-extend-btn');
    if (!container || !extendBtn) return;
    const agentSelect = document.getElementById('agents-agent-select');
    if (agentSelect && stripAsterisk(agentSelect.value) !== agentName) return;
    extendBtn.disabled = true;
    extendBtn.textContent = 'Extending…';
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/schedule/extend`, { method: 'POST' })
        .then(response => response.json())
        .then(data => {
            if (agentSelect && stripAsterisk(agentSelect.value) !== agentName) {
                extendBtn.disabled = false;
                extendBtn.textContent = 'Extend schedule';
                return;
            }
            if (data.error) {
                alert('Extend failed: ' + data.error);
                extendBtn.disabled = false;
                extendBtn.textContent = 'Extend schedule';
                return;
            }
            window._scheduleDirty = true;
            window._scheduleActivities = (data.activities || []).map(a => ({ ...a }));
            renderScheduleContent(container, agentName, window._scheduleActivities, data.timezone || null);
        })
        .catch(error => {
            if (error && error.message === 'unauthorized') return;
            if (agentSelect && stripAsterisk(agentSelect.value) !== agentName) return;
            alert('Extend failed: ' + (error && error.message ? error.message : String(error)));
            extendBtn.disabled = false;
            extendBtn.textContent = 'Extend schedule';
        });
}

function scheduleDeleteAll(agentName) {
    const container = document.getElementById('schedule-container');
    const agentSelect = document.getElementById('agents-agent-select');
    if (agentSelect && stripAsterisk(agentSelect.value) !== agentName) return;
    if (!confirm('Delete all schedule entries for this agent? You can save or discard changes.')) return;
    window._scheduleActivities = [];
    if (window._scheduleExpandedIndices) window._scheduleExpandedIndices.clear();
    scheduleMarkDirty();
    renderScheduleContent(container, agentName, [], window._scheduleTimezone || null);
}

function scheduleGetActivities(agentName, cb) {
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/schedule`)
        .then(response => response.json())
        .then(data => {
            if (data.error) { cb(null, data.error); return; }
            cb(data.activities || [], null);
        })
        .catch(e => { cb(null, e.message || String(e)); });
}

function scheduleSave(agentName, activities, timezone, lastExtended, done) {
    const payload = { activities };
    if (timezone !== undefined) payload.timezone = timezone;
    if (lastExtended !== undefined) payload.last_extended = lastExtended;
    fetchWithAuth(`${API_BASE}/agents/${encodeURIComponent(agentName)}/schedule`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(response => response.json())
        .then(data => {
            if (data.error) { if (done) done(data.error); return; }
            loadSchedule(agentName);
            if (done) done(null);
        })
        .catch(e => { if (done) done(e.message || String(e)); });
}

function scheduleAddActivity(agentName, suggestedStart, suggestedEnd, insertBeforeIndex) {
    const startIso = suggestedStart || '';
    const endIso = suggestedEnd || '';
    const modal = document.createElement('div');
    modal.id = 'schedule-activity-modal';
    modal.style.cssText = 'position: fixed; z-index: 2000; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center;';
    modal.innerHTML = '<div style="background: white; padding: 24px; border-radius: 8px; max-width: 480px; width: 90%; box-shadow: 0 4px 20px rgba(0,0,0,0.2);">' +
        '<h3 style="margin-top: 0;">Add activity</h3>' +
        '<p style="margin-bottom: 12px; font-size: 13px; color: #666;">Use ISO 8601 with timezone (e.g. 2025-12-04T06:30:00-10:00)</p>' +
        '<label style="display: block; margin-bottom: 4px;">Start time</label><input type="text" id="schedule-form-start" value="' + escapeHtml(startIso) + '" placeholder="2025-12-04T06:30:00-10:00" style="width: 100%; padding: 8px; margin-bottom: 12px; box-sizing: border-box;">' +
        '<label style="display: block; margin-bottom: 4px;">End time</label><input type="text" id="schedule-form-end" value="' + escapeHtml(endIso) + '" placeholder="2025-12-04T07:30:00-10:00" style="width: 100%; padding: 8px; margin-bottom: 12px; box-sizing: border-box;">' +
        '<label style="display: block; margin-bottom: 4px;">Activity name</label><input type="text" id="schedule-form-name" value="" placeholder="e.g. Wake / breakfast" style="width: 100%; padding: 8px; margin-bottom: 12px; box-sizing: border-box;">' +
        '<label style="display: block; margin-bottom: 4px;">Responsiveness (0–100)</label><input type="number" id="schedule-form-responsiveness" min="0" max="100" value="80" style="width: 100%; padding: 8px; margin-bottom: 12px; box-sizing: border-box;">' +
        '<label style="display: block; margin-bottom: 4px;">Description</label><textarea id="schedule-form-description" rows="3" placeholder="Optional details" style="width: 100%; padding: 8px; margin-bottom: 16px; box-sizing: border-box; resize: vertical;"></textarea>' +
        '<div style="display: flex; gap: 8px;"><button type="button" id="schedule-form-save" style="padding: 8px 16px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer;">Save</button><button type="button" id="schedule-form-cancel" style="padding: 8px 16px; background: #6c757d; color: white; border: none; border-radius: 4px; cursor: pointer;">Cancel</button></div>' +
        '</div>';
    document.body.appendChild(modal);
    const saveBtn = document.getElementById('schedule-form-save');
    const cancelBtn = document.getElementById('schedule-form-cancel');
    cancelBtn.onclick = () => { modal.remove(); };
    modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
    saveBtn.onclick = () => {
        const start = document.getElementById('schedule-form-start').value.trim();
        const end = document.getElementById('schedule-form-end').value.trim();
        const name = document.getElementById('schedule-form-name').value.trim();
        const resp = parseInt(document.getElementById('schedule-form-responsiveness').value, 10);
        const desc = document.getElementById('schedule-form-description').value.trim();
        if (!start || !end || !name) { alert('Start time, end time, and activity name are required.'); return; }
        if (isNaN(resp) || resp < 0 || resp > 100) { alert('Responsiveness must be 0–100.'); return; }
        let activities = window._scheduleActivities || [];
        const newId = 'act-' + Math.random().toString(16).slice(2, 10);
        const newAct = { id: newId, start_time: start, end_time: end, activity_name: name, responsiveness: resp, description: desc };
        if (insertBeforeIndex != null && insertBeforeIndex >= 0) {
            activities.splice(insertBeforeIndex, 0, newAct);
        } else {
            activities.push(newAct);
        }
        window._scheduleActivities = activities;
        scheduleMarkDirty();
        const container = document.getElementById('schedule-container');
        if (container) renderScheduleContent(container, agentName, activities, window._scheduleTimezone || null);
        modal.remove();
    };
}

function scheduleAddAbove(agentName, index) {
    scheduleGetActivities(agentName, (activities, err) => {
        if (err) { alert('Failed to load schedule: ' + err); return; }
        const prev = activities[index - 1];
        const curr = activities[index];
        const suggestedEnd = curr ? curr.start_time : '';
        let suggestedStart = prev ? prev.end_time : '';
        if (!suggestedStart && curr) {
            try {
                const d = new Date(curr.start_time);
                d.setMinutes(d.getMinutes() - 60);
                suggestedStart = d.toISOString().slice(0, 19) + (curr.start_time.includes('-') ? curr.start_time.slice(curr.start_time.indexOf('-')) : '+00:00');
            } catch (_) {}
        }
        scheduleAddActivity(agentName, suggestedStart, suggestedEnd, index);
    });
}

function scheduleAddBelow(agentName, index) {
    scheduleGetActivities(agentName, (activities, err) => {
        if (err) { alert('Failed to load schedule: ' + err); return; }
        const curr = activities[index];
        const next = activities[index + 1];
        const suggestedStart = curr ? curr.end_time : '';
        let suggestedEnd = next ? next.start_time : '';
        if (!suggestedEnd && curr) {
            try {
                const d = new Date(curr.end_time);
                d.setMinutes(d.getMinutes() + 60);
                suggestedEnd = d.toISOString().slice(0, 19) + (curr.end_time.includes('-') ? curr.end_time.slice(curr.end_time.indexOf('-')) : '+00:00');
            } catch (_) {}
        }
        scheduleAddActivity(agentName, suggestedStart, suggestedEnd, index + 1);
    });
}

function scheduleMergeWithNext(agentName, index) {
    const activities = window._scheduleActivities;
    if (!activities || index >= activities.length - 1) return;
    const curr = activities[index];
    const next = activities[index + 1];
    const merged = activities.slice(0, index).concat([{ ...curr, end_time: next.end_time }]).concat(activities.slice(index + 2));
    window._scheduleActivities = merged;
    scheduleMarkDirty();
    const container = document.getElementById('schedule-container');
    if (container) renderScheduleContent(container, agentName, merged, window._scheduleTimezone || null);
}

function scheduleDelete(agentName, index) {
    const activities = window._scheduleActivities;
    if (!activities || index < 0 || index >= activities.length) return;
    const filtered = activities.filter((_, i) => i !== index);
    if (window._scheduleExpandedIndices) {
        window._scheduleExpandedIndices = new Set([...window._scheduleExpandedIndices].filter(i => i !== index).map(i => i > index ? i - 1 : i));
    }
    window._scheduleActivities = filtered;
    scheduleMarkDirty();
    const container = document.getElementById('schedule-container');
    if (container) renderScheduleContent(container, agentName, filtered, window._scheduleTimezone || null);
}

// Initialize media upload features when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        setupMediaDropZone();
        setupMediaUploadButton();
    });
} else {
    setupMediaDropZone();
    setupMediaUploadButton();
}

