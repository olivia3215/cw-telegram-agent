// Admin Console Global - Global configuration (documents, prompts, parameters, LLMs)
// Copyright (c) 2025-2026 Cindy's World LLC and contributors
// Licensed under the MIT License. See LICENSE.md for details.

async function loadGlobalDocsConfigDirectories() {
    try {
        const response = await fetch('/admin/api/config-directories');
        const data = await response.json();
        
        const select = document.getElementById('global-docs-config-select');
        if (!select) return;
        
        select.innerHTML = '<option value="">Choose a config directory...</option>';
        data.directories.forEach(dir => {
            select.appendChild(createOption(dir.path, dir.display_path));
        });
        
        select.onchange = function() {
            globalDocsConfigDir = this.value;
            // Clear the currently loaded document when directory changes
            globalDocsCurrentFilename = '';
            globalDocsContent = '';
            document.getElementById('global-docs-select').innerHTML = '<option value="">Choose a document...</option>';
            showLoading('global-docs-editor-container', 'Select a config directory and document to edit');
            document.getElementById('delete-global-doc-btn').style.display = 'none';
            // Load docs list (will show all if no directory selected)
            loadGlobalDocsList();
        };
        
        // Load docs list on initial load (will show all if no directory selected)
        loadGlobalDocsList();
    } catch (error) {
        console.error('Error loading config directories:', error);
    }
}

// Load list of docs for global docs
async function loadGlobalDocsList() {
    try {
        // If no config directory is selected, fetch from all directories
        const url = globalDocsConfigDir 
            ? `/admin/api/docs?config_dir=${encodeURIComponent(globalDocsConfigDir)}`
            : '/admin/api/docs';
        
        const response = await fetch(url);
        const data = await response.json();
        
        const select = document.getElementById('global-docs-select');
        if (!select) return;
        
        select.innerHTML = '<option value="">Choose a document...</option>';
        
        // Get config directory display paths for labeling
        let configDirDisplayMap = {};
        if (!globalDocsConfigDir) {
            const configDirsResponse = await fetch('/admin/api/config-directories');
            const configDirsData = await configDirsResponse.json();
            configDirDisplayMap = {};
            configDirsData.directories.forEach(dir => {
                configDirDisplayMap[dir.path] = dir.display_path;
            });
        }
        
        data.docs.forEach(doc => {
            const option = document.createElement('option');
            option.value = doc.filename;
            // Store config_dir in data attribute
            option.dataset.configDir = doc.config_dir || globalDocsConfigDir;
            
            // Display filename with source directory if showing all directories
            if (!globalDocsConfigDir && doc.config_dir) {
                const displayPath = configDirDisplayMap[doc.config_dir] || doc.config_dir;
                option.textContent = `${doc.filename} (from ${displayPath})`;
            } else {
                option.textContent = doc.filename;
            }
            select.appendChild(option);
        });
        
        select.onchange = function() {
            if (this.value) {
                const selectedOption = this.options[this.selectedIndex];
                const docConfigDir = selectedOption.dataset.configDir;
                
                // Always update config directory to match the selected item's directory
                if (docConfigDir && docConfigDir !== globalDocsConfigDir) {
                    const configSelect = document.getElementById('global-docs-config-select');
                    if (configSelect) {
                        configSelect.value = docConfigDir;
                        globalDocsConfigDir = docConfigDir;
                    }
                }
                
                loadGlobalDoc(this.value);
            } else {
                showLoading('global-docs-editor-container', 'Select a document to edit');
                document.getElementById('delete-global-doc-btn').style.display = 'none';
            }
        };
    } catch (error) {
        console.error('Error loading docs list:', error);
    }
}

