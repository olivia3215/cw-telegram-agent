/**
 * Users tab: active users list, profile, conversations summary, accounting.
 * Depends on admin_console_core.js (API_BASE, fetchWithAuth, escapeHtml, formatTimestamp, showLoading, showError).
 */
(function() {
    'use strict';

    async function loadUsersList() {
        const select = document.getElementById('users-user-select');
        if (!select) return;
        const prevValue = select.value;
        select.innerHTML = '<option value="">Select a user...</option>';
        try {
            const response = await fetchWithAuth(API_BASE + '/users/active');
            const data = await response.json();
            if (data.error) {
                console.error('Error loading users:', data.error);
                return;
            }
            const users = data.users || [];
            users.forEach(function(u) {
                const opt = document.createElement('option');
                opt.value = String(u.channel_telegram_id);
                opt.textContent = u.display_name || String(u.channel_telegram_id);
                select.appendChild(opt);
            });
            const canRestore = !!prevValue && Array.from(select.options).some(function(opt) { return opt.value === prevValue; });
            if (canRestore) {
                select.value = prevValue;
                // Only trigger reload when we have an actual user selected.
                select.dispatchEvent(new Event('change'));
            } else {
                // Keep placeholder selected. Clear any stale subtab content.
                select.value = '';
                const profileContainer = document.getElementById('users-profile-container');
                const convContainer = document.getElementById('users-conversations-container');
                const acctContainer = document.getElementById('users-accounting-container');
                if (profileContainer) profileContainer.innerHTML = '<div class="loading">Select a user above</div>';
                if (convContainer) convContainer.innerHTML = '<div class="loading">Select a user above</div>';
                if (acctContainer) acctContainer.innerHTML = '<div class="loading">Select a user above</div>';
            }
        } catch (err) {
            if (err && err.message === 'unauthorized') return;
            console.error('Error loading users list:', err);
        }
    }

    window.loadUsersList = loadUsersList;

    async function loadUserProfile(userId) {
        const container = document.getElementById('users-profile-container');
        if (!container) return;
        showLoading(container, 'Loading profile...');
        try {
            const metaRes = await fetchWithAuth(API_BASE + '/users/' + encodeURIComponent(userId) + '/profile');
            const meta = await metaRes.json();
            if (meta.error || !meta.agent_config_name) {
                container.innerHTML = '<div class="placeholder-card">' + escapeHtml(meta.error || 'No conversation found for this user') + '</div>';
                return;
            }
            const profileRes = await fetchWithAuth(API_BASE + '/agents/' + encodeURIComponent(meta.agent_config_name) + '/partner-profile/' + encodeURIComponent(meta.user_id));
            const profile = await profileRes.json();
            if (profile.error) {
                container.innerHTML = '<div class="error">' + escapeHtml(profile.error) + '</div>';
                return;
            }
            const name = [profile.first_name || '', profile.last_name || ''].join(' ').trim() || profile.username || profile.telegram_id || '';
            let html = '<div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">';
            html += '<h3 style="margin-top: 0;">Profile</h3>';
            if (profile.profile_photo) {
                html += '<div style="margin-bottom: 12px;"><img src="' + escapeHtml(profile.profile_photo) + '" alt="Profile" style="max-width: 120px; max-height: 120px; border-radius: 8px;"></div>';
            }
            html += '<p><strong>Name:</strong> ' + escapeHtml(name || '—') + '</p>';
            html += '<p><strong>Username:</strong> ' + escapeHtml(profile.username || '—') + '</p>';
            html += '<p><strong>Telegram ID:</strong> ' + escapeHtml(profile.telegram_id || '—') + '</p>';
            if (profile.bio) {
                html += '<p><strong>Bio:</strong></p><p style="white-space: pre-wrap;">' + escapeHtml(profile.bio) + '</p>';
            }
            html += '</div>';
            container.innerHTML = html;
        } catch (err) {
            if (err && err.message === 'unauthorized') return;
            container.innerHTML = '<div class="error">Error loading profile: ' + escapeHtml(err.message || err) + '</div>';
        }
    }

    window.loadUserProfile = loadUserProfile;

    async function loadUserConversations(userId) {
        const container = document.getElementById('users-conversations-container');
        if (!container) return;
        showLoading(container, 'Loading conversations...');
        try {
            const response = await fetchWithAuth(API_BASE + '/users/' + encodeURIComponent(userId) + '/conversations');
            const data = await response.json();
            if (data.error) {
                container.innerHTML = '<div class="error">' + escapeHtml(data.error) + '</div>';
                return;
            }
            const days = data.days || 7;
            const conversations = data.conversations || [];
            let html = '<div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">';
            html += '<h3 style="margin-top: 0;">Conversations (last ' + days + ' days)</h3>';
            if (conversations.length === 0) {
                html += '<div class="placeholder-card">No conversations with cost in this period.</div>';
            } else {
                html += '<ul style="list-style: none; padding: 0;">';
                conversations.forEach(function(c) {
                    const cost = Number(c.total_cost || 0).toFixed(4);
                    html += '<li style="padding: 8px 0; border-bottom: 1px solid #f0f0f0;">';
                    html += '<a href="#" onclick="navigateToConversation(\'' + escapeHtml(c.agent_config_name).replace(/'/g, "\\'") + '\', \'' + escapeHtml(String(userId)).replace(/'/g, "\\'") + '\'); return false;">' + escapeHtml(c.agent_name || c.agent_config_name) + '</a>';
                    html += ' — $' + cost + '</li>';
                });
                html += '</ul>';
            }
            html += '</div>';
            container.innerHTML = html;
        } catch (err) {
            if (err && err.message === 'unauthorized') return;
            container.innerHTML = '<div class="error">Error loading conversations: ' + escapeHtml(err.message || err) + '</div>';
        }
    }

    window.loadUserConversations = loadUserConversations;

    async function loadUserAccounting(userId) {
        const container = document.getElementById('users-accounting-container');
        if (!container) return;
        showLoading(container, 'Loading accounting...');
        try {
            if (!window.agentsList || !window.telegramIdToNameMap) {
                try {
                    const agentsRes = await fetchWithAuth(API_BASE + '/agents');
                    const agentsData = await agentsRes.json();
                    if (!agentsData.error) {
                        window.agentsList = agentsData.agents || [];
                        window.telegramIdToNameMap = agentsData.telegram_id_to_name || {};
                    }
                } catch (e) {
                    window.agentsList = window.agentsList || [];
                    window.telegramIdToNameMap = window.telegramIdToNameMap || {};
                }
            }
            const response = await fetchWithAuth(API_BASE + '/users/' + encodeURIComponent(userId) + '/accounting');
            const data = await response.json();
            if (data.error) {
                container.innerHTML = '<div class="error">' + escapeHtml(data.error) + '</div>';
                return;
            }
            const days = data.days || 7;
            const totalCost = Number(data.total_cost || 0);
            const logs = data.logs || [];
            const agentsList = window.agentsList || [];
            const idToName = window.telegramIdToNameMap || {};
            let html = '<div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">';
            html += '<h3 style="margin-top: 0;">Accounting (last ' + days + ' days)</h3>';
            html += '<div style="font-size: 20px; font-weight: 600; margin-bottom: 12px;">Total: $' + totalCost.toFixed(4) + '</div>';
            if (logs.length === 0) {
                html += '<div class="placeholder-card">No cost logs for this period.</div>';
            } else {
                html += '<div style="overflow-x: auto;"><table style="width: 100%; border-collapse: collapse;">';
                html += '<thead><tr style="border-bottom: 1px solid #ddd; text-align: left;">';
                html += '<th style="padding: 8px;">Timestamp</th>';
                html += '<th style="padding: 8px;">Agent</th>';
                html += '<th style="padding: 8px;">Operation</th>';
                html += '<th style="padding: 8px;">Model</th>';
                html += '<th style="padding: 8px;">Input</th>';
                html += '<th style="padding: 8px;">Output</th>';
                html += '<th style="padding: 8px;">Cost</th></tr></thead><tbody>';
                logs.forEach(function(log) {
                    var agentId = log.agent_telegram_id;
                    var agentObj = agentsList.find(function(a) { return a.agent_id === agentId || a.agent_id === Number(agentId); });
                    var agentDisplay = agentObj ? escapeHtml(agentObj.name) : escapeHtml(String(agentId || ''));
                    var agentLink = agentObj
                        ? '<a href="#" onclick="navigateToConversation(\'' + escapeHtml(agentObj.config_name).replace(/'/g, "\\'") + '\', \'' + escapeHtml(String(userId)).replace(/'/g, "\\'") + '\'); return false;">' + agentDisplay + '</a>'
                        : agentDisplay;
                    html += '<tr style="border-bottom: 1px solid #f0f0f0;">';
                    html += '<td style="padding: 8px;">' + escapeHtml(typeof formatTimestamp === 'function' ? formatTimestamp(log.timestamp) : log.timestamp || '') + '</td>';
                    html += '<td style="padding: 8px;">' + agentLink + '</td>';
                    html += '<td style="padding: 8px;">' + escapeHtml(log.operation || '') + '</td>';
                    html += '<td style="padding: 8px;">' + escapeHtml(log.model_name || '') + '</td>';
                    html += '<td style="padding: 8px;">' + escapeHtml(String(log.input_tokens ?? '')) + '</td>';
                    html += '<td style="padding: 8px;">' + escapeHtml(String(log.output_tokens ?? '')) + '</td>';
                    html += '<td style="padding: 8px;">$' + Number(log.cost || 0).toFixed(4) + '</td></tr>';
                });
                html += '</tbody></table></div>';
            }
            html += '</div>';
            container.innerHTML = html;
        } catch (err) {
            if (err && err.message === 'unauthorized') return;
            container.innerHTML = '<div class="error">Error loading accounting: ' + escapeHtml(err.message || err) + '</div>';
        }
    }

    window.loadUserAccounting = loadUserAccounting;

    // navigateToConversation(agentConfigName, channelId) is defined in admin_console_global.js;
    // called with two args it opens the Conversation subtab (default); Costs view uses 'costs-conv'.

    document.getElementById('users-user-select')?.addEventListener('change', function() {
        var tab = document.querySelector('.tab-panel[data-tab-panel="users"] .tab-button.active[data-subtab]');
        var subtabName = tab ? tab.getAttribute('data-subtab') : 'profile-users';
        switchSubtab(subtabName);
    });
})();