// Load a specific global doc
async function loadGlobalDoc(filename) {
    if (!globalDocsConfigDir || !filename) return;
    
    try {
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(globalDocsConfigDir)}`);
        if (!response.ok) {
            throw new Error('Failed to load doc');
        }
        
        const data = await response.json();
        globalDocsCurrentFilename = filename;
        globalDocsContent = data.content || '';
        
        renderGlobalDocEditor(data.content || '', filename, globalDocsConfigDir);
        document.getElementById('delete-global-doc-btn').style.display = 'inline-block';
    } catch (error) {
        console.error('Error loading doc:', error);
        document.getElementById('global-docs-editor-container').innerHTML = '<div class="error">Error loading document</div>';
    }
}

// Render global doc editor
function renderGlobalDocEditor(content, filename, configDir) {
    const container = document.getElementById('global-docs-editor-container');
    container.innerHTML = `
        <div style="background: white; padding: 16px; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Filename:</label>
                <input type="text" id="global-docs-filename" value="${escapeHtml(filename)}" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" />
                <div id="global-docs-filename-status" style="margin-top: 4px; font-size: 12px; color: #666;"></div>
            </div>
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Move to:</label>
                <select id="global-docs-move-destination" style="padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; width: 100%;">
                    <option value="">Move to...</option>
                </select>
            </div>
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Content:</label>
                <textarea id="global-docs-content" style="width: 100%; min-height: 400px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 14px;">${escapeHtml(content)}</textarea>
                <div id="global-docs-save-status" style="margin-top: 4px; font-size: 12px; color: #666;"></div>
            </div>
        </div>
    `;
    
    // Setup handlers
    // Capture all state at creation time to avoid race conditions
    const filenameInput = document.getElementById('global-docs-filename');
    filenameInput.oninput = debounceGlobalDocsFilename(configDir, filename);
    
    // Setup content change handler with auto-save
    const contentTextarea = document.getElementById('global-docs-content');
    contentTextarea.oninput = debounceGlobalDocsSave(configDir, filename);
    
    // Setup move destination change handler
    const moveSelect = document.getElementById('global-docs-move-destination');
    moveSelect.onchange = function() {
        if (this.value) {
            moveGlobalDoc();
        }
    };
    
    // Load all destinations for move dropdown
    loadAllDestinationsForMove('global-docs-move-destination', globalDocsConfigDir, null);
}

// Debounced save for global docs
// Captures configDir and filename to avoid race conditions
function debounceGlobalDocsSave(configDir, filename) {
    return function() {
        if (globalDocsSaveTimeout) {
            clearTimeout(globalDocsSaveTimeout);
        }
        
        const statusEl = document.getElementById('global-docs-save-status');
        if (statusEl) statusEl.textContent = 'Typing...';
        
        globalDocsSaveTimeout = setTimeout(async () => {
            // Verify we're still editing the same document (user may have switched)
            if (globalDocsConfigDir !== configDir || globalDocsCurrentFilename !== filename) {
                // User switched documents, don't save
                if (statusEl) statusEl.textContent = '';
                return;
            }
            
            const contentTextarea = document.getElementById('global-docs-content');
            if (!contentTextarea) return;
            
            const newContent = contentTextarea.value;
            if (newContent === globalDocsContent) {
                if (statusEl) statusEl.textContent = 'Saved';
                return;
            }
            
            if (statusEl) statusEl.textContent = 'Saving...';
            
            try {
                // Use captured values instead of reading from globals
                const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: newContent })
                });
                
                if (response.ok) {
                    globalDocsContent = newContent;
                    if (statusEl) statusEl.textContent = 'Saved';
                } else {
                    throw new Error('Save failed');
                }
            } catch (error) {
                console.error('Error saving doc:', error);
                if (statusEl) statusEl.textContent = 'Error saving';
            }
        }, 1000);
    };
}

// Debounced filename change for global docs
// Captures configDir and filename to avoid race conditions
function debounceGlobalDocsFilename(configDir, filename) {
    return function() {
        if (globalDocsFilenameTimeout) {
            clearTimeout(globalDocsFilenameTimeout);
        }
        
        const filenameInput = document.getElementById('global-docs-filename');
        if (!filenameInput) return;
        
        const newFilename = filenameInput.value.trim();
        const statusEl = document.getElementById('global-docs-filename-status');
        
        // Verify we're still editing the same document (user may have switched)
        if (globalDocsConfigDir !== configDir || globalDocsCurrentFilename !== filename) {
            // User switched documents, don't rename
            if (statusEl) statusEl.textContent = '';
            return;
        }
        
        if (!newFilename || newFilename === filename) {
            if (statusEl) statusEl.textContent = '';
            return;
        }
        
        if (!newFilename.endsWith('.md')) {
            if (statusEl) statusEl.textContent = 'Filename must end with .md';
            return;
        }
        
        if (statusEl) statusEl.textContent = 'Renaming...';
        
        globalDocsFilenameTimeout = setTimeout(async () => {
            // Double-check we're still editing the same document before renaming
            if (globalDocsConfigDir !== configDir || globalDocsCurrentFilename !== filename) {
                // User switched documents, don't rename
                if (statusEl) statusEl.textContent = '';
                return;
            }
            
            try {
                // Use captured values instead of reading from globals
                const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}/rename?config_dir=${encodeURIComponent(configDir)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_filename: newFilename })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    const newFilename = data.filename;
                    globalDocsCurrentFilename = newFilename;
                    
                    // Update handlers with new filename to allow further saves/renames
                    const contentTextarea = document.getElementById('global-docs-content');
                    if (contentTextarea) {
                        contentTextarea.oninput = debounceGlobalDocsSave(configDir, newFilename);
                    }
                    const filenameInput = document.getElementById('global-docs-filename');
                    if (filenameInput) {
                        filenameInput.oninput = debounceGlobalDocsFilename(configDir, newFilename);
                    }

                    // Refresh the document list dropdown
                    loadGlobalDocsList().then(() => {
                        document.getElementById('global-docs-select').value = newFilename;
                    });
                    if (statusEl) statusEl.textContent = 'Renamed';
                    setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000);
                } else {
                    throw new Error('Rename failed');
                }
            } catch (error) {
                console.error('Error renaming doc:', error);
                if (statusEl) statusEl.textContent = 'Error renaming';
            }
        }, 1000);
    };
}

// Create new global doc
async function createGlobalDoc() {
    if (!globalDocsConfigDir) {
        alert('Please select a config directory first');
        return;
    }

    // Find a unique filename
    let filename = 'Untitled.md';
    let counter = 1;
    try {
        // Load existing docs to check for conflicts
        const response = await fetch(`/admin/api/docs?config_dir=${encodeURIComponent(globalDocsConfigDir)}`);
        const data = await response.json();
        const existingFilenames = new Set(data.docs.map(doc => doc.filename));
        
        while (existingFilenames.has(filename)) {
            filename = `Untitled-${counter}.md`;
            counter++;
        }
    } catch (error) {
        console.error('Error checking existing docs:', error);
        // Continue with Untitled.md, will fail if it exists
    }

    try {
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(globalDocsConfigDir)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: '' })
        });

        if (response.ok) {
            loadGlobalDocsList().then(() => {
                document.getElementById('global-docs-select').value = filename;
            });
            loadGlobalDoc(filename);
        } else {
            throw new Error('Create failed');
        }
    } catch (error) {
        console.error('Error creating doc:', error);
        alert('Error creating document');
    }
}

// Delete global doc
async function deleteGlobalDoc() {
    if (!globalDocsCurrentFilename || !confirm(`Delete ${globalDocsCurrentFilename}?`)) {
        return;
    }
    
    try {
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(globalDocsCurrentFilename)}?config_dir=${encodeURIComponent(globalDocsConfigDir)}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            loadGlobalDocsList();
            document.getElementById('global-docs-select').value = '';
            document.getElementById('global-docs-editor-container').innerHTML = '<div class="loading">Select a document to edit</div>';
            document.getElementById('delete-global-doc-btn').style.display = 'none';
            globalDocsCurrentFilename = '';
        } else {
            throw new Error('Delete failed');
        }
    } catch (error) {
        console.error('Error deleting doc:', error);
        alert('Error deleting document');
    }
}

// Move global doc
async function moveGlobalDoc() {
    if (!globalDocsCurrentFilename) return;

    const destinationValue = document.getElementById('global-docs-move-destination').value;
    if (!destinationValue) {
        return;
    }

    // Parse destination value: "global|config_dir" or "agent|config_dir|agent_config_name"
    let toConfigDir, toAgentConfigName;
    if (destinationValue.startsWith('global|')) {
        toConfigDir = destinationValue.substring(7); // Remove "global|" prefix
        toAgentConfigName = null;
    } else if (destinationValue.startsWith('agent|')) {
        const parts = destinationValue.substring(6).split('|'); // Remove "agent|" prefix
        toConfigDir = parts[0] || '';
        toAgentConfigName = parts[1] || '';
    } else {
        return;
    }

    // Check if it's the current location
    if (!toAgentConfigName && toConfigDir === globalDocsConfigDir) {
        return; // Already at this location
    }

    if (!toConfigDir) {
        alert('Invalid destination');
        return;
    }

    if (destinationValue.startsWith('agent|') && !toAgentConfigName) {
        alert('Invalid agent destination');
        return;
    }

    if (!confirm(`Move ${globalDocsCurrentFilename} to ${destinationValue.startsWith('agent|') ? 'agent docs' : 'global docs'}?`)) {
        // Reset dropdown to current location
        loadAllDestinationsForMove('global-docs-move-destination', globalDocsConfigDir, null);
        return;
    }

    try {
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(globalDocsCurrentFilename)}/move?from_config_dir=${encodeURIComponent(globalDocsConfigDir)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                to_config_dir: toConfigDir,
                to_agent_config_name: toAgentConfigName
            })
        });

        if (response.ok) {
            loadGlobalDocsList();
            document.getElementById('global-docs-select').value = '';
            document.getElementById('global-docs-editor-container').innerHTML = '<div class="loading">Select a document to edit</div>';
            document.getElementById('delete-global-doc-btn').style.display = 'none';
            globalDocsCurrentFilename = '';
            alert('Document moved successfully');
        } else {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.error || 'Move failed');
        }
    } catch (error) {
        console.error('Error moving doc:', error);
        alert(`Error moving document: ${error.message}`);
        // Reset dropdown to current location
        loadAllDestinationsForMove('global-docs-move-destination', globalDocsConfigDir, null);
    }
}

// Load agent docs list
async function loadAgentDocs(agentConfigName) {
    if (!agentConfigName) {
        document.getElementById('agent-docs-select').innerHTML = '<option value="">Choose a document...</option>';
        showLoading('agent-docs-editor-container', 'Select an agent and document to edit');
        return;
    }
    
    agentDocsCurrentAgent = agentConfigName;
    
    try {
        // Get agent to find its config directory
        const agentsResponse = await fetch('/admin/api/agents');
        const agentsData = await agentsResponse.json();
        const agent = agentsData.agents.find(a => a.config_name === agentConfigName);
        
        if (!agent) {
            throw new Error('Agent not found');
        }
        
        // Get config directory from agent (should be stored in agent object)
        // For now, we'll need to search through config directories
        // Actually, the agent should have config_directory property
        // Get config directory from agent or use first available
        let configDir = agent.config_directory;
        if (!configDir) {
            const configDirsResponse = await fetch('/admin/api/config-directories');
            const configDirsData = await configDirsResponse.json();
            configDir = configDirsData.directories[0]?.path || '';
        }
        
        const response = await fetch(`/admin/api/docs?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentConfigName)}`);
        const data = await response.json();
        
        const select = document.getElementById('agent-docs-select');
        if (!select) return;
        
        select.innerHTML = '<option value="">Choose a document...</option>';
        data.docs.forEach(doc => {
            select.appendChild(createOption(doc.filename, doc.filename));
        });
        
        select.onchange = function() {
            if (this.value) {
                loadAgentDoc(this.value, configDir);
            } else {
                showLoading('agent-docs-editor-container', 'Select a document to edit');
                document.getElementById('delete-agent-doc-btn').style.display = 'none';
            }
        };
    } catch (error) {
        console.error('Error loading agent docs list:', error);
    }
}

// Load a specific agent doc
async function loadAgentDoc(filename, configDir) {
    if (!agentDocsCurrentAgent || !filename) return;
    
    try {
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentDocsCurrentAgent)}`);
        if (!response.ok) {
            throw new Error('Failed to load doc');
        }
        
        const data = await response.json();
        agentDocsCurrentFilename = filename;
        agentDocsContent = data.content || '';
        
        renderAgentDocEditor(data.content || '', filename, configDir);
        document.getElementById('delete-agent-doc-btn').style.display = 'inline-block';
    } catch (error) {
        console.error('Error loading agent doc:', error);
        document.getElementById('agent-docs-editor-container').innerHTML = '<div class="error">Error loading document</div>';
    }
}

// Render agent doc editor (similar to global but with agent context)
function renderAgentDocEditor(content, filename, configDir) {
    const container = document.getElementById('agent-docs-editor-container');
    container.innerHTML = `
        <div style="background: white; padding: 16px; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Filename:</label>
                <input type="text" id="agent-docs-filename" value="${escapeHtml(filename)}" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" />
                <div id="agent-docs-filename-status" style="margin-top: 4px; font-size: 12px; color: #666;"></div>
            </div>
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Move to:</label>
                <select id="agent-docs-move-destination" style="padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; width: 100%;">
                    <option value="">Move to...</option>
                </select>
            </div>
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Content:</label>
                <textarea id="agent-docs-content" style="width: 100%; min-height: 400px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 14px;">${escapeHtml(content)}</textarea>
                <div id="agent-docs-save-status" style="margin-top: 4px; font-size: 12px; color: #666;"></div>
            </div>
        </div>
    `;
    
    // Setup handlers (similar to global docs)
    // Capture all state at creation time to avoid race conditions
    const filenameInput = document.getElementById('agent-docs-filename');
    filenameInput.oninput = debounceAgentDocsFilename(configDir, agentDocsCurrentAgent, filename);

    const contentTextarea = document.getElementById('agent-docs-content');
    contentTextarea.oninput = debounceAgentDocsSave(configDir, agentDocsCurrentAgent, filename);

    // Setup move destination change handler
    const moveSelect = document.getElementById('agent-docs-move-destination');
    moveSelect.onchange = function() {
        if (this.value) {
            moveAgentDoc(configDir);
        }
    };

    // Load all destinations for move dropdown
    loadAllDestinationsForMove('agent-docs-move-destination', configDir, agentDocsCurrentAgent);
}

// Debounced save for agent docs
// Captures configDir, agentConfigName, and filename to avoid race conditions
function debounceAgentDocsSave(configDir, agentConfigName, filename) {
    return function() {
        if (agentDocsSaveTimeout) {
            clearTimeout(agentDocsSaveTimeout);
        }
        
        const statusEl = document.getElementById('agent-docs-save-status');
        if (statusEl) statusEl.textContent = 'Typing...';
        
        agentDocsSaveTimeout = setTimeout(async () => {
            // Verify we're still editing the same document (user may have switched)
            if (agentDocsCurrentAgent !== agentConfigName || agentDocsCurrentFilename !== filename) {
                // User switched agents/documents, don't save
                if (statusEl) statusEl.textContent = '';
                return;
            }
            
            const contentTextarea = document.getElementById('agent-docs-content');
            if (!contentTextarea) return;
            
            const newContent = contentTextarea.value;
            if (newContent === agentDocsContent) {
                if (statusEl) statusEl.textContent = 'Saved';
                return;
            }
            
            if (statusEl) statusEl.textContent = 'Saving...';
            
            try {
                // Use captured values instead of reading from globals
                const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentConfigName)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: newContent })
                });
                
                if (response.ok) {
                    agentDocsContent = newContent;
                    if (statusEl) statusEl.textContent = 'Saved';
                } else {
                    throw new Error('Save failed');
                }
            } catch (error) {
                console.error('Error saving agent doc:', error);
                if (statusEl) statusEl.textContent = 'Error saving';
            }
        }, 1000);
    };
}

// Debounced filename change for agent docs
// Captures configDir, agentConfigName, and filename to avoid race conditions
function debounceAgentDocsFilename(configDir, agentConfigName, filename) {
    return function() {
        if (agentDocsFilenameTimeout) {
            clearTimeout(agentDocsFilenameTimeout);
        }
        
        const filenameInput = document.getElementById('agent-docs-filename');
        if (!filenameInput) return;
        
        const newFilename = filenameInput.value.trim();
        const statusEl = document.getElementById('agent-docs-filename-status');
        
        // Verify we're still editing the same document (user may have switched)
        if (agentDocsCurrentAgent !== agentConfigName || agentDocsCurrentFilename !== filename) {
            // User switched agents/documents, don't rename
            if (statusEl) statusEl.textContent = '';
            return;
        }
        
        if (!newFilename || newFilename === filename) {
            if (statusEl) statusEl.textContent = '';
            return;
        }
        
        if (!newFilename.endsWith('.md')) {
            if (statusEl) statusEl.textContent = 'Filename must end with .md';
            return;
        }
        
        if (statusEl) statusEl.textContent = 'Renaming...';
        
        agentDocsFilenameTimeout = setTimeout(async () => {
            // Double-check we're still editing the same document before renaming
            if (agentDocsCurrentAgent !== agentConfigName || agentDocsCurrentFilename !== filename) {
                // User switched agents/documents, don't rename
                if (statusEl) statusEl.textContent = '';
                return;
            }
            
            try {
                // Use captured values instead of reading from globals
                const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}/rename?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentConfigName)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_filename: newFilename })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    const newFilename = data.filename;
                    agentDocsCurrentFilename = newFilename;
                    
                    // Update handlers with new filename to allow further saves/renames
                    const contentTextarea = document.getElementById('agent-docs-content');
                    if (contentTextarea) {
                        contentTextarea.oninput = debounceAgentDocsSave(configDir, agentConfigName, newFilename);
                    }
                    const filenameInput = document.getElementById('agent-docs-filename');
                    if (filenameInput) {
                        filenameInput.oninput = debounceAgentDocsFilename(configDir, agentConfigName, newFilename);
                    }

                    // Refresh the document list dropdown
                    loadAgentDocs(agentConfigName).then(() => {
                        document.getElementById('agent-docs-select').value = newFilename;
                    });
                    if (statusEl) statusEl.textContent = 'Renamed';
                    setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000);
                } else {
                    throw new Error('Rename failed');
                }
            } catch (error) {
                console.error('Error renaming agent doc:', error);
                if (statusEl) statusEl.textContent = 'Error renaming';
            }
        }, 1000);
    };
}

// Create new agent doc
async function createAgentDoc() {
    const agentSelect = document.getElementById('agents-agent-select');
    const agentConfigName = agentSelect ? stripAsterisk(agentSelect.value) : null;

    if (!agentConfigName) {
        alert('Please select an agent first');
        return;
    }

    // Find a unique filename
    let filename = 'Untitled.md';
    let counter = 1;
    try {
        const agentsResponse = await fetch('/admin/api/agents');
        const agentsData = await agentsResponse.json();
        const agent = agentsData.agents.find(a => a.config_name === agentConfigName);
        
        // Get config directory from agent or use first available
        let configDir = agent?.config_directory;
        if (!configDir) {
            const configDirsResponse = await fetch('/admin/api/config-directories');
            const configDirsData = await configDirsResponse.json();
            configDir = configDirsData.directories[0]?.path || '';
        }

        // Load existing docs to check for conflicts
        const docsResponse = await fetch(`/admin/api/docs?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentConfigName)}`);
        const docsData = await docsResponse.json();
        const existingFilenames = new Set(docsData.docs.map(doc => doc.filename));
        
        while (existingFilenames.has(filename)) {
            filename = `Untitled-${counter}.md`;
            counter++;
        }

        const response = await fetch(`/admin/api/docs/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentConfigName)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: '' })
        });

        if (response.ok) {
            loadAgentDocs(agentConfigName).then(() => {
                document.getElementById('agent-docs-select').value = filename;
            });
            loadAgentDoc(filename, configDir);
        } else {
            throw new Error('Create failed');
        }
    } catch (error) {
        console.error('Error creating agent doc:', error);
        alert('Error creating document');
    }
}

// Delete agent doc
async function deleteAgentDoc() {
    if (!agentDocsCurrentFilename || !agentDocsCurrentAgent) return;
    
    if (!confirm(`Delete ${agentDocsCurrentFilename}?`)) {
        return;
    }
    
    try {
        const agentsResponse = await fetch('/admin/api/agents');
        const agentsData = await agentsResponse.json();
        const agent = agentsData.agents.find(a => a.config_name === agentDocsCurrentAgent);
        
        // Get config directory from agent or use first available
        let configDir = agent?.config_directory;
        if (!configDir) {
            const configDirsResponse = await fetch('/admin/api/config-directories');
            const configDirsData = await configDirsResponse.json();
            configDir = configDirsData.directories[0]?.path || '';
        }
        
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(agentDocsCurrentFilename)}?config_dir=${encodeURIComponent(configDir)}&agent_config_name=${encodeURIComponent(agentDocsCurrentAgent)}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            loadAgentDocs(agentDocsCurrentAgent);
            document.getElementById('agent-docs-select').value = '';
            document.getElementById('agent-docs-editor-container').innerHTML = '<div class="loading">Select a document to edit</div>';
            document.getElementById('delete-agent-doc-btn').style.display = 'none';
            agentDocsCurrentFilename = '';
        } else {
            throw new Error('Delete failed');
        }
    } catch (error) {
        console.error('Error deleting agent doc:', error);
        alert('Error deleting document');
    }
}

// Move agent doc
async function moveAgentDoc(fromConfigDir) {
    if (!agentDocsCurrentFilename || !agentDocsCurrentAgent) return;

    const destinationValue = document.getElementById('agent-docs-move-destination').value;
    if (!destinationValue) {
        return;
    }

    // Parse destination value: "global|config_dir" or "agent|config_dir|agent_config_name"
    let toConfigDir, toAgentConfigName;
    if (destinationValue.startsWith('global|')) {
        toConfigDir = destinationValue.substring(7); // Remove "global|" prefix
        toAgentConfigName = null;
    } else if (destinationValue.startsWith('agent|')) {
        const parts = destinationValue.substring(6).split('|'); // Remove "agent|" prefix
        toConfigDir = parts[0] || '';
        toAgentConfigName = parts[1] || '';
    } else {
        return;
    }

    // Check if it's the current location
    if (toAgentConfigName === agentDocsCurrentAgent && 
        (toConfigDir === fromConfigDir || (!toConfigDir && !fromConfigDir))) {
        return; // Already at this location
    }

    if (!toConfigDir) {
        alert('Invalid destination');
        return;
    }

    if (destinationValue.startsWith('agent|') && !toAgentConfigName) {
        alert('Invalid agent destination');
        return;
    }

    if (!confirm(`Move ${agentDocsCurrentFilename} to ${destinationValue.startsWith('agent|') ? 'agent docs' : 'global docs'}?`)) {
        // Reset dropdown to current location
        loadAllDestinationsForMove('agent-docs-move-destination', fromConfigDir, agentDocsCurrentAgent);
        return;
    }

    try {
        const response = await fetch(`/admin/api/docs/${encodeURIComponent(agentDocsCurrentFilename)}/move?from_config_dir=${encodeURIComponent(fromConfigDir)}&from_agent_config_name=${encodeURIComponent(agentDocsCurrentAgent)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                to_config_dir: toConfigDir,
                to_agent_config_name: toAgentConfigName
            })
        });

        if (response.ok) {
            loadAgentDocs(agentDocsCurrentAgent);
            document.getElementById('agent-docs-select').value = '';
            document.getElementById('agent-docs-editor-container').innerHTML = '<div class="loading">Select a document to edit</div>';
            document.getElementById('delete-agent-doc-btn').style.display = 'none';
            agentDocsCurrentFilename = '';
            alert('Document moved successfully');
        } else {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.error || 'Move failed');
        }
    } catch (error) {
        console.error('Error moving agent doc:', error);
        alert(`Error moving document: ${error.message}`);
        // Reset dropdown to current location
        loadAllDestinationsForMove('agent-docs-move-destination', fromConfigDir, agentDocsCurrentAgent);
    }
}

// Helper functions
// ===== Role Prompts Management Functions =====

// Global prompts state
let globalPromptsConfigDir = '';
let globalPromptsCurrentFilename = '';
// globalPromptsSaveTimeout and globalPromptsFilenameTimeout are declared in admin_console_core.js
let globalPromptsContent = '';

// Load config directories for global prompts
async function loadGlobalPromptsConfigDirectories() {
    try {
        const response = await fetch('/admin/api/config-directories');
        const data = await response.json();
        
        const select = document.getElementById('global-prompts-config-select');
        if (!select) return;
        
        select.innerHTML = '<option value="">Choose a config directory...</option>';
        data.directories.forEach(dir => {
            select.appendChild(createOption(dir.path, dir.display_path));
        });
        
        select.onchange = function() {
            globalPromptsConfigDir = this.value;
            // Clear the currently loaded prompt when directory changes
            globalPromptsCurrentFilename = '';
            globalPromptsContent = '';
            document.getElementById('global-prompts-select').innerHTML = '<option value="">Choose a role prompt...</option>';
            showLoading('global-prompts-editor-container', 'Select a config directory and role prompt to edit');
            document.getElementById('delete-global-prompt-btn').style.display = 'none';
            // Load prompts list (will show all if no directory selected)
            loadGlobalPromptsList();
        };
        
        // Load prompts list on initial load (will show all if no directory selected)
        loadGlobalPromptsList();
    } catch (error) {
        console.error('Error loading config directories:', error);
    }
}

// Load list of prompts for global prompts
async function loadGlobalPromptsList() {
    try {
        // If no config directory is selected, fetch from all directories
        const url = globalPromptsConfigDir 
            ? `/admin/api/prompts?config_dir=${encodeURIComponent(globalPromptsConfigDir)}`
            : '/admin/api/prompts';
        
        const response = await fetch(url);
        const data = await response.json();
        
        const select = document.getElementById('global-prompts-select');
        if (!select) return;
        
        select.innerHTML = '<option value="">Choose a role prompt...</option>';
        
        // Get config directory display paths for labeling
        let configDirDisplayMap = {};
        if (!globalPromptsConfigDir) {
            const configDirsResponse = await fetch('/admin/api/config-directories');
            const configDirsData = await configDirsResponse.json();
            configDirDisplayMap = {};
            configDirsData.directories.forEach(dir => {
                configDirDisplayMap[dir.path] = dir.display_path;
            });
        }
        
        data.prompts.forEach(prompt => {
            const option = document.createElement('option');
            option.value = prompt.filename;
            // Store config_dir in data attribute
            option.dataset.configDir = prompt.config_dir || globalPromptsConfigDir;
            
            // Display filename with source directory if showing all directories
            if (!globalPromptsConfigDir && prompt.config_dir) {
                const displayPath = configDirDisplayMap[prompt.config_dir] || prompt.config_dir;
                option.textContent = `${prompt.filename} (from ${displayPath})`;
            } else {
                option.textContent = prompt.filename;
            }
            select.appendChild(option);
        });
        
        select.onchange = function() {
            if (this.value) {
                const selectedOption = this.options[this.selectedIndex];
                const promptConfigDir = selectedOption.dataset.configDir;
                
                // Always update config directory to match the selected item's directory
                if (promptConfigDir && promptConfigDir !== globalPromptsConfigDir) {
                    const configSelect = document.getElementById('global-prompts-config-select');
                    if (configSelect) {
                        configSelect.value = promptConfigDir;
                        globalPromptsConfigDir = promptConfigDir;
                    }
                }
                
                loadGlobalPrompt(this.value);
            } else {
                showLoading('global-prompts-editor-container', 'Select a role prompt to edit');
                document.getElementById('delete-global-prompt-btn').style.display = 'none';
            }
        };
    } catch (error) {
        console.error('Error loading prompts list:', error);
    }
}

// Load a specific global prompt
async function loadGlobalPrompt(filename) {
    if (!globalPromptsConfigDir || !filename) return;
    
    // Capture configDir at the start to avoid race conditions if dropdown changes during fetch
    const configDir = globalPromptsConfigDir;
    
    try {
        const response = await fetch(`/admin/api/prompts/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}`);
        if (!response.ok) {
            throw new Error('Failed to load prompt');
        }
        
        const data = await response.json();
        globalPromptsCurrentFilename = filename;
        globalPromptsContent = data.content || '';
        
        renderGlobalPromptEditor(data.content || '', filename, configDir);
        document.getElementById('delete-global-prompt-btn').style.display = 'inline-block';
    } catch (error) {
        console.error('Error loading prompt:', error);
        document.getElementById('global-prompts-editor-container').innerHTML = '<div class="error">Error loading role prompt</div>';
        globalPromptsCurrentFilename = '';
        document.getElementById('delete-global-prompt-btn').style.display = 'none';
    }
}

// Render global prompt editor
function renderGlobalPromptEditor(content, filename, configDir) {
    const container = document.getElementById('global-prompts-editor-container');
    container.innerHTML = `
        <div style="background: white; padding: 16px; border-radius: 4px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Filename:</label>
                <input type="text" id="global-prompts-filename" value="${escapeHtml(filename)}" style="width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;" />
                <div id="global-prompts-filename-status" style="margin-top: 4px; font-size: 12px; color: #666;"></div>
            </div>
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Move to:</label>
                <select id="global-prompts-move-destination" style="padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; width: 100%;">
                    <option value="">Move to...</option>
                </select>
            </div>
            <div style="margin-bottom: 12px;">
                <label style="display: block; margin-bottom: 4px; font-weight: bold;">Content:</label>
                <textarea id="global-prompts-content" style="width: 100%; min-height: 400px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: monospace; font-size: 14px;">${escapeHtml(content)}</textarea>
                <div id="global-prompts-save-status" style="margin-top: 4px; font-size: 12px; color: #666;"></div>
            </div>
        </div>
    `;
    
    // Setup handlers
    // Capture all state at creation time to avoid race conditions
    const filenameInput = document.getElementById('global-prompts-filename');
    filenameInput.oninput = debounceGlobalPromptsFilename(configDir, filename);
    
    // Setup content change handler with auto-save
    const contentTextarea = document.getElementById('global-prompts-content');
    contentTextarea.oninput = debounceGlobalPromptsSave(configDir, filename);
    
    // Setup move destination change handler
    const moveSelect = document.getElementById('global-prompts-move-destination');
    moveSelect.onchange = function() {
        if (this.value) {
            moveGlobalPrompt();
        }
    };
    
    // Load all destinations for move dropdown (only config directories, not agents)
    loadConfigDirectoriesForMovePrompts('global-prompts-move-destination', globalPromptsConfigDir);
}

// Debounced save for global prompts
// Captures configDir and filename to avoid race conditions
function debounceGlobalPromptsSave(configDir, filename) {
    return function() {
        if (globalPromptsSaveTimeout) {
            clearTimeout(globalPromptsSaveTimeout);
        }
        
        const statusEl = document.getElementById('global-prompts-save-status');
        if (statusEl) statusEl.textContent = 'Typing...';
        
        globalPromptsSaveTimeout = setTimeout(async () => {
            // Verify we're still editing the same prompt (user may have switched)
            if (globalPromptsConfigDir !== configDir || globalPromptsCurrentFilename !== filename) {
                // User switched prompts, don't save
                if (statusEl) statusEl.textContent = '';
                return;
            }
            
            const contentTextarea = document.getElementById('global-prompts-content');
            if (!contentTextarea) return;
            
            const newContent = contentTextarea.value;
            if (newContent === globalPromptsContent) {
                if (statusEl) statusEl.textContent = 'Saved';
                return;
            }
            
            if (statusEl) statusEl.textContent = 'Saving...';
            
            try {
                // Use captured values instead of reading from globals
                const response = await fetch(`/admin/api/prompts/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content: newContent })
                });
                
                if (response.ok) {
                    globalPromptsContent = newContent;
                    if (statusEl) statusEl.textContent = 'Saved';
                } else {
                    throw new Error('Save failed');
                }
            } catch (error) {
                console.error('Error saving prompt:', error);
                if (statusEl) statusEl.textContent = 'Error saving';
            }
        }, 1000);
    };
}

// Immediately save any pending content changes (used before rename/move operations)
// Returns a promise that resolves when the save is complete (or immediately if no save needed)
async function savePendingGlobalPromptsContent(configDir, filename) {
    const contentTextarea = document.getElementById('global-prompts-content');
    if (!contentTextarea) {
        return; // No textarea, nothing to save
    }
    
    const newContent = contentTextarea.value;
    if (newContent === globalPromptsContent) {
        return; // No changes, nothing to save
    }
    
    // Verify we're still editing the same prompt (user may have switched)
    if (globalPromptsConfigDir !== configDir || globalPromptsCurrentFilename !== filename) {
        return; // User switched prompts, don't save
    }
    
    const statusEl = document.getElementById('global-prompts-save-status');
    if (statusEl) statusEl.textContent = 'Saving...';
    
    try {
        const response = await fetch(`/admin/api/prompts/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(configDir)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: newContent })
        });
        
        if (response.ok) {
            globalPromptsContent = newContent;
            if (statusEl) statusEl.textContent = 'Saved';
        } else {
            throw new Error('Save failed');
        }
    } catch (error) {
        console.error('Error saving prompt:', error);
        if (statusEl) statusEl.textContent = 'Error saving';
        throw error; // Re-throw so caller knows save failed
    }
}

// Debounced filename change for global prompts
// Captures configDir and filename to avoid race conditions
function debounceGlobalPromptsFilename(configDir, filename) {
    return function() {
        if (globalPromptsFilenameTimeout) {
            clearTimeout(globalPromptsFilenameTimeout);
        }
        
        const filenameInput = document.getElementById('global-prompts-filename');
        if (!filenameInput) return;
        
        const newFilename = filenameInput.value.trim();
        const statusEl = document.getElementById('global-prompts-filename-status');
        
        // Verify we're still editing the same prompt (user may have switched)
        if (globalPromptsConfigDir !== configDir || globalPromptsCurrentFilename !== filename) {
            // User switched prompts, don't rename
            if (statusEl) statusEl.textContent = '';
            return;
        }
        
        if (!newFilename || newFilename === filename) {
            if (statusEl) statusEl.textContent = '';
            return;
        }
        
        // Validate filename format
        if (!newFilename.endsWith('.md')) {
            if (statusEl) statusEl.textContent = 'Filename must end with .md';
            return;
        }
        
        const nameWithoutExt = newFilename.slice(0, -3);
        if (nameWithoutExt.length > 50) {
            if (statusEl) statusEl.textContent = 'Filename (without .md) must be 50 characters or less';
            return;
        }
        
        if (!/^[a-zA-Z0-9_\- ]+$/.test(nameWithoutExt)) {
            if (statusEl) statusEl.textContent = 'Filename can only contain letters, numbers, underscores, dashes, and spaces';
            return;
        }
        
        // Ensure at least one non-space character exists (prevent filenames with only spaces)
        if (!/[a-zA-Z0-9_\-]/.test(nameWithoutExt)) {
            if (statusEl) statusEl.textContent = 'Filename must contain at least one non-space character';
            return;
        }
        
        if (statusEl) statusEl.textContent = 'Renaming...';
        
        globalPromptsFilenameTimeout = setTimeout(async () => {
            // Double-check we're still editing the same prompt before renaming
            if (globalPromptsConfigDir !== configDir || globalPromptsCurrentFilename !== filename) {
                // User switched prompts, don't rename
                if (statusEl) statusEl.textContent = '';
                return;
            }
            
            try {
                // Save any pending content changes before renaming (with old filename)
                await savePendingGlobalPromptsContent(configDir, filename);
                
                // Use captured values instead of reading from globals
                const response = await fetch(`/admin/api/prompts/${encodeURIComponent(filename)}/rename?config_dir=${encodeURIComponent(configDir)}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_filename: newFilename })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    const newFilename = data.filename;
                    
                    // Check again after fetch completes: ensure we're still editing the same prompt
                    // If user switched prompts while rename was in flight, don't update globals/handlers
                    if (globalPromptsConfigDir !== configDir || globalPromptsCurrentFilename !== filename) {
                        // User switched prompts, don't update globals or handlers
                        if (statusEl) statusEl.textContent = '';
                        return;
                    }
                    
                    globalPromptsCurrentFilename = newFilename;
                    
                    // Clear any pending save timeout to prevent saving with old filename
                    if (globalPromptsSaveTimeout) {
                        clearTimeout(globalPromptsSaveTimeout);
                        globalPromptsSaveTimeout = null;
                    }
                    
                    // Clear any pending filename timeout to prevent stale rename operations
                    if (globalPromptsFilenameTimeout) {
                        clearTimeout(globalPromptsFilenameTimeout);
                        globalPromptsFilenameTimeout = null;
                    }
                    
                    // Update handlers with new filename to allow further saves/renames
                    const contentTextarea = document.getElementById('global-prompts-content');
                    if (contentTextarea) {
                        // Update globalPromptsContent to match current textarea value
                        // (in case user continued typing during the rename operation)
                        globalPromptsContent = contentTextarea.value;
                        contentTextarea.oninput = debounceGlobalPromptsSave(configDir, newFilename);
                    }
                    const filenameInput = document.getElementById('global-prompts-filename');
                    if (filenameInput) {
                        filenameInput.oninput = debounceGlobalPromptsFilename(configDir, newFilename);
                    }

                    // Refresh the prompt list dropdown
                    loadGlobalPromptsList().then(() => {
                        document.getElementById('global-prompts-select').value = newFilename;
                    });
                    if (statusEl) statusEl.textContent = 'Renamed';
                    setTimeout(() => { if (statusEl) statusEl.textContent = ''; }, 2000);
                } else {
                    const errorData = await response.json().catch(() => ({}));
                    throw new Error(errorData.error || 'Rename failed');
                }
            } catch (error) {
                console.error('Error renaming prompt:', error);
                if (statusEl) statusEl.textContent = `Error: ${error.message}`;
            }
        }, 1000);
    };
}

// Create new global prompt
async function createGlobalPrompt() {
    if (!globalPromptsConfigDir) {
        alert('Please select a config directory first');
        return;
    }

    // Find a unique filename
    let filename = 'Untitled.md';
    let counter = 1;
    try {
        // Load existing prompts to check for conflicts
        const response = await fetch(`/admin/api/prompts?config_dir=${encodeURIComponent(globalPromptsConfigDir)}`);
        const data = await response.json();
        const existingFilenames = new Set(data.prompts.map(prompt => prompt.filename));
        
        while (existingFilenames.has(filename)) {
            filename = `Untitled-${counter}.md`;
            counter++;
        }
    } catch (error) {
        console.error('Error checking existing prompts:', error);
        // Continue with Untitled.md, will fail if it exists
    }

    try {
        const response = await fetch(`/admin/api/prompts/${encodeURIComponent(filename)}?config_dir=${encodeURIComponent(globalPromptsConfigDir)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: '' })
        });

        if (response.ok) {
            loadGlobalPromptsList().then(() => {
                document.getElementById('global-prompts-select').value = filename;
            });
            loadGlobalPrompt(filename);
        } else {
            throw new Error('Create failed');
        }
    } catch (error) {
        console.error('Error creating prompt:', error);
        alert('Error creating role prompt');
    }
}

// Delete global prompt
async function deleteGlobalPrompt() {
    if (!globalPromptsCurrentFilename || !confirm(`Delete ${globalPromptsCurrentFilename}?`)) {
        return;
    }
    
    // Clear any pending save timeout to prevent saving after deletion
    if (globalPromptsSaveTimeout) {
        clearTimeout(globalPromptsSaveTimeout);
        globalPromptsSaveTimeout = null;
    }
    
    try {
        const response = await fetch(`/admin/api/prompts/${encodeURIComponent(globalPromptsCurrentFilename)}?config_dir=${encodeURIComponent(globalPromptsConfigDir)}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            loadGlobalPromptsList();
            document.getElementById('global-prompts-select').value = '';
            document.getElementById('global-prompts-editor-container').innerHTML = '<div class="loading">Select a role prompt to edit</div>';
            document.getElementById('delete-global-prompt-btn').style.display = 'none';
            globalPromptsCurrentFilename = '';
        } else {
            throw new Error('Delete failed');
        }
    } catch (error) {
        console.error('Error deleting prompt:', error);
        alert('Error deleting role prompt');
    }
}

// Move global prompt
async function moveGlobalPrompt() {
    if (!globalPromptsCurrentFilename) return;

    const destinationValue = document.getElementById('global-prompts-move-destination').value;
    if (!destinationValue) {
        return;
    }

    const toConfigDir = destinationValue;

    // Check if it's the current location
    if (toConfigDir === globalPromptsConfigDir) {
        return; // Already at this location
    }

    if (!toConfigDir) {
        alert('Invalid destination');
        return;
    }

    if (!confirm(`Move ${globalPromptsCurrentFilename} to ${toConfigDir}?`)) {
        // Reset dropdown to current location
        loadConfigDirectoriesForMovePrompts('global-prompts-move-destination', globalPromptsConfigDir);
        return;
    }

    // Save any pending content changes before moving
    try {
        await savePendingGlobalPromptsContent(globalPromptsConfigDir, globalPromptsCurrentFilename);
    } catch (error) {
        // If save fails, still allow move (but user may lose unsaved changes)
        console.error('Failed to save content before move:', error);
        alert('Warning: Failed to save content before move. Your recent edits may be lost.');
    }

    // Clear any pending save timeout to prevent saving after move
    if (globalPromptsSaveTimeout) {
        clearTimeout(globalPromptsSaveTimeout);
        globalPromptsSaveTimeout = null;
    }

    try {
        const response = await fetch(`/admin/api/prompts/${encodeURIComponent(globalPromptsCurrentFilename)}/move?from_config_dir=${encodeURIComponent(globalPromptsConfigDir)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                to_config_dir: toConfigDir
            })
        });

        if (response.ok) {
            loadGlobalPromptsList();
            document.getElementById('global-prompts-select').value = '';
            document.getElementById('global-prompts-editor-container').innerHTML = '<div class="loading">Select a role prompt to edit</div>';
            document.getElementById('delete-global-prompt-btn').style.display = 'none';
            globalPromptsCurrentFilename = '';
            alert('Role prompt moved successfully');
        } else {
            const errorData = await response.json().catch(() => ({}));
            const errorMessage = errorData.error || 'Move failed';
            if (errorMessage.includes('already exists')) {
                alert(`Cannot move: A role prompt with that name already exists in the destination directory.`);
            } else {
                alert(`Error moving role prompt: ${errorMessage}`);
            }
            // Reset dropdown to current location
            loadConfigDirectoriesForMovePrompts('global-prompts-move-destination', globalPromptsConfigDir);
        }
    } catch (error) {
        console.error('Error moving prompt:', error);
        alert(`Error moving role prompt: ${error.message}`);
        // Reset dropdown to current location
        loadConfigDirectoriesForMovePrompts('global-prompts-move-destination', globalPromptsConfigDir);
    }
}

// Load config directories for move (prompts only, simpler than docs)
async function loadConfigDirectoriesForMovePrompts(selectId, currentConfigDir) {
    try {
        const response = await fetch('/admin/api/config-directories');
        const data = await response.json();
        
        const select = document.getElementById(selectId);
        if (!select) return;
        
        select.innerHTML = '<option value="">Move to...</option>';
        data.directories.forEach(dir => {
            const option = document.createElement('option');
            option.value = dir.path;
            option.textContent = dir.display_path;
            // Mark current location
            if (currentConfigDir === dir.path) {
                option.selected = true;
                option.disabled = true;
                option.textContent += ' (current)';
            }
            select.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading config directories:', error);
    }
}

// Global parameters functions
async function loadGlobalParameters() {
    const container = document.getElementById('global-parameters-container');
    showLoading(container, 'Loading parameters...');
    
    try {
        const response = await fetchWithAuth('/admin/api/global-parameters');
        const data = await response.json();
        
        if (data.error) {
            showError(container, data.error);
            return;
        }
        
        const parameters = data.parameters || [];
        const availableLLMs = data.available_llms || [];
        
        container.innerHTML = `
            <div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <div style="margin-bottom: 16px;">
                    <h3 style="margin: 0;">Global Parameters</h3>
                </div>
                <div class="agent-param-grid">
                    ${parameters.map(param => {
                        const inputType = (param.type === 'float' || param.type === 'int') ? 'number' : 'text';
                        const stepAttr = param.type === 'float' ? 'step="0.1"' : (param.type === 'int' ? 'step="1"' : '');
                        
                        // Special handling for LLM model parameters - use combobox (datalist + input)
                        const llmModelParams = ['DEFAULT_AGENT_LLM', 'MEDIA_MODEL', 'TRANSLATION_MODEL'];
                        if (llmModelParams.includes(param.name)) {
                            return `
                                <div class="agent-param-section">
                                    <h3>${escapeHtml(param.name)}</h3>
                                    <input 
                                        id="global-param-${escapeHtml(param.name)}" 
                                        type="text" 
                                        class="agent-param-input" 
                                        value="${escapeHtml(param.value)}"
                                        placeholder="Type or select an LLM model...">
                                    <div style="font-size: 12px; color: #666; margin-top: 4px;">
                                        ${escapeHtml(param.comment || '')}
                                        ${param.default ? ` (Default: ${escapeHtml(param.default)})` : ''}
                                    </div>
                                    <div id="global-param-status-${escapeHtml(param.name)}" style="margin-top: 4px; font-size: 12px; color: #28a745;"></div>
                                </div>
                            `;
                        }
                        
                        // Regular input for other parameters
                        return `
                            <div class="agent-param-section">
                                <h3>${escapeHtml(param.name)}</h3>
                                <input 
                                    id="global-param-${escapeHtml(param.name)}" 
                                    type="${inputType}" 
                                    class="agent-param-input" 
                                    value="${escapeHtml(param.value)}"
                                    ${stepAttr}
                                    onchange="updateGlobalParameter('${escJsAttr(param.name)}', this.value)">
                                <div style="font-size: 12px; color: #666; margin-top: 4px;">
                                    ${escapeHtml(param.comment || '')}
                                    ${param.default ? ` (Default: ${escapeHtml(param.default)})` : ''}
                                </div>
                                <div id="global-param-status-${escapeHtml(param.name)}" style="margin-top: 4px; font-size: 12px; color: #28a745;"></div>
                            </div>
                        `;
                    }).join('')}
                </div>
            </div>
        `;

        const llmModelParams = ['DEFAULT_AGENT_LLM', 'MEDIA_MODEL', 'TRANSLATION_MODEL'];
        llmModelParams.forEach(paramName => {
            const inputEl = document.getElementById(`global-param-${paramName}`);
            setupLLMCombobox(inputEl, availableLLMs, {
                onChange: (value) => updateGlobalParameter(paramName, value),
            });
        });
    } catch (error) {
        console.error('Error loading global parameters:', error);
        container.innerHTML = `<div class="error">Error loading parameters: ${escapeHtml(error.message || error)}</div>`;
    }
}

async function updateGlobalParameter(parameterName, value) {
    const statusEl = document.getElementById(`global-param-status-${parameterName}`);
    if (statusEl) {
        statusEl.textContent = 'Saving...';
        statusEl.style.color = '#666';
    }
    
    try {
        const response = await fetchWithAuth('/admin/api/global-parameters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: parameterName,
                value: value
            })
        });
        
        const data = await response.json();
        
        if (data.error) {
            if (statusEl) {
                statusEl.textContent = `Error: ${data.error}`;
                statusEl.style.color = '#dc3545';
            }
            alert(`Error updating ${parameterName}: ${data.error}`);
        } else {
            if (statusEl) {
                statusEl.textContent = 'Saved';
                statusEl.style.color = '#28a745';
            }
        }
    } catch (error) {
        console.error(`Error updating ${parameterName}:`, error);
        if (statusEl) {
            statusEl.textContent = `Error: ${error.message || error}`;
            statusEl.style.color = '#dc3545';
        }
        alert(`Error updating ${parameterName}: ${error.message || error}`);
    }
}

// Global LLMs functions
let draggedLLMId = null;
let openrouterModelsCache = null;

async function loadGlobalLLMs() {
    const container = document.getElementById('global-llms-container');
    showLoading(container, 'Loading LLMs...');
    
    try {
        const response = await fetchWithAuth('/admin/api/global/llms');
        const data = await response.json();
        
        if (data.error) {
            showError(container, data.error);
            return;
        }
        
        const llms = data.llms || [];
        
        container.innerHTML = `
            <div style="background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                    <h3 style="margin: 0;">Available LLMs</h3>
                    <div>
                        <select id="add-llm-select" style="padding: 6px 12px; font-size: 14px; border: 1px solid #ddd; border-radius: 4px; margin-right: 8px;">
                            <option value="">Add LLM...</option>
                        </select>
                        <button onclick="addLLMFromSelect()" style="padding: 6px 12px; font-size: 14px; background: #28a745; color: white; border: none; border-radius: 4px; cursor: pointer;">
                            Add
                        </button>
                    </div>
                </div>
                <div id="llms-list" style="display: flex; flex-direction: column; gap: 12px;">
                    ${llms.map(llm => renderLLMItem(llm)).join('')}
                </div>
            </div>
        `;
        
        // Load OpenRouter models for the add pulldown (after DOM is created)
        loadOpenRouterModelsForAdd();
        
        // Initialize drag and drop
        initializeLLMDragAndDrop();
    } catch (error) {
        console.error('Error loading LLMs:', error);
        container.innerHTML = `<div class="error">Error loading LLMs: ${escapeHtml(error.message || error)}</div>`;
    }
}

function renderLLMItem(llm) {
    const promptPrice = llm.prompt_price ? parseFloat(llm.prompt_price).toFixed(2) : '0.00';
    const completionPrice = llm.completion_price ? parseFloat(llm.completion_price).toFixed(2) : '0.00';
    const description = llm.description || '';
    
    return `
        <div class="llm-item" data-llm-id="${llm.id}" draggable="true" style="padding: 12px; border: 1px solid #ddd; border-radius: 8px; background: #f9f9f9; cursor: move;">
            <div style="flex: 1;">
                <!-- First line: Model ID and Prices -->
                <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 8px;">
                    <label style="font-size: 12px; color: #666; white-space: nowrap; margin-right: 8px;">Model ID:</label>
                        <div style="flex: 1;">
                            <input 
                                type="text" 
                                class="llm-field" 
                                data-field="model_id"
                                data-llm-id="${llm.id}"
                                value="${escapeHtml(llm.model_id)}"
                                style="width: 100%; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;"
                                onblur="updateLLMField(${llm.id}, 'model_id', this.value)">
                        </div>
                        <div style="display: flex; align-items: center; gap: 6px; margin-left: 12px;">
                            <label style="font-size: 12px; color: #666; white-space: nowrap; margin-left: 8px;">Prices:</label>
                            <input 
                                type="number" 
                                step="0.01"
                                class="llm-field" 
                                data-field="prompt_price"
                                data-llm-id="${llm.id}"
                                value="${promptPrice}"
                                placeholder="0.00"
                                style="width: 60px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; text-align: right;"
                                onblur="this.value = parseFloat(this.value || 0).toFixed(2); updateLLMField(${llm.id}, 'prompt_price', parseFloat(this.value) || 0)">
                            <span style="color: #666;">/</span>
                            <input 
                                type="number" 
                                step="0.01"
                                class="llm-field" 
                                data-field="completion_price"
                                data-llm-id="${llm.id}"
                                value="${completionPrice}"
                                placeholder="0.00"
                                style="width: 60px; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; text-align: right;"
                                onblur="this.value = parseFloat(this.value || 0).toFixed(2); updateLLMField(${llm.id}, 'completion_price', parseFloat(this.value) || 0)">
                        </div>
                        <div>
                            <button onclick="deleteLLM(${llm.id})" style="padding: 6px 12px; background: #dc3545; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px; white-space: nowrap;">
                                Delete
                            </button>
                        </div>
                    </div>
                <!-- Second line: Name -->
                <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 8px;">
                    <label style="font-size: 12px; color: #666; white-space: nowrap;">Name:</label>
                    <input 
                        type="text" 
                        class="llm-field" 
                        data-field="name"
                        data-llm-id="${llm.id}"
                        value="${escapeHtml(llm.name)}"
                        style="flex: 1; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px;"
                        onblur="updateLLMField(${llm.id}, 'name', this.value)">
                </div>
                <!-- Third line: Description -->
                <div>
                    <label style="display: block; font-size: 12px; color: #666; margin-bottom: 4px;">Description:</label>
                    <textarea 
                        class="llm-field" 
                        data-field="description"
                        data-llm-id="${llm.id}"
                        style="width: 100%; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; min-height: 50px; resize: vertical;"
                        onblur="updateLLMField(${llm.id}, 'description', this.value)">${escapeHtml(description)}</textarea>
                </div>
            </div>
        </div>
    `;
}

function initializeLLMDragAndDrop() {
    const llmsList = document.getElementById('llms-list');
    if (!llmsList) return;
    
    let draggedElement = null;
    
    const items = llmsList.querySelectorAll('.llm-item');
    items.forEach(item => {
        item.addEventListener('dragstart', (e) => {
            draggedElement = item;
            item.style.opacity = '0.5';
            item.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
        });
        
        item.addEventListener('dragend', (e) => {
            item.style.opacity = '1';
            item.classList.remove('dragging');
            // Remove any drag-over classes
            items.forEach(i => i.classList.remove('drag-over'));
            draggedElement = null;
        });
        
        item.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            
            if (draggedElement && draggedElement !== item) {
                const afterElement = getDragAfterElement(llmsList, e.clientY);
                const dragging = draggedElement;
                if (afterElement == null) {
                    llmsList.appendChild(dragging);
                } else {
                    llmsList.insertBefore(dragging, afterElement);
                }
            }
        });
        
        item.addEventListener('dragenter', (e) => {
            e.preventDefault();
            if (draggedElement && draggedElement !== item) {
                item.classList.add('drag-over');
            }
        });
        
        item.addEventListener('dragleave', (e) => {
            item.classList.remove('drag-over');
        });
        
        item.addEventListener('drop', async (e) => {
            e.preventDefault();
            item.classList.remove('drag-over');
            if (draggedElement) {
                await saveLLMOrder();
            }
        });
    });
}

function getDragAfterElement(container, y) {
    const draggableElements = [...container.querySelectorAll('.llm-item:not(.dragging)')];
    
    return draggableElements.reduce((closest, child) => {
        const box = child.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        
        if (offset < 0 && offset > closest.offset) {
            return { offset: offset, element: child };
        } else {
            return closest;
        }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
}

async function saveLLMOrder() {
    const llmsList = document.getElementById('llms-list');
    if (!llmsList) return;
    
    const items = llmsList.querySelectorAll('.llm-item');
    const orderMapping = {};
    items.forEach((item, index) => {
        const llmId = parseInt(item.dataset.llmId);
        orderMapping[llmId] = index;
    });
    
    try {
        const response = await fetchWithAuth('/admin/api/global/llms/reorder', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order: orderMapping })
        });
        
        const data = await response.json();
        if (data.error) {
            console.error('Error reordering LLMs:', data.error);
            alert(`Error reordering LLMs: ${data.error}`);
            // Reload to restore correct order
            await loadGlobalLLMs();
        }
    } catch (error) {
        console.error('Error reordering LLMs:', error);
        alert(`Error reordering LLMs: ${error.message || error}`);
        // Reload to restore correct order
        await loadGlobalLLMs();
    }
}

async function updateLLMField(llmId, field, value) {
    try {
        const updateData = { [field]: value };
        
        // If model_id is being updated and contains "/", trigger OpenRouter validation
        if (field === 'model_id' && value.includes('/')) {
            // This will be handled by the backend
        }
        
        const response = await fetchWithAuth(`/admin/api/global/llms/${llmId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateData)
        });
        
        const data = await response.json();
        if (data.error) {
            alert(`Error updating ${field}: ${data.error}`);
            // Reload to restore correct values
            await loadGlobalLLMs();
        } else {
            // Update the displayed values if the response includes updated data
            if (data.model_id !== undefined) {
                const item = document.querySelector(`.llm-item[data-llm-id="${llmId}"]`);
                if (item) {
                    // Update fields if they changed (e.g., auto-filled from OpenRouter)
                    if (data.name !== undefined) {
                        const nameInput = item.querySelector(`input[data-field="name"]`);
                        if (nameInput) nameInput.value = data.name;
                    }
                    if (data.description !== undefined) {
                        const descTextarea = item.querySelector(`textarea[data-field="description"]`);
                        if (descTextarea) {
                            descTextarea.value = data.description || '';
                            autoGrowTextarea(descTextarea);
                        }
                    }
                    if (data.prompt_price !== undefined) {
                        const promptInput = item.querySelector(`input[data-field="prompt_price"]`);
                        if (promptInput) promptInput.value = parseFloat(data.prompt_price).toFixed(2);
                    }
                    if (data.completion_price !== undefined) {
                        const completionInput = item.querySelector(`input[data-field="completion_price"]`);
                        if (completionInput) completionInput.value = parseFloat(data.completion_price).toFixed(2);
                    }
                }
            }
        }
    } catch (error) {
        console.error(`Error updating LLM field ${field}:`, error);
        alert(`Error updating ${field}: ${error.message || error}`);
        await loadGlobalLLMs();
    }
}

async function deleteLLM(llmId) {
    if (!confirm('Are you sure you want to delete this LLM?')) {
        return;
    }
    
    try {
        const response = await fetchWithAuth(`/admin/api/global/llms/${llmId}`, {
            method: 'DELETE'
        });
        
        const data = await response.json();
        if (data.error) {
            alert(`Error deleting LLM: ${data.error}`);
        } else {
            await loadGlobalLLMs();
        }
    } catch (error) {
        console.error('Error deleting LLM:', error);
        alert(`Error deleting LLM: ${error.message || error}`);
    }
}

async function loadOpenRouterModelsForAdd() {
    const select = document.getElementById('add-llm-select');
    if (!select) return;
    
    // Clear existing options except the first one
    while (select.children.length > 1) {
        select.removeChild(select.lastChild);
    }
    
    // Add "Custom" option first
    const customOption = document.createElement('option');
    customOption.value = '__custom__';
    customOption.textContent = 'Custom...';
    select.appendChild(customOption);
    
    try {
        // Get both OpenRouter models and existing LLMs in parallel
        const [openrouterResponse, existingLLMsResponse] = await Promise.all([
            fetchWithAuth('/admin/api/global/llms/openrouter-models'),
            fetchWithAuth('/admin/api/global/llms')
        ]);
        
        const openrouterData = await openrouterResponse.json();
        const existingLLMsData = await existingLLMsResponse.json();
        
        if (openrouterData.error) {
            console.error('Error loading OpenRouter models:', openrouterData.error);
            return;
        }
        
        // Build set of existing model IDs for quick lookup
        const existingModelIds = new Set();
        if (existingLLMsData.llms) {
            existingLLMsData.llms.forEach(llm => {
                existingModelIds.add(llm.model_id);
            });
        }
        
        const models = openrouterData.models || [];
        // Filter out models that already exist
        const availableModels = models.filter(model => !existingModelIds.has(model.value));
        
        availableModels.forEach(model => {
            const option = document.createElement('option');
            option.value = model.value;
            option.textContent = model.label;
            select.appendChild(option);
        });
                        
        openrouterModelsCache = models; // Keep full cache for reference
    } catch (error) {
        console.error('Error loading OpenRouter models:', error);
    }
}

async function addLLMFromSelect() {
    const select = document.getElementById('add-llm-select');
    if (!select || !select.value) {
        alert('Please select an LLM to add');
        return;
    }
    
    const selectedValue = select.value;
    
    if (selectedValue === '__custom__') {
        // Add custom LLM (modeless entry with defaults)
        const modelId = `new-llm-${Date.now()}`;
        const name = 'New LLM name';
        try {
            const response = await fetchWithAuth('/admin/api/global/llms', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_id: modelId,
                    name: name,
                    description: null,
                    prompt_price: 0.0,
                    completion_price: 0.0,
                    provider: 'custom',
                    display_order: 0
                })
            });
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                alert(`Error adding LLM: ${errorData.error || `HTTP error! status: ${response.status}`}`);
                return;
            }
            
            const data = await response.json();
            if (data.error) {
                alert(`Error adding LLM: ${data.error}`);
            } else {
                select.value = '';
                await loadGlobalLLMs();
            }
        } catch (error) {
            console.error('Error adding custom LLM:', error);
            alert(`Error adding LLM: ${error.message || error}`);
        }
    } else {
        // Add from OpenRouter
        const selectedModel = openrouterModelsCache?.find(m => m.value === selectedValue);
        if (!selectedModel) {
            alert('Selected model not found');
            return;
        }
        
        // Parse pricing from label
        const priceMatch = selectedModel.label.match(/\$([\d.]+)\s*\/\s*\$([\d.]+)/);
        const promptPrice = priceMatch ? parseFloat(priceMatch[1]) : 0.0;
        const completionPrice = priceMatch ? parseFloat(priceMatch[2]) : 0.0;
        
        // Extract name (remove pricing)
        const name = selectedModel.label.replace(/\s*\(\$[\d.]+\s*\/\s*\$[\d.]+\)\s*$/, '').trim();
        
        // Use description from OpenRouter API, fallback to generic text
        const description = selectedModel.description || 'Model via OpenRouter';
        
        try {
            const response = await fetchWithAuth('/admin/api/global/llms', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_id: selectedValue,
                    name: name,
                    description: description,
                    prompt_price: promptPrice,
                    completion_price: completionPrice,
                    provider: 'openrouter'
                })
            });
            
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ error: `HTTP error! status: ${response.status}` }));
                alert(`Error adding LLM: ${errorData.error || `HTTP error! status: ${response.status}`}`);
                return;
            }
            
            const data = await response.json();
            if (data.error) {
                alert(`Error adding LLM: ${data.error}`);
            } else {
                select.value = '';
                await loadGlobalLLMs();
            }
        } catch (error) {
            console.error('Error adding LLM:', error);
            alert(`Error adding LLM: ${error.message || error}`);
        }
    }
}

async function loadGlobalCosts() {
    const container = document.getElementById('global-costs-container');
    if (!container) return;

    showLoading(container, 'Loading costs...');
    try {
        const response = await fetchWithAuth('/admin/api/global/costs');
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
                <h3 style="margin-top: 0;">Global Costs (Last ${days} Days)</h3>
                <div style="font-size: 20px; font-weight: 600; margin-bottom: 12px;">Total: $${totalCost.toFixed(4)}</div>
        `;

        if (logs.length === 0) {
            html += '<div class="placeholder-card">No cost logs found for this period.</div>';
        } else {
            html += '<div style="overflow-x: auto;"><table style="width: 100%; border-collapse: collapse;">';
            html += '<thead><tr style="border-bottom: 1px solid #ddd; text-align: left;">';
            html += '<th style="padding: 8px;">Timestamp</th>';
            html += '<th style="padding: 8px;">Agent</th>';
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
                    <td style="padding: 8px;">${escapeHtml(String(log.agent_telegram_id || ''))}</td>
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


